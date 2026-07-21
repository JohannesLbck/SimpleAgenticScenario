# SimpleAgenticScenario

This repository contains an experimental agentic lighting-control scenario with:

- Python services for sensor simulation and lighting control
- Ruby MCP tool servers for orchestration-style agent runs
- Evaluation scripts to compare runtime behavior against ground truth and compute process metrics

The project is useful for testing different control styles (LLM-based, heuristic, process-driven) and then quantitatively evaluating decisions.

## Repository Overview

- `Simulators/simulator.py`: dynamic simulator (randomized environment)
- `Simulators/simulator_static.py`: dataset-driven simulator and ground-truth provider
- `ComplexScenario/`: blood-donation registration workflow with a multi-step, agentic decision process
- `EvalHelper/`: log comparison and evaluation utilities
- `EvalHelper/MetricCalculation/`: cyclomatic and ABC metric scripts for process XML files
- `MCP/`: MCP tool servers + orchestration scripts (`eval_oo1.sh`, `eval_oo3.sh`)

## Architecture at a Glance

Simulator, EvalHelper:

1. Simulator exposes sensor endpoints and a lumen actuator endpoint
2. Logs are evaluated with scripts in `EvalHelper`

MCP:

1. MCP servers expose tools (`light`, `sleep`, `log`) which are connected with the python endpoints
2. `MCP/agent.rb` invokes an LLM with these tools
3. Logs are written and compared using `EvalHelper`

## Prerequisites

## Simulator, EvalHelper:

- Python 3.10+
- Virtual environment recommended

Install:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## MCP:

- Ruby 3.4+
  
Install:

```bash
bundler install
```

The MCP LLM scripts expect API credentials in:

- `MCP/api.key` (for `MCP/agent.rb`)
- `MCP/agent_endpoint/api.key` (for `MCP/agent_endpoint/server.rb`)
- `MCP/agent_endpoint_no_tools/api.key` (for `MCP/agent_endpoint_no_tools/server.rb`)

## Ports and Endpoints

Simulator, EvalHelper:

- Static simulator: `http://127.0.0.1:4649`
- Dynamic simulator: `http://127.0.0.1:4648`

Useful simulator endpoints:

- `GET /readsensor`
- `GET /sensor/all`
- `PUT /changelumens` (or `/change_lumens`)
- `GET /changelumens/state`

MCP services (expected by `mcp/agent.rb`):

- Light MCP: `http://localhost:4567/_mcp`
- Sleep MCP: `http://localhost:4568/_mcp`
- Logger MCP: `http://localhost:4569/_mcp`

Additional MCP service ports:

- Logger backend (`MCP/logger/server.rb`): `9091`
- Agent endpoint (`MCP/agent_endpoint/server.rb`): `9092`
- Agent endpoint without tools (`MCP/agent_endpoint_no_tools/server.rb`): `9092`

## Quickstart (Python Flow)

Run static simulator in one terminal:

```bash
cd ~/Papers/AgenticFundamentals/SimpleAgenticScenario/Simulators
source ../.venv/bin/activate
python simulator_static.py --foreground
```

## Quickstart (Ruby Flow)

Start logger backend:

```bash
cd ~/Papers/AgenticFundamentals/SimpleAgenticScenario/mcp/logger
ruby server.rb
```

Start MCP tool servers:

```bash
cd ~/Papers/AgenticFundamentals/SimpleAgenticScenario/mcp/light_sim_johannes
ruby -rsinatra -e 'set :port, 4567; load "mcp_light.rb"'
```

```bash
cd ~/Papers/AgenticFundamentals/SimpleAgenticScenario/MCP/wait
ruby mcp_sleep.rb
```

```bash
cd ~/Papers/AgenticFundamentals/SimpleAgenticScenario/MCP/logger
ruby mcp_logger.rb
```

Run an orchestration experiment prompt:

```bash
cd ~/Papers/AgenticFundamentals/SimpleAgenticScenario/MCP
./do_it.sh eval_oo1
# or
./do_it.sh eval_oo2
# or
./eval.sh
```

Direct invocation:

```bash
cd ~/Papers/AgenticFundamentals/SimpleAgenticScenario/MCP
ruby agent.rb "Your instruction prompt here"
```

## ComplexScenario

Described in its own README in ComplexScenario.

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
cd ~/Papers/AgenticFundamentals/SimpleAgenticScenario/EvalHelper
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
cd ~/Papers/AgenticFundamentals/SimpleAgenticScenario/EvalHelper/MetricCalculation
python evaluate_all.py /path/to/process.xml
```

```bash
python evaluate_ABC.py /path/to/process1.xml /path/to/process2.xml
```

## Environment Variables

For `agent.py`:

- `SIMULATOR_BASE_URL` (default: `http://127.0.0.1:8001`)
- `OPENAI_MODEL` (default: `gpt-4o-mini`)
- `OPENAI_API_KEY`

