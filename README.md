# SimpleAgenticScenario

This repository contains an experimental agentic lighting-control scenario with:

- Python services for sensor simulation and lighting control
- Ruby MCP tool servers for orchestration-style agent runs
- Evaluation scripts to compare runtime behavior against ground truth and compute process metrics

The project is useful for testing different control styles (LLM-based, heuristic, process-driven) and then quantitatively evaluating decisions.

## Repository Overview

- `agent.py`: FastAPI lighting agent service (LLM + heuristic fallback)
- `Simulators/simulator.py`: dynamic simulator (randomized environment)
- `Simulators/simulator_static.py`: dataset-driven simulator and ground-truth provider
- `EvalHelper/`: log comparison and evaluation utilities
- `EvalHelper/MetricCalculation/`: cyclomatic and ABC metric scripts for process XML files
- `mcp/`: Ruby MCP tool servers + orchestration scripts (`oo1.sh`, `oo3.sh`, `ootest.sh`)

## Architecture at a Glance

Python path:

1. Simulator exposes sensor endpoints and a lumen actuator endpoint
2. Agent reads sensor signals, chooses target lumen, applies change
3. Logs are evaluated with scripts in `EvalHelper/`

MCP path:

1. Ruby MCP servers expose tools (`light`, `sleep`, `log`)
2. `mcp/agent.rb` invokes an LLM with these tools
3. Logs are written and compared with evaluation scripts

## Prerequisites

## Python services

- Python 3.10+
- Virtual environment recommended

Install:

```bash
cd /home/johannesl/Papers/AgenticFundamentals/SimpleAgenticScenario
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Ruby MCP services

You need Ruby plus gems used by the scripts:

- `sinatra`
- `mcp`
- `riddl-client` / `riddl-server`
- `ruby_llm`

The MCP LLM scripts expect API credentials in:

- `mcp/api.key` (for `mcp/agent.rb`)
- `mcp/agent_endpoint/api.key` (for `mcp/agent_endpoint/server.rb`)

## Ports and Endpoints

Python services:

- Agent: `http://127.0.0.1:4749`
- Static simulator: `http://127.0.0.1:4649`
- Dynamic simulator: `http://127.0.0.1:4648`

Useful agent endpoints:

- `GET /health`
- `POST /allinonelightagent`
- `POST /deterministiclightagent`
- `POST /lightagent`
- `POST /light_agent` (alias)

Useful simulator endpoints:

- `GET /readsensor`
- `GET /sensor/all`
- `PUT /changelumens` (or `/change_lumens`)
- `GET /changelumens/state`

MCP services (expected by `mcp/agent.rb`):

- Light MCP: `http://localhost:4567/_mcp`
- Sleep MCP: `http://localhost:4568/_mcp`
- Logger MCP: `http://localhost:4569/_mcp`

Additional Ruby service ports:

- Logger backend (`mcp/logger/server.rb`): `9091`
- Agent endpoint (`mcp/agent_endpoint/server.rb`): `9092`

## Quickstart (Python-Only Flow)

Run static simulator in one terminal:

```bash
cd /home/johannesl/Papers/AgenticFundamentals/SimpleAgenticScenario/Simulators
source ../.venv/bin/activate
python simulator_static.py --foreground
```

Run agent in another terminal:

```bash
cd /home/johannesl/Papers/AgenticFundamentals/SimpleAgenticScenario
source .venv/bin/activate
SIMULATOR_BASE_URL=http://127.0.0.1:4649 python agent.py --foreground
```

Trigger a decision:

```bash
curl -X POST http://127.0.0.1:4749/deterministiclightagent \
  -H "Content-Type: application/json" \
  -d '{"apply_to_lumen_service": true}'
```

## Running MCP Services

Start logger backend:

```bash
cd /home/johannesl/Papers/AgenticFundamentals/SimpleAgenticScenario/mcp/logger
ruby server.rb
```

Start MCP tool servers:

```bash
cd /home/johannesl/Papers/AgenticFundamentals/SimpleAgenticScenario/mcp/light_sim_johannes
ruby -rsinatra -e 'set :port, 4567; load "mcp_light.rb"'
```

```bash
cd /home/johannesl/Papers/AgenticFundamentals/SimpleAgenticScenario/mcp/wait
ruby mcp_sleep.rb
```

```bash
cd /home/johannesl/Papers/AgenticFundamentals/SimpleAgenticScenario/mcp/logger
ruby mcp_logger.rb
```

Run an orchestration experiment prompt:

```bash
cd /home/johannesl/Papers/AgenticFundamentals/SimpleAgenticScenario/mcp
./oo1.sh
# or
./oo3.sh
# or
./ootest.sh
```

Direct invocation:

```bash
cd /home/johannesl/Papers/AgenticFundamentals/SimpleAgenticScenario/mcp
ruby agent.rb "Your instruction prompt here"
```

## Evaluation Scripts

From `EvalHelper/`:

- `eval_sensor_log.py`: evaluate `simulator_static.log` directly
- `compare_log_with_csv.py`: compare process/YAML logs against CSV ground truth
- `compare_log_with_csv_mcp.py`: compare MCP logs against CSV ground truth
- `compare_mcplog_with_gt.py`: align MCP and simulator logs, compute precision/recall/F1
- `recalculate_gt_ranges.py`: recompute and validate GT lumen ranges in dataset CSV
- `filter.py`: filter XES-YAML events by scenario-relevant labels/transitions

Example:

```bash
cd /home/johannesl/Papers/AgenticFundamentals/SimpleAgenticScenario/EvalHelper
python eval_sensor_log.py ../Simulators/simulator_static.log --report report.csv
```

Example (GT range validation only):

```bash
python recalculate_gt_ranges.py ../Simulators/artificial_week_sensor_dataset_no_user_input.csv
```

Example (rewrite GT range in place):

```bash
python recalculate_gt_ranges.py \
  ../Simulators/artificial_week_sensor_dataset_no_user_input.csv \
  ../Simulators/artificial_week_sensor_dataset_no_user_input.csv
```

## Metric Calculation Scripts

From `EvalHelper/MetricCalculation/`:

- `evaluate_cyclomatic_cfg.py`: cyclomatic complexity via explicit CFG construction
- `evaluate_cyclomatic_direct.py`: cyclomatic complexity via structural counting
- `evaluate_ABC.py`: ABC vector/scalar for process descriptions
- `evaluate_all.py`: runs all metric calculations and compares outputs

Examples:

```bash
cd /home/johannesl/Papers/AgenticFundamentals/SimpleAgenticScenario/EvalHelper/MetricCalculation
python evaluate_all.py /path/to/process.xml
```

```bash
python evaluate_ABC.py /path/to/process1.xml /path/to/process2.xml
```

## Environment Variables

For `agent.py`:

- `SIMULATOR_BASE_URL` (default: `http://127.0.0.1:8001`)
- `OPENAI_MODEL` (default: `gpt-4o-mini`)
- `OPENAI_API_KEY` (optional; if unset, heuristic mode is used)

## Notes

- In the current scripts, the most reliable way to run Python services is with `--foreground`.
- Several scripts produce logs/reports in-place under `EvalHelper/`, `Simulators/`, and `mcp/`.
- Use separate terminals for each long-running service.
