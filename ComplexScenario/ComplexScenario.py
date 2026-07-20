from __future__ import annotations

import argparse
import logging
import os
import time
import warnings
from operator import add
from typing import Annotated, Literal, TypedDict

warnings.filterwarnings(
    "ignore",
    message=r"Core Pydantic V1 functionality isn't compatible with Python 3\.14 or greater\.",
    category=UserWarning,
)
warnings.filterwarnings(
    "ignore",
    message=r"create_react_agent has been moved to `langchain\.agents`\. Please update your import to `from langchain\.agents import create_agent`\.",
    category=DeprecationWarning,
)

import httpx
from langchain_core.tools import tool
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import create_react_agent

LOG_FILE = "ComplexScenario.log"

DEFAULT_BASE_URL = "http://127.0.0.1:4650"
BASE_URL = os.getenv("BLOOD_DONATION_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
MODEL_NAME = os.getenv("OPENAI_MODEL", "openai:gpt-5.5")
DEFAULT_EXAMPLE_MESSAGE = (
    "Please continue my blood donation registration. Use my digital ID for identity verification "
    "and digital signature for consent. I am 32 years old, weigh 72 kg, last donated 5 months "
    "ago, I am not pregnant, and I have had no recent illness, travel, surgery, antibiotics, "
    "or infection risks."
)
DEFAULT_EXAMPLE_INPUTS = [
    "Please explain what happens after the identity verification step.",
    "Please use my digital signature to complete the consent verification.",
    "I am 32 years old, weigh 72 kg, last donated blood 5 months ago, I am not pregnant, and I have had no recent illness, fever, infection symptoms, travel to risk areas, surgery, tattoos, antibiotics, transfusions, or drug-risk exposures. I feel well today and I am eligible to donate.",
]
DEFAULT_EXAMPLE_FALLBACK = (
    "I am 32 years old, weigh 72 kg, last donated blood 5 months ago, I am not pregnant, and I have had no recent illness, fever, infection symptoms, travel to risk areas, surgery, tattoos, antibiotics, transfusions, or drug-risk exposures. I feel well today and I am eligible to donate."
)
DEFAULT_EXAMPLE_QUERY_RESPONSE = "Please explain what happens after the identity verification step."
DEFAULT_EXAMPLE_IDENTITY_RESPONSE = "Please verify me with my digital ID."
DEFAULT_EXAMPLE_CONSENT_RESPONSE = "Please use my digital signature to complete the consent verification."
DEFAULT_EXAMPLE_INTERVIEW_RESPONSE = DEFAULT_EXAMPLE_FALLBACK

logger = logging.getLogger("identityverifier")
if not logger.handlers:
    logger.setLevel(logging.INFO)
    log_path = os.path.join(os.path.dirname(__file__), LOG_FILE)
    file_handler = logging.FileHandler(log_path)
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(file_handler)
    logger.propagate = False


_example_input_queue: list[str] = []
_example_input_fallback: str | None = None


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


def configure_example_inputs(inputs: list[str], fallback: str | None = None) -> None:
    global _example_input_queue, _example_input_fallback
    _example_input_queue = list(inputs)
    _example_input_fallback = fallback


def _match_example_input(prompt: str) -> str | None:
    lowered = prompt.lower()
    if not lowered:
        return None
    if any(token in lowered for token in ["clarified", "clarify", "what happens", "what would you like clarified", "what can i help", "what do you want clarified"]):
        return DEFAULT_EXAMPLE_QUERY_RESPONSE
    if any(token in lowered for token in ["identity", "digital id", "face scan", "verification option"]):
        return DEFAULT_EXAMPLE_IDENTITY_RESPONSE
    if any(token in lowered for token in ["consent", "digital signature", "vocal consent", "signature option", "which verification option", "which option do you prefer"]):
        return DEFAULT_EXAMPLE_CONSENT_RESPONSE
    if any(token in lowered for token in ["age", "weight", "pregnan", "travel", "illness", "surgery", "antibiotic", "donation history", "last donated", "risk factor", "infection", "drug", "tattoo", "transfusion", "questionnaire", "eligible"]):
        return DEFAULT_EXAMPLE_INTERVIEW_RESPONSE
    return None


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
    matched_example = _match_example_input(message or "")
    if matched_example is not None:
        return matched_example
    if _example_input_queue:
        return _example_input_queue.pop(0)
    if _example_input_fallback is not None:
        return _example_input_fallback
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
            model=MODEL_NAME,
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
            model=MODEL_NAME,
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
            model=MODEL_NAME,
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
            model=MODEL_NAME,
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
    content = last_message.content if hasattr(last_message, "content") else last_message
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


def run_example(
    message: str = DEFAULT_EXAMPLE_MESSAGE,
    example_inputs: list[str] | None = None,
    fallback_input: str = DEFAULT_EXAMPLE_FALLBACK,
) -> dict:
    _ensure_service_ready()
    configure_example_inputs(example_inputs or DEFAULT_EXAMPLE_INPUTS, fallback_input)
    try:
        return workflow.invoke(
            {
                "message": message,
                "requested_action": _normalize_requested_action(message),
                "identity_result": None,
                "consent_result": None,
                "interview_result": None,
                "physician_result": None,
                "final_decision": "",
                "terminate": False,
                "notes": [],
                "final_response": "",
            }
        )
    finally:
        configure_example_inputs([], None)


def _build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the ComplexScenario workflow against live endpoints")
    parser.add_argument(
        "--message",
        default=DEFAULT_EXAMPLE_MESSAGE,
        help="Example donor request sent into the workflow",
    )
    parser.add_argument(
        "--render-graph",
        action="store_true",
        help="Render the workflow graph before invoking the example workflow",
    )
    return parser


if __name__ == "__main__":
    args = _build_argument_parser().parse_args()
    if args.render_graph:
        image_path = render_workflow_graph()
        print(f"Workflow image written to: {image_path}")
    result = run_example(args.message)
    print(result.get("final_response", ""))