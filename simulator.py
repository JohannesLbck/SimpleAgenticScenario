from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
import random

from fastapi import FastAPI
from pydantic import BaseModel, Field

app = FastAPI(title="Sensor and Lumen Simulator", version="1.0.0")


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
    return {"ambient_light_lux": lux}


@app.get("/sensor/occupancy")
def occupancy() -> dict:
    count = state.overrides.occupancy_count
    if count is None:
        count = _occupancy_default(datetime.now().hour)
    return {"occupancy_count": count}


@app.get("/sensor/motion")
def motion() -> dict:
    if state.overrides.motion_detected is not None:
        detected = state.overrides.motion_detected
    else:
        detected = random.choice([True, True, True, False])
    return {"motion_detected": detected}


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

    return {
        "hour": hour,
        "time_of_day": tod,
        "screen_brightness_nits": screen_nits,
        "weather_cloud_cover_pct": cloud_cover,
    }


@app.get("/sensor/all")
def sensor_all() -> dict:
    data = {}
    data.update(ambient_light())
    data.update(occupancy())
    data.update(motion())
    data.update(context())
    return data


@app.post("/readsensor")
def readsensor(_: ReadSensorRequest | None = None) -> dict:
    # Process-engine compatible endpoint: request body may contain a data argument.
    return sensor_all()


@app.post("/read_sensor")
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
        "last_command_source": state.last_command_source,
    }
