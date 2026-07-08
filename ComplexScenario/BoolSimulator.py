from __future__ import annotations

import argparse
import logging
import os
import random
import signal
import subprocess
import sys

import uvicorn
from fastapi import FastAPI

PID_FILE = "boolsimulator.pid"
LOG_FILE = "boolsimulator.log"
PORT = 4650

PHYSICIAN_DECLINE_REASONS = [
    "Hemoglobin level below the minimum threshold of 12.5 g/dL.",
    "Donor reports having received a blood transfusion within the past 12 months.",
    "Active antibiotic treatment ongoing; deferral until 7 days after course completion.",
    "Blood pressure reading of 170/105 mmHg exceeds the accepted upper limit.",
    "Donor travelled to a malaria-endemic region within the past 6 months.",
    "Body weight below the minimum of 50 kg required for whole blood donation.",
    "Donor reports tattoo or piercing received within the past 4 months.",
    "Recent surgical procedure within the past 6 months; insufficient recovery period.",
    "Positive result on rapid infectious disease screening test.",
    "Donor reports symptoms consistent with active respiratory infection.",
]

app = FastAPI(title="Bool Simulator", version="1.0.0")

logger = logging.getLogger("boolsimulator")
if not logger.handlers:
    logger.setLevel(logging.INFO)
    log_path = os.path.join(os.path.dirname(__file__), LOG_FILE)
    file_handler = logging.FileHandler(log_path)
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(file_handler)
    logger.propagate = False


def _random_bool(probability: int) -> bool:
    """Return True with the given probability (1–100)."""
    return random.randint(1, 100) <= probability


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/face-scan-id")
def face_scan_id() -> dict:
    result = _random_bool(85)
    logger.info("face-scan-id result=%s", result)
    return {"result": result}


@app.get("/digital-id")
def digital_id() -> dict:
    result = _random_bool(90)
    logger.info("digital-id result=%s", result)
    return {"result": result}


@app.get("/digital-signature")
def digital_signature() -> dict:
    result = _random_bool(80)
    logger.info("digital-signature result=%s", result)
    return {"result": result}


@app.get("/vocal-consent")
def vocal_consent() -> dict:
    result = _random_bool(75)
    logger.info("vocal-consent result=%s", result)
    return {"result": result}


@app.get("/signature")
def signature() -> dict:
    result = _random_bool(88)
    logger.info("signature result=%s", result)
    return {"result": result}


@app.get("/physician")
def physician() -> dict:
    result = _random_bool(70)
    reason = None if result else random.choice(PHYSICIAN_DECLINE_REASONS)
    logger.info("physician result=%s reason=%s", result, reason)
    return {"result": result, "reason": reason}


def run_server() -> None:
    uvicorn.run("BoolSimulator:app", host="0.0.0.0", port=PORT, log_level="info")


def _read_pid(pid_file: str = PID_FILE) -> int | None:
    if not os.path.exists(pid_file):
        return None
    try:
        with open(pid_file) as f:
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
        print(f"BoolSimulator already running with PID {existing_pid}")
        return
    if existing_pid and os.path.exists(PID_FILE):
        os.remove(PID_FILE)

    log_path = os.path.join(os.path.dirname(__file__), LOG_FILE)
    with open(log_path, "a") as log_file:
        proc = subprocess.Popen(
            [
                sys.executable, "-m", "uvicorn",
                "BoolSimulator:app",
                "--host", "0.0.0.0",
                "--port", str(PORT),
                "--log-level", "info",
            ],
            cwd=os.path.dirname(__file__) or ".",
            stdout=log_file,
            stderr=log_file,
            start_new_session=True,
        )
    with open(PID_FILE, "w") as f:
        f.write(str(proc.pid))
    print(f"Started BoolSimulator daemon with PID {proc.pid}")


def _stop_daemon() -> None:
    pid = _read_pid()
    if not pid:
        print("No boolsimulator.pid found. BoolSimulator is not running.")
        return
    if not _is_running(pid):
        print(f"Stale PID file found for PID {pid}. Removing it.")
        if os.path.exists(PID_FILE):
            os.remove(PID_FILE)
        return

    print(f"Stopping BoolSimulator daemon PID {pid}")
    os.kill(pid, signal.SIGINT)
    if os.path.exists(PID_FILE):
        os.remove(PID_FILE)


def _status_daemon() -> None:
    pid = _read_pid()
    if _is_running(pid):
        print(f"BoolSimulator is running with PID {pid}")
    elif pid:
        print(f"BoolSimulator is not running (stale PID {pid})")
    else:
        print("BoolSimulator is not running")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Manage the BoolSimulator service")
    parser.add_argument("--stop", action="store_true", help="Stop the background daemon")
    parser.add_argument("--status", action="store_true", help="Show daemon status")
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
