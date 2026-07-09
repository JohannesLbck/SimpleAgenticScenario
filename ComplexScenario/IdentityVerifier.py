from __future__ import annotations

import argparse
import logging
import os
import signal
import subprocess
import sys

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException
from langchain_core.tools import tool
from langgraph.prebuilt import create_react_agent
from pydantic import BaseModel

PID_FILE = "identityverifier.pid"
LOG_FILE = "identityverifier.log"
PORT = 4651

BASE_URL = "https://power.bpm.cit.tum.de/BloodDonationServices"

app = FastAPI(title="Identity Verifier Agent", version="1.0.0")

logger = logging.getLogger("identityverifier")
if not logger.handlers:
    logger.setLevel(logging.INFO)
    log_path = os.path.join(os.path.dirname(__file__), LOG_FILE)
    file_handler = logging.FileHandler(log_path)
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(file_handler)
    logger.propagate = False


# --- Tools ---

@tool
def conduct_facescan() -> str:
    """Verify the donor's identity using a facial recognition scan."""
    response = httpx.get(f"{BASE_URL}/face-scan-id")
    response.raise_for_status()
    data = response.json()
    return "Face scan ID verification passed." if data["result"] else "Face scan ID verification failed."


@tool
def verify_digital_id() -> str:
    """Verify the donor's identity using their digital ID document."""
    response = httpx.get(f"{BASE_URL}/digital-id")
    response.raise_for_status()
    data = response.json()
    return "Digital ID verification passed." if data["result"] else "Digital ID verification failed."


@tool
def verify_digital_signature() -> str:
    """Verify the donor's identity using a digital signature."""
    response = httpx.get(f"{BASE_URL}/digital-signature")
    response.raise_for_status()
    data = response.json()
    return "Digital signature verification passed." if data["result"] else "Digital signature verification failed."


@tool
def record_vocal_consent() -> str:
    """Record and verify the donor's vocal consent."""
    response = httpx.get(f"{BASE_URL}/vocal-consent")
    response.raise_for_status()
    data = response.json()
    return "Vocal consent recorded and verified." if data["result"] else "Vocal consent verification failed."


@tool
def verify_signature() -> str:
    """Verify the donor's handwritten signature."""
    response = httpx.get(f"{BASE_URL}/signature")
    response.raise_for_status()
    data = response.json()
    return "Signature verification passed." if data["result"] else "Signature verification failed."


@tool
def physician_clearance() -> str:
    """Request physician clearance for blood donation eligibility."""
    response = httpx.get(f"{BASE_URL}/physician")
    response.raise_for_status()
    data = response.json()
    if data["result"]:
        return "Physician clearance granted."
    reason = data.get("reason") or "No specific reason provided."
    return f"Physician clearance denied. Reason: {reason}"


# --- Agent ---

SYSTEM_PROMPT = (
    "Your task is to verify a user's identity and eligibility for blood donation. "
    "Explain the different verification options available to the user and, when prompted, "
    "use the appropriate tool to perform the verification. "
    "If a verification step fails, explain potential reasons that could have caused the failure "
    "for the chosen option, then offer the available options again."
)

agent = create_react_agent(
    model="openai:gpt-5.5",
    tools=[
        conduct_facescan,
        verify_digital_id,
        verify_digital_signature,
        record_vocal_consent,
        verify_signature,
        physician_clearance,
    ],
    prompt=SYSTEM_PROMPT,
)


# --- FastAPI ---

class InvokeRequest(BaseModel):
    message: str


class InvokeResponse(BaseModel):
    response: str


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/invoke", response_model=InvokeResponse)
def invoke(request: InvokeRequest) -> InvokeResponse:
    try:
        result = agent.invoke(
            {"messages": [{"role": "user", "content": request.message}]}
        )
        last_message = result["messages"][-1]
        content = last_message.content if hasattr(last_message, "content") else str(last_message)
        logger.info("invoke message=%r response=%r", request.message, content)
        return InvokeResponse(response=content)
    except Exception as exc:
        logger.error("invoke error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


# --- Daemon helpers ---

def run_server() -> None:
    uvicorn.run("IdentityVerifier:app", host="0.0.0.0", port=PORT, log_level="info")


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
        print(f"IdentityVerifier already running with PID {existing_pid}")
        return
    if existing_pid and os.path.exists(PID_FILE):
        os.remove(PID_FILE)

    log_path = os.path.join(os.path.dirname(__file__), LOG_FILE)
    with open(log_path, "a") as log_file:
        proc = subprocess.Popen(
            [
                sys.executable, "-m", "uvicorn",
                "IdentityVerifier:app",
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
    print(f"Started IdentityVerifier daemon with PID {proc.pid}")


def _stop_daemon() -> None:
    pid = _read_pid()
    if not pid:
        print("No identityverifier.pid found. IdentityVerifier is not running.")
        return
    if not _is_running(pid):
        print(f"Stale PID file found for PID {pid}. Removing it.")
        if os.path.exists(PID_FILE):
            os.remove(PID_FILE)
        return
    print(f"Stopping IdentityVerifier daemon PID {pid}")
    os.kill(pid, signal.SIGINT)
    if os.path.exists(PID_FILE):
        os.remove(PID_FILE)


def _status_daemon() -> None:
    pid = _read_pid()
    if _is_running(pid):
        print(f"IdentityVerifier is running with PID {pid}")
    elif pid:
        print(f"IdentityVerifier is not running (stale PID {pid})")
    else:
        print("IdentityVerifier is not running")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Manage the IdentityVerifier service")
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

