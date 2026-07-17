# ComplexScenario

This folder contains a multi-step agentic blood-donation registration scenario used as a richer workflow benchmark than the simple lighting-control case.

The scenario combines:

- Parallel branches (stop handling vs. verification/interaction path)
- Conditional routing (accept, reject, physician escalation)
- Tool-based service calls for identity, consent, and physician clearance
- Trace-style logging for post-run inspection

## Main Files

- `AgenticBloodRegistration.xml`: reference process model for the scenario
- `ComplexScenario.py`: LangGraph workflow implementation and graph rendering entry point
- `IdentityVerifier.py`: FastAPI + tool-enabled verification service implementation
- `BoolSimulator.py`: local simulator for endpoint behavior (`/face-scan-id`, `/digital-id`, `/digital-signature`, `/vocal-consent`, `/signature`, `/physician`)
- `workflow_graph.png`: generated visualization of the workflow graph

## Scenario Flow

1. Check whether donation should be stopped and verify identity (parallel entry).
2. Provide information to the donor.
3. Run user-query answering and consent acquisition.
4. Run interview/questionnaire and determine one of three paths:
   - direct acceptance
   - direct rejection
   - physician questions/clearance
5. Finalize and emit decision plus trace summary.

## Running

Install dependencies from the repository root first:

```bash
cd ~/Papers/AgenticFundamentals/SimpleAgenticScenario
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Generate the workflow image:

```bash
cd ~/Papers/AgenticFundamentals/SimpleAgenticScenario/ComplexScenario
source ../.venv/bin/activate
python ComplexScenario.py
```

Run the local simulator in foreground mode:

```bash
cd ~/Papers/AgenticFundamentals/SimpleAgenticScenario/ComplexScenario
source ../.venv/bin/activate
python BoolSimulator.py --foreground
```

## Endpoint Configuration Note

`ComplexScenario.py` and `IdentityVerifier.py` currently use this remote base URL, with no guarantee that it is always running:

- `https://power.bpm.cit.tum.de/BloodDonationServices`

If you want to test fully local behavior, set `BASE_URL` in those files to your local simulator endpoint.
