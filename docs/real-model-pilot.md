# Phase 18 — First real-model PPA pilot: readiness and protocol

**Goal:** run the first measured, reproducible CORDIC RTL optimization study using the existing model-backed Agent. There are no measured results in this repository until the commands below are explicitly executed on a provisioned machine.

## Why Phase 18 changes the Agent adapter

Previously, the existing Chat Completions Agent always instructed the model to *minimize Yosys generic cells*, even when the evaluator selected the opt-in **Liberty-mapped area subject to WNS ≥ 0** objective. Phase 18 now sends one of two audited instructions chosen from the controller's supported objective schema. It refuses unknown or corrupted objective fields rather than blindly incorporating untrusted strings. The six sealed public gates and the precise task spec remain unchanged.

## Recommended pilot controls

For a first measurement, use **two code-capable model IDs** on the same OpenAI-compatible local inference server, with a fixed temperature (0), max generation tokens (4096), seed (20261113), 3 independent runs per model, 2 optimization iterations per run and identical EDA settings. Prefer an isolated runner and pinned versions. Start with models that fit your hardware; do not assume the model server guarantees sampling determinism. Repeating a run with the same seed is not a substitute for independent statistical samples.

Only **the model ID** should differ between the two campaigns. Keep model service endpoint, task, EDA tools, library files, exact SDC, budgets and input RTL the same. If model-specific inference settings cannot match, treat the results as descriptive *non-comparable* experiments instead.

## 1. Configure and preflight (offline; no provider calls)

```bash
export LIBERTY=/absolute/path/NangateOpenCellLibrary_typical.lib
export TECH_LEF=/absolute/path/NangateOpenCellLibrary.tech.lef
export CELLS_LEF=/absolute/path/NangateOpenCellLibrary.macro.lef

export CHIPRD_LLM_BASE_URL=http://127.0.0.1:8000/v1
export CHIPRD_LLM_MODEL=model-A
export CHIPRD_LLM_TEMPERATURE=0
export CHIPRD_LLM_MAX_TOKENS=4096
export CHIPRD_LLM_TIMEOUT_SECONDS=90

python3 -m chiprd.pilot_preflight \
  --liberty "$LIBERTY" --tech-lef "$TECH_LEF" --cells-lef "$CELLS_LEF" \
  --clock-period-ns 10 --io-delay-ns 0.5 \
  --output artifacts/pilot-preflight-model-a.json
```

Preflight checks model configuration using the **same adapter validator** as live inference, executable presence (Python 3, git, Verilator, Yosys, OpenROAD, EQY, SBY, Z3), bounded regular Liberty/LEF inputs and the checked-in task and RTL. It records the model ID and endpoint **origin only**, not the API key or URL path. It does **not** test endpoint reachability, PDK semantic compatibility, tool versions, or live EDA. A passing preflight is an environment readiness indication, **not a guarantee the study will complete**. Use a new output file name for each preflight.

For remote endpoints use HTTPS and set `CHIPRD_LLM_API_KEY` only in the execution environment. Do not commit tokens or include them in shell arguments or report files. Inspect all raw Agent artifacts for sensitive data before publishing.

## 2. Run the two PPA campaigns (explicit network/model and EDA work)

```bash
python3 -m chiprd.cli ppa-campaign \
  --agent-command-json '["python3","examples/agents/llm_agent.py"]' \
  --liberty "$LIBERTY" --tech-lef "$TECH_LEF" --cells-lef "$CELLS_LEF" \
  --clock-period-ns 10 --io-delay-ns 0.5 \
  --runs 3 --max-iterations 2 --seed 20261113 \
  --max-agent-seconds 120 --max-experiment-seconds 600 \
  --max-wall-clock-seconds 3600 \
  --campaign-id pilot-model-a \
  --output artifacts/ppa-campaigns/pilot-model-a

# Switch the model ID to a second supported model, without changing controls.
export CHIPRD_LLM_MODEL=model-B

python3 -m chiprd.pilot_preflight \
  --liberty "$LIBERTY" --tech-lef "$TECH_LEF" --cells-lef "$CELLS_LEF" \
  --output artifacts/pilot-preflight-model-b.json

python3 -m chiprd.cli ppa-campaign \
  --agent-command-json '["python3","examples/agents/llm_agent.py"]' \
  --liberty "$LIBERTY" --tech-lef "$TECH_LEF" --cells-lef "$CELLS_LEF" \
  --clock-period-ns 10 --io-delay-ns 0.5 \
  --runs 3 --max-iterations 2 --seed 20261113 \
  --max-agent-seconds 120 --max-experiment-seconds 600 \
  --max-wall-clock-seconds 3600 \
  --campaign-id pilot-model-b \
  --output artifacts/ppa-campaigns/pilot-model-b
```

The provider may charge for API calls. EDA tools and model infrastructure must already be provisioned. An outer shell/container timeout is recommended: the Agent's aggregate wall-clock deadline is checked between EDA calls and cannot forcibly stop a running EDA subprocess.

## 3. Offline cross-model comparison

```bash
python3 -m chiprd.model_comparison \
  artifacts/ppa-campaigns/pilot-model-a \
  artifacts/ppa-campaigns/pilot-model-b \
  --output artifacts/pilot-model-comparison.json
```

The comparison must reject mismatched provenance, task, Liberty/LEF/SDC inputs, controller/evaluator fingerprints, budgets and generation settings; each measured candidate must pass the six public correctness gates and pre-layout setup timing. Results should report: successful runs, percent of runs improving area relative to Golden, mean/median mapped-area reduction, timing feasibility rate and reported token usage with unknown data distinguished.

**Interpretation:** Do not publish a 'winning model' from three runs, treat generic cells as real area, or treat a setup WNS ≥ 0 under ideal clocks as physical signoff. This public benchmark may be overfit by the Agent; the independent-seed numerical audit remains a separate post-selection validation step.

## 4. Regression and review

```bash
python3 -m unittest discover -s tests -p 'test_llm_agent.py' -v
python3 -m unittest discover -s tests -p 'test_pilot_preflight.py' -v
python3 -m unittest discover -s tests -v
```

CI must run only mocked model adapter and offline preflight tests; it must never make paid provider calls or claim empirical model improvement. For the real pilot, preserve full logs, source fingerprints and model configuration and keep raw artifacts private until reviewed.
