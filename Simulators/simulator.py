from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
import logging
import sys
import subprocess
import signal
import random
import os
import uvicorn
import argparse

from fastapi import FastAPI
from pydantic import BaseModel, Field


PID_FILE = "simulator.pid"
LOG_FILE = "simulator.log"

app = FastAPI(title="Sensor and Lumen Simulator", version="1.0.0")


logger = logging.getLogger("simulator")
if not logger.handlers:
    logger.setLevel(logging.INFO)
    log_path = os.path.join(os.path.dirname(__file__), LOG_FILE)
    file_handler = logging.FileHandler(log_path)
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(file_handler)
    logger.propagate = False


class SensorOverrides(BaseModel):
    ambient_light_lux: Optional[float] = Field(default=None, ge=0, le=200000)
    occupancy_count: Optional[int] = Field(default=None, ge=0, le=500)
    motion_detected: Optional[bool] = None
    screen_brightness_nits: Optional[int] = Field(default=None, ge=0, le=4000)
    weather_cloud_cover_pct: Optional[int] = Field(default=None, ge=0, le=100)


class LumenCommand(BaseModel):
    lumen: int = Field(ge=0, le=3000)
    reason: Optional[str] = None
    source: str = "llm-agent"


class ReadSensorRequest(BaseModel):
    data: Optional[Any] = None


class SimulatorState:
    def __init__(self) -> None:
        self.overrides = SensorOverrides()
        self.current_lumen = 0
        self.last_command_source = "none"


state = SimulatorState()


def _log_sensor_response(sensor_name: str, payload: dict[str, Any]) -> None:
    logger.info("sensor=%s response=%s", sensor_name, payload)


def infer_time_of_day(hour: int) -> str:
    if 6 <= hour < 11:
        return "morning"
    if 11 <= hour < 17:
        return "day"
    if 17 <= hour < 21:
        return "evening"
    return "night"


def _ambient_light_default(hour: int) -> float:
    # Simple daylight curve for deterministic simulation per hour.
    daylight_factor = max(0.0, 1.0 - abs(13 - hour) / 7)
    return 40 + daylight_factor * 650 + random.uniform(-20, 20)


def _occupancy_default(hour: int) -> int:
    if 8 <= hour <= 18:
        return random.choice([1, 1, 2, 2, 3])
    return random.choice([0, 0, 0, 1])


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/sensor/override")
def set_overrides(payload: SensorOverrides) -> dict:
    state.overrides = payload
    return {"status": "overrides-updated", "overrides": state.overrides.model_dump()}


@app.get("/sensor/ambient-light")
def ambient_light() -> dict:
    hour = datetime.now().hour
    lux = state.overrides.ambient_light_lux
    if lux is None:
        lux = round(_ambient_light_default(hour), 2)
    payload = {"ambient_light_lux": lux}
    _log_sensor_response("ambient-light", payload)
    return payload


@app.get("/sensor/occupancy")
def occupancy() -> dict:
    count = state.overrides.occupancy_count
    if count is None:
        count = _occupancy_default(datetime.now().hour)
    payload = {"occupancy_count": count}
    _log_sensor_response("occupancy", payload)
    return payload


@app.get("/sensor/motion")
def motion() -> dict:
    if state.overrides.motion_detected is not None:
        detected = state.overrides.motion_detected
    else:
        detected = random.choice([True, True, True, False])
    payload = {"motion_detected": detected}
    _log_sensor_response("motion", payload)
    return payload


@app.get("/sensor/context")
def context() -> dict:
    hour = datetime.now().hour
    tod = infer_time_of_day(hour)

    screen_nits = state.overrides.screen_brightness_nits
    if screen_nits is None:
        screen_nits = random.choice([120, 200, 300, 420])

    cloud_cover = state.overrides.weather_cloud_cover_pct
    if cloud_cover is None:
        cloud_cover = random.choice([10, 20, 35, 50, 75, 90])

    payload = {
        "hour": hour,
        "time_of_day": tod,
        "screen_brightness_nits": screen_nits,
        "weather_cloud_cover_pct": cloud_cover,
    }
    _log_sensor_response("context", payload)
    return payload


@app.get("/sensor/all")
def sensor_all() -> dict:
    data = {}
    data.update(ambient_light())
    data.update(occupancy())
    data.update(motion())
    data.update(context())
    logger.info("sensor=all response=%s", data)
    return data


@app.get("/readsensor")
def readsensor(_: ReadSensorRequest | None = None) -> dict:
    # Process-engine compatible endpoint: request body may contain a data argument.
    return sensor_all()


@app.get("/read_sensor")
def read_sensor_alias(payload: ReadSensorRequest | None = None) -> dict:
    return readsensor(payload)


@app.post("/changelumens")
def change_lumens(payload: LumenCommand) -> dict:
    state.current_lumen = payload.lumen
    state.last_command_source = payload.source
    return {
        "status": "applied",
        "applied_lumen": state.current_lumen,
        "reason": payload.reason,
        "source": state.last_command_source,
        "applied_at": datetime.now().isoformat(),
    }


@app.post("/change_lumens")
def change_lumens_alias(payload: LumenCommand) -> dict:
    return change_lumens(payload)


@app.get("/changelumens/state")
def lumen_state() -> dict:
    return {
        "current_lumen": state.current_lumen,
    }


def run_server():
    uvicorn.run("simulator:app", port=4648, log_level="info")



def _read_pid(pid_file=PID_FILE):
    if not os.path.exists(pid_file):
        return None
    try:
        with open(pid_file, "r") as f:
            return int(f.read().strip())
    except Exception:
        return None


def _is_running(pid):
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _start_daemon():
    existing_pid = _read_pid()
    if _is_running(existing_pid):
        print(f"Subscriber already running with PID {existing_pid}")
        return
    if existing_pid and os.path.exists(PID_FILE):
        os.remove(PID_FILE)

    log_path = os.path.join(os.path.dirname(__file__), LOG_FILE)
    with open(log_path, "a") as log_file:
        proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "subscriber:app", "--port", "4648", "--log-level", "info"],
            cwd=os.path.dirname(__file__),
            stdout=log_file,
            stderr=log_file,
            start_new_session=True,
        )
    with open(PID_FILE, "w") as f:
        f.write(str(proc.pid))
    print(f"Started subscriber daemon with PID {proc.pid}")


def _stop_daemon():
    pid = _read_pid()
    if not pid:
        print("No subscriber.pid found. Subscriber is not running.")
        return
    if not _is_running(pid):
        print(f"Stale PID file found for PID {pid}. Removing it.")
        if os.path.exists(PID_FILE):
            os.remove(PID_FILE)
        return

    print(f"Stopping subscriber daemon PID {pid}")
    os.kill(pid, signal.SIGINT)
    if os.path.exists(PID_FILE):
        os.remove(PID_FILE)


def _status_daemon():
    pid = _read_pid()
    if _is_running(pid):
        print(f"Subscriber is running with PID {pid}")
    elif pid:
        print(f"Subscriber is not running (stale PID {pid})")
    else:
        print("Subscriber is not running")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Manage the compliance subscriber service")
    parser.add_argument("--stop", action="store_true", help="Stop the background subscriber daemon")
    parser.add_argument("--status", action="store_true", help="Show subscriber daemon status")
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
