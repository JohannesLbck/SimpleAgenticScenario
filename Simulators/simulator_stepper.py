from __future__ import annotations

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
from urllib.parse import parse_qs

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel


PID_FILE = "simulator_stepper.pid"
LOG_FILE = "simulator_stepper.log"
XES_YAML_LOG_FILE = "simulator_stepper.xes.yaml"
#DATASET_FILE = "artificial_week_sensor_dataset.csv"
DATASET_FILE = "artificial_week_sensor_dataset_no_user_input.csv"
LOOP_DATASET_SECONDS = 7 * 24 * 60 * 60
PORT = 4649


app = FastAPI(title="Stepper Sensor Dataset Simulator", version="1.0.0")


logger = logging.getLogger("simulator_stepper")
xes_log_lock = threading.Lock()
if not logger.handlers:
    logger.setLevel(logging.INFO)
    log_path = os.path.join(os.path.dirname(__file__), LOG_FILE)
    file_handler = logging.FileHandler(log_path)
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(file_handler)
    logger.propagate = False


def _parse_gt_target_range(value: Any) -> tuple[int, int]:
    raw = str(value).strip()
    if "-" in raw:
        left, right = raw.split("-", 1)
        low = int(left.strip())
        high = int(right.strip())
    else:
        low = int(raw)
        high = low

    low = max(0, low)
    high = max(0, high)
    if high < low:
        low, high = high, low
    return low, high


def _yaml_scalar(value: Any) -> str:
    text = str(value)
    escaped = text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f'"{escaped}"'


def _append_xes_yaml_event(event: dict[str, Any]) -> None:
    log_path = os.path.join(os.path.dirname(__file__), XES_YAML_LOG_FILE)
    lines = ["- event:"]
    for key, value in event.items():
        lines.append(f"    {key}: {_yaml_scalar(value)}")
    lines.append("")

    with xes_log_lock:
        with open(log_path, "a", encoding="utf-8") as log_file:
            log_file.write("\n".join(lines))


def _write_middleware_xes_event(
    *,
    request: Request,
    lifecycle: str,
    data: dict[str, Any],
) -> None:
    event = {
        "concept:name": request.url.path,
        "lifecycle:transition": lifecycle,
        "data": json.dumps(data, ensure_ascii=True, sort_keys=True),
        "time:timestamp": datetime.now().isoformat(),
    }
    _append_xes_yaml_event(event)


@app.middleware("http")
async def log_raw_request(request: Request, call_next):
    body = await request.body()
    raw_text = body.decode("utf-8", errors="replace")

    _write_middleware_xes_event(
        request=request,
        lifecycle="start",
        data={
            "kind": "request",
            "method": request.method,
            "path": request.url.path,
            "content_type": request.headers.get("content-type"),
            "body": raw_text,
        },
    )

    response = await call_next(request)

    _write_middleware_xes_event(
        request=request,
        lifecycle="complete",
        data={
            "kind": "response",
            "method": request.method,
            "path": request.url.path,
            "status_code": response.status_code,
        },
    )

    return response


class ReadSensorRequest(BaseModel):
    data: Optional[Any] = None


class DatasetSimulatorState:
    def __init__(self, dataset_path: Path) -> None:
        self.dataset_path = dataset_path
        self.rows: list[dict[str, Any]] = []
        self.current_lumen = 0
        self.current_index = 0
        self._load_dataset()

    def _load_dataset(self) -> None:
        with self.dataset_path.open(newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            raw_rows = list(reader)

        parsed_rows: list[dict[str, Any]] = []
        for row in raw_rows:
            timestamp = datetime.fromisoformat(row["timestamp"])
            gt_min, gt_max = _parse_gt_target_range(row["GT - Target"])
            parsed_rows.append(
                {
                    "timestamp": timestamp,
                    "ambient_light_lux": float(row["ambient_light_lux"]),
                    "occupancy_count": int(row["occupancy_count"]),
                    "motion_detected": row["motion_detected"].strip().upper() == "TRUE",
                    "hour": float(row["hour"]),
                    "gt_target_min": gt_min,
                    "gt_target_max": gt_max,
                    "gt_target_range": f"{gt_min}-{gt_max}",
                    #"user_input": row["User Input"],
                    "trigger": row.get("trigger", "scheduled_30m"),
                }
            )

        parsed_rows.sort(key=lambda x: x["timestamp"])

        self.rows = parsed_rows

    def read_current(self) -> dict[str, Any]:
        if not self.rows:
            raise RuntimeError("Dataset is empty.")

        now = datetime.now()
        row = self.rows[self.current_index]
        self.current_index = (self.current_index + 1) % len(self.rows)

        payload = {
            "dataset_timestamp": row["timestamp"].isoformat(),
            "actual_timestamp": now.isoformat(),
            "trigger": row["trigger"],
            "ambient_light_lux": row["ambient_light_lux"],
            "occupancy_count": row["occupancy_count"],
            "motion_detected": row["motion_detected"],
            "hour": row["hour"],
            "current_light_lumen": self.current_lumen,
            "gt_target_lumen": row["gt_target_range"],
            "gt_target_lumen_min": row["gt_target_min"],
            "gt_target_lumen_max": row["gt_target_max"],
            #"user_input": row["user_input"],
        }
        logger.info("readsensor response=%s", payload)
        return payload


state = DatasetSimulatorState(Path(__file__).with_name(DATASET_FILE))


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
    }

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

    if lumen_value < 0 or lumen_value > 5000:
        return JSONResponse(
            status_code=400,
            content={
                "status": "error",
                "message": "'lumen' must be between 0 and 5000.",
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

    uvicorn.run("simulator_stepper:app", port=PORT, log_level="info")


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
                "simulator_stepper:app",
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
        print("No simulator_stepper.pid found. Stepper simulator is not running.")
        return
    if not _is_running(pid):
        print(f"Stale PID file found for PID {pid}. Removing it.")
        if os.path.exists(PID_FILE):
            os.remove(PID_FILE)
        return

    print(f"Stopping stepper simulator daemon PID {pid}")
    os.kill(pid, signal.SIGINT)
    if os.path.exists(PID_FILE):
        os.remove(PID_FILE)


def _status_daemon() -> None:
    pid = _read_pid()
    if _is_running(pid):
        print(f"Stepper simulator is running with PID {pid}")
    elif pid:
        print(f"Stepper simulator is not running (stale PID {pid})")
    else:
        print("Stepper simulator is not running")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Manage the stepper dataset simulator service")
    parser.add_argument("--stop", action="store_true", help="Stop the background stepper simulator daemon")
    parser.add_argument("--status", action="store_true", help="Show stepper simulator daemon status")
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
