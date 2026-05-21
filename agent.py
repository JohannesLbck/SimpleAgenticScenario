from __future__ import annotations

import ast
import json
import os
from datetime import datetime
from typing import Any, Optional
import uvicorn
import argparse
import subprocess
import sys
import signal

import httpx
from fastapi import FastAPI, HTTPException
from openai import OpenAI
from pydantic import BaseModel, Field, ValidationError, field_validator

SIMULATOR_BASE_URL = os.getenv("SIMULATOR_BASE_URL", "http://127.0.0.1:8001")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

app = FastAPI(title="Lumen Agent Service", version="1.0.0")


class SensorPayload(BaseModel):
    ambient_light_lux: Optional[float] = Field(default=None, ge=0, le=200000)
    occupancy_count: Optional[int] = Field(default=None, ge=0, le=500)
    motion_detected: Optional[bool] = None
    screen_brightness_nits: Optional[int] = Field(default=None, ge=0, le=4000)
    weather_cloud_cover_pct: Optional[int] = Field(default=None, ge=0, le=100)
    time_of_day: Optional[str] = None


class AgentRequest(BaseModel):
    sensor_payload: Optional[SensorPayload] = None
    apply_to_lumen_service: bool = True


class ProcessSensorRequest(BaseModel):
    sensor_payload: SensorPayload

    @field_validator("sensor_payload", mode="before")
    @classmethod
    def parse_sensor_payload(cls, value: Any) -> SensorPayload:
        if isinstance(value, SensorPayload):
            return value
        if isinstance(value, dict):
            return SensorPayload.model_validate(value)
        if isinstance(value, str):
            raw = value.strip()
            try:
                return SensorPayload.model_validate_json(raw)
            except ValidationError:
                try:
                    parsed = ast.literal_eval(raw)
                except Exception as exc:
                    raise ValueError("sensor_payload is not valid JSON or dict string") from exc
                if not isinstance(parsed, dict):
                    raise ValueError("sensor_payload must decode to an object")
                return SensorPayload.model_validate(parsed)
        raise ValueError("sensor_payload must be an object or JSON string")


class ProcessSensorResult(BaseModel):
    lumen: int
    reason: str


class LumenDecision(BaseModel):
    lumen: int = Field(ge=0, le=3000)
    reason: str


def infer_time_of_day(hour: int) -> str:
    if 6 <= hour < 11:
        return "morning"
    if 11 <= hour < 17:
        return "day"
    if 17 <= hour < 21:
        return "evening"
    return "night"


async def fetch_simulator_signals() -> dict[str, Any]:
    endpoints = [
        "/sensor/ambient-light",
        "/sensor/occupancy",
        "/sensor/motion",
        "/sensor/context",
    ]
    out: dict[str, Any] = {}
    async with httpx.AsyncClient(timeout=10.0) as client:
        responses = await client.get(f"{SIMULATOR_BASE_URL}/health")
        responses.raise_for_status()

        for path in endpoints:
            resp = await client.get(f"{SIMULATOR_BASE_URL}{path}")
            resp.raise_for_status()
            out.update(resp.json())

    if "time_of_day" not in out:
        out["time_of_day"] = infer_time_of_day(datetime.now().hour)
    return out


def heuristic_lumen(sensor_data: dict[str, Any]) -> LumenDecision:
    ambient = float(sensor_data.get("ambient_light_lux", 0))
    occupancy = int(sensor_data.get("occupancy_count", 0))
    motion = bool(sensor_data.get("motion_detected", False))
    time_of_day = sensor_data.get("time_of_day", "day")
    screen_nits = int(sensor_data.get("screen_brightness_nits", 200))

    if occupancy == 0 and not motion:
        return LumenDecision(lumen=80, reason="Low occupancy and motion; energy-saving background level.")

    base = 550
    if time_of_day == "morning":
        base = 500
    elif time_of_day == "day":
        base = 420
    elif time_of_day == "evening":
        base = 600
    elif time_of_day == "night":
        base = 300

    lux_adjust = max(-250, min(250, int((300 - ambient) * 0.6)))
    occupancy_adjust = min(250, occupancy * 60)
    screen_adjust = min(120, max(-100, int((screen_nits - 250) * 0.2)))

    value = base + lux_adjust + occupancy_adjust + screen_adjust
    value = max(120, min(1600, value))
    return LumenDecision(lumen=value, reason="Heuristic fallback based on light, occupancy, activity, and time.")


def parse_llm_json(raw_content: str) -> dict[str, Any]:
    cleaned = raw_content.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        cleaned = cleaned.replace("json", "", 1).strip()
    return json.loads(cleaned)


def llm_decide(sensor_data: dict[str, Any]) -> LumenDecision:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return heuristic_lumen(sensor_data)

    client = OpenAI(api_key=api_key)
    system_prompt = (
        "You are a building-lighting control assistant. "
        "Given sensor data and time of day, choose a lumen value between 80 and 1800. "
        "Prefer comfort with mild energy saving. "
        "Respond with strict JSON only: {\"lumen\": integer, \"reason\": string}."
    )

    user_prompt = {
        "instruction": "Choose optimal lumen for current room condition.",
        "sensor_data": sensor_data,
        "policy": {
            "target_ambient_lux_when_occupied": "300-500",
            "night_preference": "warmer and dimmer",
            "energy_rule": "reduce output when no occupancy and no motion",
        },
    }

    response = client.responses.create(
        model=OPENAI_MODEL,
        input=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(user_prompt)},
        ],
        temperature=0.2,
    )

    text = response.output_text
    try:
        parsed = parse_llm_json(text)
        lumen = int(parsed.get("lumen", 500))
        reason = str(parsed.get("reason", "LLM decision"))
        lumen = max(80, min(1800, lumen))
        return LumenDecision(lumen=lumen, reason=reason)
    except Exception:
        return heuristic_lumen(sensor_data)


async def apply_lumen_change(lumen: int) -> dict[str, Any]:
    payload = {"lumen": lumen, "source": "llm-agent"}
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(f"{SIMULATOR_BASE_URL}/changelumens", json=payload)
        resp.raise_for_status()
        return resp.json()


def merged_signals_from_request(req: AgentRequest) -> dict[str, Any]:
    request_signals = req.sensor_payload.model_dump(exclude_none=True) if req.sensor_payload else {}
    if "time_of_day" not in request_signals:
        request_signals["time_of_day"] = infer_time_of_day(datetime.now().hour)
    return request_signals


def normalize_sensor_payload(sensor_payload: SensorPayload) -> dict[str, Any]:
    normalized = sensor_payload.model_dump(exclude_none=True)
    if "time_of_day" not in normalized:
        normalized["time_of_day"] = infer_time_of_day(datetime.now().hour)
    return normalized


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/allinonelightagent")
async def process_llm(req: AgentRequest) -> dict[str, Any]:
    simulator_signals = await fetch_simulator_signals()
    request_signals = req.sensor_payload.model_dump(exclude_none=True) if req.sensor_payload else {}

    merged = {**simulator_signals, **request_signals}
    if "time_of_day" not in merged:
        merged["time_of_day"] = infer_time_of_day(datetime.now().hour)

    decision = llm_decide(merged)
    response: dict[str, Any] = {
        "lumen": decision.lumen,
        "reason": decision.reason,
        "sensor_payload": merged,
    }

    if req.apply_to_lumen_service:
        actuator_result = await apply_lumen_change(decision.lumen)
        response["actuator_result"] = actuator_result

    return response


@app.post("/deterministiclightagent")
async def deterministic_light_agent(req: AgentRequest) -> dict[str, Any]:
    simulator_signals = await fetch_simulator_signals()
    request_signals = req.sensor_payload.model_dump(exclude_none=True) if req.sensor_payload else {}
    merged = {**simulator_signals, **request_signals}
    if "time_of_day" not in merged:
        merged["time_of_day"] = infer_time_of_day(datetime.now().hour)

    decision = heuristic_lumen(merged)
    response: dict[str, Any] = {
        "lumen": decision.lumen,
        "reason": decision.reason,
        "sensor_payload": merged,
    }

    if req.apply_to_lumen_service:
        actuator_result = await apply_lumen_change(decision.lumen)
        response["actuator_result"] = actuator_result

    return response

@app.post("/lightagent")
async def light_agent(req: ProcessSensorRequest) -> dict[str, Any]:
    # Orchestration-only endpoint: process engine passes sensor_payload argument.
    merged = normalize_sensor_payload(req.sensor_payload)
    decision = llm_decide(merged)
    result = ProcessSensorResult(
        lumen=decision.lumen,
        reason=decision.reason,
    )
    return {
        "lumen": result.lumen,
        "reason": result.reason,
    }


@app.post("/light_agent")
async def light_agent_alias(req: ProcessSensorRequest) -> dict[str, Any]:
    return await light_agent(req)


@app.post("/light_agent/debug")
async def purely_process_light_agent_debug(req: ProcessSensorRequest) -> dict[str, Any]:
    merged = normalize_sensor_payload(req.sensor_payload)
    decision = llm_decide(merged)
    return {
        "lumen": decision.lumen,
        "reason": decision.reason,
        "sensor_payload": merged,
    }


def run_server():
    uvicorn.run("agent:app", port=4749, log_level="info")


PID_FILE = "agent.pid"
LOG_FILE = "agent.log"


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
            [sys.executable, "-m", "uvicorn", "subscriber:app", "--port", "9321", "--log-level", "info"],
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
