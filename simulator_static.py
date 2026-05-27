from __future__ import annotations

from bisect import bisect_right
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
import argparse
import csv
import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import parse_qs

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel


PID_FILE = "simulator_static.pid"
LOG_FILE = "simulator_static.log"
DATASET_FILE = "artificial_week_sensor_dataset.csv"
LOOP_DATASET_SECONDS = 7 * 24 * 60 * 60
PORT = 4649


app = FastAPI(title="Static Sensor Dataset Simulator", version="1.0.0")


@app.middleware("http")
async def log_raw_request(request: Request, call_next):
    body = await request.body()
    raw_text = body.decode("utf-8", errors="replace")
    print(
        "[simulator_static] raw request",
        {
            "method": request.method,
            "path": request.url.path,
            "content_type": request.headers.get("content-type"),
            "body": raw_text,
        },
    )
    logger.info(
        "raw request method=%s path=%s content_type=%s body=%s",
        request.method,
        request.url.path,
        request.headers.get("content-type"),
        raw_text,
    )
    response = await call_next(request)
    print(f"[simulator_static] response status={response.status_code} path={request.url.path}")
    return response


logger = logging.getLogger("simulator_static")
if not logger.handlers:
    logger.setLevel(logging.INFO)
    log_path = os.path.join(os.path.dirname(__file__), LOG_FILE)
    file_handler = logging.FileHandler(log_path)
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(file_handler)
    logger.propagate = False


class ReadSensorRequest(BaseModel):
    data: Optional[Any] = None


class DatasetSimulatorState:
    def __init__(self, dataset_path: Path) -> None:
        self.dataset_path = dataset_path
        self.rows: list[dict[str, Any]] = []
        self.row_seconds: list[float] = []
        self.started_at = datetime.now()
        self.last_mapped_second: float | None = None
        self.current_lumen = 0
        self._load_dataset()

    def _load_dataset(self) -> None:
        with self.dataset_path.open(newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            raw_rows = list(reader)

        parsed_rows: list[dict[str, Any]] = []
        for row in raw_rows:
            timestamp = datetime.fromisoformat(row["timestamp"])
            parsed_rows.append(
                {
                    "timestamp": timestamp,
                    "ambient_light_lux": float(row["ambient_light_lux"]),
                    "occupancy_count": int(row["occupancy_count"]),
                    "motion_detected": row["motion_detected"].strip().upper() == "TRUE",
                    "hour": float(row["hour"]),
                    "gt_target": int(row["GT - Target"]),
                    "user_input": row["User Input"],
                    "trigger": row.get("trigger", "scheduled_30m"),
                }
            )

        parsed_rows.sort(key=lambda x: x["timestamp"])

        dataset_start = parsed_rows[0]["timestamp"]
        self.rows = parsed_rows
        self.row_seconds = [
            (row["timestamp"] - dataset_start).total_seconds() % LOOP_DATASET_SECONDS
            for row in parsed_rows
        ]

    def _mapped_dataset_second(self, now: datetime) -> float:
        elapsed_real_seconds = (now - self.started_at).total_seconds()
        # 6 real seconds map to 1 dataset hour.
        dataset_elapsed_seconds = elapsed_real_seconds * 600.0
        return dataset_elapsed_seconds % LOOP_DATASET_SECONDS

    def _row_at(self, mapped_second: float) -> dict[str, Any]:
        idx = bisect_right(self.row_seconds, mapped_second) - 1
        if idx < 0:
            idx = len(self.rows) - 1
        return self.rows[idx]

    def _rows_between(self, previous: float | None, current: float) -> list[dict[str, Any]]:
        if previous is None:
            return []

        def in_window(value: float) -> bool:
            if current >= previous:
                return previous < value <= current
            return value > previous or value <= current

        rows_in_window: list[dict[str, Any]] = []
        for sec, row in zip(self.row_seconds, self.rows):
            if in_window(sec):
                rows_in_window.append(row)
        return rows_in_window

    def mapped_second_now(self) -> float:
        return self._mapped_dataset_second(datetime.now())

    def next_event_after(self, mapped_second: float) -> tuple[dict[str, Any], float]:
        if not self.rows:
            raise RuntimeError("Dataset is empty.")

        for sec, row in zip(self.row_seconds, self.rows):
            if sec > mapped_second:
                return row, sec

        # Loop around to the first row in the dataset week.
        first_sec, first_row = self.row_seconds[0], self.rows[0]
        return first_row, first_sec

    def real_seconds_until(self, mapped_second: float, target_dataset_second: float) -> float:
        if target_dataset_second >= mapped_second:
            dataset_delta = target_dataset_second - mapped_second
        else:
            dataset_delta = (LOOP_DATASET_SECONDS - mapped_second) + target_dataset_second

        # 6 real seconds map to 1 dataset hour => dataset runs 600x faster than wall clock.
        return dataset_delta / 600.0

    def payload_for_row(self, row: dict[str, Any], actual_now: datetime) -> dict[str, Any]:
        return {
            "dataset_timestamp": row["timestamp"].isoformat(),
            "actual_timestamp": actual_now.isoformat(),
            "trigger": row["trigger"],
            "ambient_light_lux": row["ambient_light_lux"],
            "occupancy_count": row["occupancy_count"],
            "motion_detected": row["motion_detected"],
            "hour": row["hour"],
            "current_light_lumen": self.current_lumen,
            "gt_target_lumen": row["gt_target"],
            "user_input": row["user_input"],
        }

    def read_current(self) -> dict[str, Any]:
        now = datetime.now()
        mapped_second = self._mapped_dataset_second(now)
        row = self._row_at(mapped_second)
        rows_since_last_read = self._rows_between(self.last_mapped_second, mapped_second)
        self.last_mapped_second = mapped_second

        callbacks = [
            {
                "dataset_timestamp": e["timestamp"].isoformat(),
                "trigger": e["trigger"],
                "user_input": e["user_input"],
                "message": "dataset event triggered",
            }
            for e in rows_since_last_read
        ]

        callback = callbacks[0] if callbacks else None

        payload = {
            "dataset_timestamp": row["timestamp"].isoformat(),
            "actual_timestamp": now.isoformat(),
            "trigger": row["trigger"],
            "ambient_light_lux": row["ambient_light_lux"],
            "occupancy_count": row["occupancy_count"],
            "motion_detected": row["motion_detected"],
            "hour": row["hour"],
            "current_light_lumen": self.current_lumen,
            "gt_target_lumen": row["gt_target"],
            "user_input": row["user_input"],
        }
        logger.info("readsensor response=%s", payload)
        return payload


def _send_delayed_callback(
    callback_url: str,
    callback_id: str | None,
    event_row: dict[str, Any],
    delay_seconds: float,
) -> None:
    print(
        "[simulator_static] scheduling callback",
        {
            "callback_url": callback_url,
            "callback_id": callback_id,
            "delay_seconds": round(delay_seconds, 3),
            "event_dataset_timestamp": event_row["timestamp"].isoformat(),
            "trigger": event_row["trigger"],
        },
    )

    def _worker() -> None:
        if delay_seconds > 0:
            print(f"[simulator_static] waiting {delay_seconds:.3f}s before callback PUT")
            time.sleep(delay_seconds)

        payload = state.payload_for_row(event_row, datetime.now())
        payload["message"] = "dataset event reached"

        body = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
        }
        if callback_id:
            headers["CPEE-CALLBACK-ID"] = callback_id
        headers["CPEE-UPDATE"] = "false"

        req = urllib.request.Request(
            callback_url,
            data=body,
            headers=headers,
            method="PUT",
        )

        try:
            print(
                "[simulator_static] sending callback PUT",
                {
                    "callback_url": callback_url,
                    "callback_id": callback_id,
                    "payload": payload,
                },
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                print(f"[simulator_static] callback PUT success status={resp.status}")
                logger.info(
                    "Delivered callback to %s status=%s payload=%s",
                    callback_url,
                    resp.status,
                    payload,
                )
        except urllib.error.URLError as exc:
            print(f"[simulator_static] callback PUT failed error={exc}")
            logger.error("Failed to deliver callback to %s error=%s", callback_url, exc)

    threading.Thread(target=_worker, daemon=True).start()


state = DatasetSimulatorState(Path(__file__).with_name(DATASET_FILE))


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
    }


@app.get("/readsensor_callback", response_model=None)
def readsensor_callback(request: Request) -> JSONResponse:
    callback_url = request.headers.get("cpee-callback")
    callback_id = request.headers.get("cpee-callback-id")

    if callback_url:
        print(
            "[simulator_static] async readsensor request",
            {
                "callback_url": callback_url,
                "callback_id": callback_id,
                "instance": request.headers.get("cpee-instance"),
                "activity": request.headers.get("cpee-activity"),
                "label": request.headers.get("cpee-label"),
            },
        )
        mapped_second = state.mapped_second_now()
        event_row, event_dataset_second = state.next_event_after(mapped_second)
        delay_seconds = state.real_seconds_until(mapped_second, event_dataset_second)

        _send_delayed_callback(
            callback_url=callback_url,
            callback_id=callback_id,
            event_row=event_row,
            delay_seconds=delay_seconds,
        )

        ack_payload = {
            "status": "acknowledged",
            "response": "Ack.: Response later",
            "callback_url": callback_url,
            "callback_id": callback_id,
            "next_event_dataset_timestamp": event_row["timestamp"].isoformat(),
            "seconds_until_callback": round(delay_seconds, 3),
        }
        print("[simulator_static] returning async ACK", ack_payload)
        response = JSONResponse(status_code=202, content=ack_payload)
        response.headers["CPEE-CALLBACK"] = "true"
        return response

    return JSONResponse(status_code=200, content=state.read_current())

@app.get("/readsensor", response_model=None)
def readsensor(request: Request) -> JSONResponse:
    return JSONResponse(status_code=200, content=state.read_current())

@app.get("/read_sensor", response_model=None)
def read_sensor_alias(request: Request) -> JSONResponse:
    return JSONResponse(content=readsensor(request))


@app.post("/read_sensor", response_model=None)
def read_sensor_alias_post(request: Request, payload: ReadSensorRequest | None = None) -> JSONResponse:
    return JSONResponse(content=readsensor(request))


@app.get("/sensor/all")
def sensor_all() -> JSONResponse:
    return JSONResponse(content=state.read_current())


@app.put("/changelumens")
async def change_lumens(request: Request, lumen: Optional[int] = None) -> JSONResponse:
    lumen_value: Optional[int] = lumen

    if lumen_value is None:
        raw_value: Any = None
        content_type = (request.headers.get("content-type") or "").split(";")[0].strip().lower()

        if content_type == "application/json":
            try:
                payload = await request.json()
            except Exception:
                payload = {}
            if isinstance(payload, dict):
                raw_value = payload.get("lumen")
        elif content_type == "application/x-www-form-urlencoded":
            body = await request.body()
            parsed = parse_qs(body.decode("utf-8", errors="replace"), keep_blank_values=True)
            values = parsed.get("lumen")
            if values:
                raw_value = values[0]

        if raw_value is None or str(raw_value).strip() == "":
            return JSONResponse(
                status_code=400,
                content={
                    "status": "error",
                    "message": "Missing 'lumen' value. Provide it via query, JSON, or form body.",
                },
            )

        try:
            lumen_value = int(str(raw_value).strip())
        except ValueError:
            return JSONResponse(
                status_code=400,
                content={
                    "status": "error",
                    "message": "Invalid 'lumen' value. Expected integer.",
                },
            )

    if lumen_value < 0 or lumen_value > 3000:
        return JSONResponse(
            status_code=400,
            content={
                "status": "error",
                "message": "'lumen' must be between 0 and 3000.",
            },
        )

    state.current_lumen = lumen_value
    return JSONResponse(content={
        "status": "applied",
        "applied_lumen": state.current_lumen,
    })


@app.put("/change_lumens")
async def change_lumens_alias(request: Request, lumen: Optional[int] = None) -> JSONResponse:
    return await change_lumens(request, lumen)


@app.get("/changelumens/state")
def lumen_state() -> JSONResponse:
    return JSONResponse(content={
        "current_lumen": state.current_lumen,
    })


def run_server() -> None:
    import uvicorn

    uvicorn.run("simulator_static:app", port=PORT, log_level="info")


def _read_pid(pid_file: str = PID_FILE) -> int | None:
    if not os.path.exists(pid_file):
        return None
    try:
        with open(pid_file, "r", encoding="utf-8") as f:
            return int(f.read().strip())
    except Exception:
        return None


def _is_running(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _start_daemon() -> None:
    existing_pid = _read_pid()
    if _is_running(existing_pid):
        print(f"Static simulator already running with PID {existing_pid}")
        return
    if existing_pid and os.path.exists(PID_FILE):
        os.remove(PID_FILE)

    log_path = os.path.join(os.path.dirname(__file__), LOG_FILE)
    with open(log_path, "a", encoding="utf-8") as log_file:
        proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "simulator_static:app",
                "--port",
                str(PORT),
                "--log-level",
                "info",
            ],
            cwd=os.path.dirname(__file__),
            stdout=log_file,
            stderr=log_file,
            start_new_session=True,
        )

    with open(PID_FILE, "w", encoding="utf-8") as f:
        f.write(str(proc.pid))
    print(f"Started static simulator daemon with PID {proc.pid}")


def _stop_daemon() -> None:
    pid = _read_pid()
    if not pid:
        print("No simulator_static.pid found. Static simulator is not running.")
        return
    if not _is_running(pid):
        print(f"Stale PID file found for PID {pid}. Removing it.")
        if os.path.exists(PID_FILE):
            os.remove(PID_FILE)
        return

    print(f"Stopping static simulator daemon PID {pid}")
    os.kill(pid, signal.SIGINT)
    if os.path.exists(PID_FILE):
        os.remove(PID_FILE)


def _status_daemon() -> None:
    pid = _read_pid()
    if _is_running(pid):
        print(f"Static simulator is running with PID {pid}")
    elif pid:
        print(f"Static simulator is not running (stale PID {pid})")
    else:
        print("Static simulator is not running")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Manage the static dataset simulator service")
    parser.add_argument("--stop", action="store_true", help="Stop the background static simulator daemon")
    parser.add_argument("--status", action="store_true", help="Show static simulator daemon status")
    parser.add_argument("--foreground", action="store_true", help="Run in foreground for debugging")
    args = parser.parse_args()

    if args.stop:
        _stop_daemon()
    elif args.status:
        _status_daemon()
    elif args.foreground:
        run_server()
    else:
        _start_daemon()
