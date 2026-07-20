from __future__ import annotations

import argparse
import logging
import os
import time

import httpx
from langchain_core.tools import tool
from langgraph.prebuilt import create_react_agent

LOG_FILE = "identityverifier.log"

DEFAULT_BASE_URL = "http://127.0.0.1:4650"
BASE_URL = os.getenv("BLOOD_DONATION_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
MODEL_NAME = os.getenv("OPENAI_MODEL", "openai:gpt-5.5")
DEFAULT_EXAMPLE_MESSAGE = (
    "Please verify my blood donation identity. I want to use my digital ID, and if you need to "
    "check eligibility further you may request physician clearance."
)
DEFAULT_EXAMPLE_VERIFICATION_RESPONSE = "Please use my digital ID for verification."


logger = logging.getLogger("identityverifier")
if not logger.handlers:
    logger.setLevel(logging.INFO)
    log_path = os.path.join(os.path.dirname(__file__), LOG_FILE)
    file_handler = logging.FileHandler(log_path)
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(file_handler)
    logger.propagate = False


_example_input_enabled = False


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

@tool
def user_input(message: str) -> str:
    """Prompt the user and return typed input."""
    if _example_input_enabled:
        return DEFAULT_EXAMPLE_VERIFICATION_RESPONSE
    prompt = (message or "").strip()
    if prompt:
        prompt = f"{prompt} "
    try:
        return input(prompt)
    except EOFError:
        return ""


# --- Agent ---

SYSTEM_PROMPT = (
    "Your task is to verify a user's identity and eligibility for blood donation. "
    "Explain the different verification options available to the user and, when prompted, "
    "use the appropriate tool to perform the verification. "
    "If a verification step fails, explain potential reasons that could have caused the failure "
    "for the chosen option, then offer the available options again."
)

agent = create_react_agent(
    model=MODEL_NAME,
    tools=[
        user_input,
        conduct_facescan,
        verify_digital_id,
        verify_digital_signature,
        record_vocal_consent,
        verify_signature,
        physician_clearance,
    ],
    prompt=SYSTEM_PROMPT,
)


def _using_local_simulator() -> bool:
    return BASE_URL in {DEFAULT_BASE_URL, "http://localhost:4650"}


def _probe_service() -> None:
    probe_path = "/health" if _using_local_simulator() else "/digital-id"
    response = httpx.get(f"{BASE_URL}{probe_path}", timeout=2.0)
    response.raise_for_status()


def _ensure_service_ready() -> None:
    try:
        _probe_service()
        return
    except Exception:
        if not _using_local_simulator():
            raise

    from BoolSimulator import _start_daemon

    _start_daemon()
    deadline = time.monotonic() + 10.0
    last_error = None
    while time.monotonic() < deadline:
        try:
            _probe_service()
            return
        except Exception as exc:
            last_error = exc
            time.sleep(0.5)
    raise RuntimeError(f"Service at {BASE_URL} did not become ready: {last_error}")


def _extract_text(message) -> str:
    content = getattr(message, "content", message)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text")
                if text:
                    parts.append(str(text))
            elif item:
                parts.append(str(item))
        return "\n".join(parts)
    return str(content)


def invoke_agent_text(message: str) -> str:
    result = agent.invoke({"messages": [{"role": "user", "content": message}]})
    messages = result.get("messages", [])
    if not messages:
        return ""
    return _extract_text(messages[-1])


def run_example(message: str = DEFAULT_EXAMPLE_MESSAGE) -> str:
    global _example_input_enabled
    _ensure_service_ready()
    _example_input_enabled = True
    try:
        return invoke_agent_text(message)
    finally:
        _example_input_enabled = False


def _build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the identity verification agent against live endpoints")
    parser.add_argument(
        "--message",
        default=DEFAULT_EXAMPLE_MESSAGE,
        help="Example user request sent to the agent",
    )
    return parser


if __name__ == "__main__":
    args = _build_argument_parser().parse_args()
    print(run_example(args.message))


