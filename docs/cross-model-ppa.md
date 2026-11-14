# Phase 17 — Audited cross-model PPA comparison

This workflow compares **archived** Phase 16 PPA campaigns from explicitly configured model-backed RTL Agents. It does not contact a model provider or rerun EDA. No real model results are bundled with the repository.

## Run the experiments

From the repository root, prepare a local vLLM endpoint or a remote OpenAI-compatible endpoint. Set the model before **each** separate campaign, while preserving the same EDA platform, task, seed, generation settings and budgets.

```bash
export CHIPRD_LLM_BASE_URL=http://127.0.0.1:8000/v1
export CHIPRD_LLM_TEMPERATURE=0
export CHIPRD_LLM_MAX_TOKENS=4096
export CHIPRD_LLM_TIMEOUT_SECONDS=90

export CHIPRD_LLM_MODEL=model-A
python3 -m chiprd.cli ppa-campaign \
  --agent-command-json '["python3","examples/agents/llm_agent.py"]' \
  --liberty "$LIBERTY" --tech-lef "$TECH_LEF" --cells-lef "$CELLS_LEF" \
  --clock-period-ns 10 --io-delay-ns 0.5 \
  --runs 3 --max-iterations 2 --seed 20261113 \
  --max-wall-clock-seconds 3600 --max-agent-seconds 120 \
  --max-experiment-seconds 600 \
  --campaign-id study-model-a --output artifacts/ppa-campaigns/model-a

export CHIPRD_LLM_MODEL=model-B
python3 -m chiprd.cli ppa-campaign \
  --agent-command-json '["python3","examples/agents/llm_agent.py"]' \
  --liberty "$LIBERTY" --tech-lef "$TECH_LEF" --cells-lef "$CELLS_LEF" \
  --clock-period-ns 10 --io-delay-ns 0.5 \
  --runs 3 --max-iterations 2 --seed 20261113 \
  --max-wall-clock-seconds 3600 --max-agent-seconds 120 \
  --max-experiment-seconds 600 \
  --campaign-id study-model-b --output artifacts/ppa-campaigns/model-b
```

These are **examples, not completed experiments**. Real runs require functioning model inference and the full existing CORDIC simulation, formal and PPA toolchain.

Then compare the two archives without any model/EDA calls:

```bash
python3 -m chiprd.model_comparison \
  artifacts/ppa-campaigns/model-a \
  artifacts/ppa-campaigns/model-b \
  --output artifacts/cross-model-study.json
```

## Verification policy

The comparator rejects missing and modified archived evidence; validates both campaign manifests and per-run report hashes; recomputes run-level PPA selection and aggregated metrics from archived loop reports; and demands identical benchmark task/baseline, PPA input hashes and SDC assumptions, evaluator/controller code fingerprints, seed, run count, budget, and stated generation parameters. Model IDs may differ but must be present and distinct. A campaign with failed runs is marked **partial**, not ranked alongside complete runs.

Each row includes model ID, runs with valid PPA evidence, improving-run count, mean area reduction, mean PPA feasibility rate, and **reported** tokens with missing-usage counts. No winner is declared, even when a mean differs: with only a few repeats, those are descriptive results, not evidence of statistical significance. The comparison does not attest to hosted model identity, exact tool binaries, runtime hardware, model sampling determinism, cost, silicon speed or physical signoff.

**Compatibility:** Phase 16 campaign manifests made before Phase 17 lack required evaluator fingerprints and are explicitly rejected for strict cross-model comparison. Rerun them under Phase 17 to obtain comparable evidence rather than silently mixing schemas.

**Privacy:** never publish raw provider/Agent artifacts without review. Model prompts, responses and generated source can contain proprietary or sensitive information. Use an isolated runner for untrusted Agents and manage API credentials only via environment configuration.

## Regression tests

```bash
python3 -m unittest discover -s tests -p 'test_model_comparison.py' -v
python3 -m unittest discover -s tests -v
```
