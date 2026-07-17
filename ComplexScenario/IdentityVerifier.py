from __future__ import annotations

import argparse
import logging
import os

import httpx
from fastapi import FastAPI, HTTPException
from langchain_core.tools import tool
from langgraph.prebuilt import create_react_agent
from pydantic import BaseModel

LOG_FILE = "identityverifier.log"

BASE_URL = "https://power.bpm.cit.tum.de/BloodDonationServices"


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

@tool
def user_input(message: str) -> str:
    """Prompt the user and return typed input."""
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


