from __future__ import annotations

import logging
import os
import warnings
from operator import add
from typing import Annotated, Literal, TypedDict

import httpx
from langchain_core.tools import tool
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import create_react_agent

LOG_FILE = "ComplexScenario.log"

BASE_URL = "https://power.bpm.cit.tum.de/BloodDonationServices"

logger = logging.getLogger("identityverifier")
if not logger.handlers:
    logger.setLevel(logging.INFO)
    log_path = os.path.join(os.path.dirname(__file__), LOG_FILE)
    file_handler = logging.FileHandler(log_path)
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(file_handler)
    logger.propagate = False


# --- StateGraph process model (aligned with AgenticBloodRegistration.xml) ---

def _or_reducer(existing: bool, incoming: bool) -> bool:
    return bool(existing) or bool(incoming)


class ProcessState(TypedDict):
    message: str
    requested_action: str
    identity_result: bool | None
    consent_result: bool | None
    interview_result: Literal["accept", "physician", "reject"] | None
    physician_result: bool | None
    final_decision: str
    terminate: Annotated[bool, _or_reducer]
    notes: Annotated[list[str], add]
    final_response: str


def _call_service(path: str, success_text: str, failure_text: str) -> tuple[bool, str]:
    try:
        response = httpx.get(f"{BASE_URL}/{path}", timeout=10.0)
        response.raise_for_status()
        data = response.json()
        if data.get("result"):
            return True, success_text
        reason = data.get("reason")
        if reason:
            return False, f"{failure_text} Reason: {reason}"
        return False, failure_text
    except Exception as exc:
        return False, f"{failure_text} Error while calling service: {exc}"


def _normalize_requested_action(message: str) -> str:
    lowered = message.lower()
    if "stop" in lowered or "cancel" in lowered:
        return "stop"
    if "reject" in lowered:
        return "reject"
    if "accept" in lowered:
        return "accept"
    return "undecided"


@tool
def conduct_facescan() -> str:
    """Verify identity via face scan service."""
    ok, text = _call_service(
        "face-scan-id",
        "Face scan verification passed.",
        "Face scan verification failed.",
    )
    return text if ok else text

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


@tool
def verify_digital_id() -> str:
    """Verify identity via digital ID service."""
    ok, text = _call_service(
        "digital-id",
        "Digital ID verification passed.",
        "Digital ID verification failed.",
    )
    return text if ok else text


@tool
def verify_digital_signature() -> str:
    """Verify consent/identity via digital signature service."""
    ok, text = _call_service(
        "digital-signature",
        "Digital signature verification passed.",
        "Digital signature verification failed.",
    )
    return text if ok else text


@tool
def record_vocal_consent() -> str:
    """Record and verify vocal consent."""
    ok, text = _call_service(
        "vocal-consent",
        "Vocal consent verified.",
        "Vocal consent verification failed.",
    )
    return text if ok else text


@tool
def verify_signature() -> str:
    """Verify handwritten signature consent."""
    ok, text = _call_service(
        "signature",
        "Signature verification passed.",
        "Signature verification failed.",
    )
    return text if ok else text


_verify_identity_agent = None
_obtain_consent_agent = None
_answer_user_queries_agent = None
_interview_questionnaire_agent = None


def _get_verify_identity_agent():
    global _verify_identity_agent
    if _verify_identity_agent is None:
        _verify_identity_agent = create_react_agent(
            model="openai:gpt-5.5",
            tools=[
                user_input,
                conduct_facescan,
                verify_digital_id,
            ],
            prompt=(
                "You should verify the identity of a user. Select exactly one of the available tools "
                "based on the user message, then respond with 'RESULT: pass' or 'RESULT: fail' "
                "followed by a short explanation."
            ),
        )
    return _verify_identity_agent


def _get_obtain_consent_agent():
    global _obtain_consent_agent
    if _obtain_consent_agent is None:
        _obtain_consent_agent = create_react_agent(
            model="openai:gpt-5.5",
            tools=[
                user_input,
                verify_digital_signature,
                record_vocal_consent,
                verify_signature,
            ],
            prompt=(
                "You should obtain the consent from a user. Choose the most suitable consent verification "
                "tool based on the user message, then respond with 'RESULT: pass' or 'RESULT: fail' "
                "followed by a short explanation."
                "Start by asking the user which verification option he prefers, then use the appropriate tool to perform the verification."
            ),
        )
    return _obtain_consent_agent


def _get_answer_user_queries_agent():
    global _answer_user_queries_agent
    if _answer_user_queries_agent is None:
        _answer_user_queries_agent = create_react_agent(
            model="openai:gpt-5.5",
            tools=[user_input],
            prompt=(
                "You handle the Answer User Queries step. "
                "Always call the user_input tool exactly once to ask what the user wants clarified, "
                "then provide a short and helpful answer."
            ),
        )
    return _answer_user_queries_agent


def _get_interview_questionnaire_agent():
    global _interview_questionnaire_agent
    if _interview_questionnaire_agent is None:
        _interview_questionnaire_agent = create_react_agent(
            model="openai:gpt-5.5",
            tools=[user_input],
            prompt=(
                "You conduct the interview and questionnaire step. "
                "Use the user_input tool to ask concise eligibility questions. "
                "After collecting answers, your final response must include exactly one of these decision lines: "
                "'DECISION: accept', 'DECISION: physician', or 'DECISION: reject'. "
                "Then add one brief reason line."
            ),
        )
    return _interview_questionnaire_agent


def _invoke_agent_text(agent, message: str) -> str:
    result = agent.invoke({"messages": [{"role": "user", "content": message}]})
    messages = result.get("messages", [])
    if not messages:
        return ""
    last_message = messages[-1]
    return last_message.content if hasattr(last_message, "content") else str(last_message)


def _did_pass(agent_text: str) -> bool:
    lowered = agent_text.lower()
    if "result: pass" in lowered:
        return True
    if "result: fail" in lowered:
        return False
    positive_tokens = ["passed", "granted", "verified", "success"]
    negative_tokens = ["failed", "denied", "error"]
    if any(token in lowered for token in negative_tokens):
        return False
    return any(token in lowered for token in positive_tokens)


def _parse_interview_decision(agent_text: str) -> Literal["accept", "physician", "reject"]:
    lowered = agent_text.lower()
    if "decision: accept" in lowered:
        return "accept"
    if "decision: physician" in lowered:
        return "physician"
    if "decision: reject" in lowered:
        return "reject"
    if "physician" in lowered:
        return "physician"
    if "reject" in lowered or "ineligible" in lowered:
        return "reject"
    return "accept"


def stop_donation_process(state: ProcessState) -> dict:
    if state["requested_action"] == "stop":
        return {
            "terminate": True,
            "final_decision": "stopped",
            "notes": ["Stop Donation Process triggered."],
        }
    return {"notes": ["Stop Donation Process branch checked and not triggered."]}


def verify_identity(state: ProcessState) -> dict:
    message = state["message"]
    agent = _get_verify_identity_agent()
    agent_text = _invoke_agent_text(agent, message)
    return {
        "identity_result": _did_pass(agent_text),
        "notes": [f"Verify Identity (agent): {agent_text}"],
    }


def provide_information(_: ProcessState) -> dict:
    return {
        "notes": [
            "Provide Information: donor was informed about verification options and process steps."
        ]
    }


def answer_user_queries(state: ProcessState) -> dict:
    agent = _get_answer_user_queries_agent()
    agent_text = _invoke_agent_text(agent, state["message"])
    return {"notes": [f"Answer User Queries (agent): {agent_text}"]}


def obtain_consent(state: ProcessState) -> dict:
    message = state["message"]
    agent = _get_obtain_consent_agent()
    agent_text = _invoke_agent_text(agent, message)
    return {
        "consent_result": _did_pass(agent_text),
        "notes": [f"Obtain Consent (agent): {agent_text}"],
    }


def conduct_interview_and_questionnaire(state: ProcessState) -> dict:
    agent = _get_interview_questionnaire_agent()
    agent_text = _invoke_agent_text(agent, state["message"])
    decision = _parse_interview_decision(agent_text)
    return {
        "interview_result": decision,
        "notes": [f"Conduct Interview and Questionnaire (agent): {agent_text}"],
    }


def direct_decision_router(state: ProcessState) -> Literal["direct_acceptance", "direct_rejection", "direct_physician_questions"]:
    if state["requested_action"] == "reject":
        return "direct_rejection"
    if state["identity_result"] is False or state["consent_result"] is False:
        return "direct_rejection"
    if state["requested_action"] == "accept" and state["interview_result"] == "accept":
        return "direct_acceptance"
    if state["interview_result"] == "accept":
        return "direct_acceptance"
    if state["interview_result"] == "reject":
        return "direct_rejection"
    return "direct_physician_questions"


def direct_acceptance(_: ProcessState) -> dict:
    return {
        "final_decision": "accepted",
        "notes": ["Direct Acceptance executed."],
    }


def direct_rejection(_: ProcessState) -> dict:
    return {
        "final_decision": "rejected",
        "terminate": True,
        "notes": ["Direct Rejection executed; process terminated."],
    }


def direct_physician_questions(_: ProcessState) -> dict:
    return {"notes": ["Direct Physician Questions executed."]}


def physician_clearance(state: ProcessState) -> dict:
    ok, text = _call_service(
        "physician",
        "Physician clearance granted.",
        "Physician clearance denied.",
    )
    return {"physician_result": ok, "notes": [text]}


def physician_router(state: ProcessState) -> Literal["physician_acceptance", "terminate_after_physician"]:
    if state["physician_result"]:
        return "physician_acceptance"
    return "terminate_after_physician"


def physician_acceptance(_: ProcessState) -> dict:
    return {
        "final_decision": "accepted",
        "notes": ["Physician path: accepted."],
    }


def terminate_after_physician(_: ProcessState) -> dict:
    return {
        "final_decision": "rejected",
        "terminate": True,
        "notes": ["Physician path: termination executed."],
    }


def finalize_response(state: ProcessState) -> dict:
    notes = " ".join(state.get("notes", []))
    decision = state.get("final_decision") or "pending"
    terminate = state.get("terminate", False)
    response = (
        f"Decision: {decision}. "
        f"Terminated: {terminate}. "
        f"Trace: {notes}"
    )
    return {"final_response": response}


def build_workflow():
    graph = StateGraph(ProcessState)

    graph.add_node("stop_donation_process", stop_donation_process)
    graph.add_node("verify_identity", verify_identity)
    graph.add_node("provide_information", provide_information)
    graph.add_node("answer_user_queries", answer_user_queries)
    graph.add_node("obtain_consent", obtain_consent)
    graph.add_node("conduct_interview_and_questionnaire", conduct_interview_and_questionnaire)
    graph.add_node("direct_acceptance", direct_acceptance)
    graph.add_node("direct_rejection", direct_rejection)
    graph.add_node("direct_physician_questions", direct_physician_questions)
    graph.add_node("physician_clearance", physician_clearance)
    graph.add_node("physician_acceptance", physician_acceptance)
    graph.add_node("terminate_after_physician", terminate_after_physician)
    graph.add_node("finalize_response", finalize_response)

    # Top-level parallel 
    graph.add_edge(START, "stop_donation_process")
    graph.add_edge(START, "verify_identity")

    graph.add_edge("verify_identity", "provide_information")

    # Parallel split 
    graph.add_edge("provide_information", "answer_user_queries")
    graph.add_edge("provide_information", "obtain_consent")

    graph.add_edge("obtain_consent", "conduct_interview_and_questionnaire")

    graph.add_conditional_edges(
        "conduct_interview_and_questionnaire",
        direct_decision_router,
        {
            "direct_acceptance": "direct_acceptance",
            "direct_rejection": "direct_rejection",
            "direct_physician_questions": "direct_physician_questions",
        },
    )

    graph.add_edge("direct_physician_questions", "physician_clearance")
    graph.add_conditional_edges(
        "physician_clearance",
        physician_router,
        {
            "physician_acceptance": "physician_acceptance",
            "terminate_after_physician": "terminate_after_physician",
        },
    )

    # Join branches before END.
    graph.add_edge("stop_donation_process", "finalize_response")
    graph.add_edge("answer_user_queries", "finalize_response")
    graph.add_edge("direct_acceptance", "finalize_response")
    graph.add_edge("direct_rejection", "finalize_response")
    graph.add_edge("physician_acceptance", "finalize_response")
    graph.add_edge("terminate_after_physician", "finalize_response")

    graph.add_edge("finalize_response", END)
    return graph.compile()


workflow = build_workflow()


def render_workflow_graph(output_path: str = "workflow_graph.png") -> str:
    png_data = workflow.get_graph().draw_mermaid_png()
    target_path = os.path.join(os.path.dirname(__file__), output_path)
    with open(target_path, "wb") as f:
        f.write(png_data)
    return target_path


if __name__ == "__main__":
    warnings.filterwarnings(
        "ignore",
        message=r"Core Pydantic V1 functionality isn't compatible with Python 3\.14 or greater\.",
        category=UserWarning,
    )
    image_path = render_workflow_graph()
    print(f"Workflow image written to: {image_path}")