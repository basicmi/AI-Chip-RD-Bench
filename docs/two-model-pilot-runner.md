# Phase 19 — Two-model PPA pilot runner

The command below turns the Phase 16–18 building blocks into **one reproducible, explicit opt-in pilot**. It is a runner, not a measured benchmark result. Unit tests never contact an LLM.

## Configure

From the repository root, provision the full CORDIC EDA toolchain and a local OpenAI-compatible vLLM endpoint (or a remote HTTPS API). Set credentials only via environment variables:

```bash
export CHIPRD_LLM_BASE_URL=http://127.0.0.1:8000/v1
export CHIPRD_LLM_TEMPERATURE=0
export CHIPRD_LLM_MAX_TOKENS=4096
export CHIPRD_LLM_TIMEOUT_SECONDS=90
export LIBERTY=/absolute/path/to/NangateOpenCellLibrary_typical.lib
export TECH_LEF=/absolute/path/to/NangateOpenCellLibrary.tech.lef
export CELLS_LEF=/absolute/path/to/NangateOpenCellLibrary.macro.lef
```

The two model IDs below must resolve to **distinct, available** models on that same endpoint. Test model availability separately: the offline preflight intentionally does not contact the provider. The runner preserves identical public gates, library/SDC inputs, generation parameters, seed and budget across both models.

## 1. Safe dry-run (default)

```bash
python3 -m chiprd.pilot_runner \
  --model-a your-model-a --model-b your-model-b \
  --liberty "$LIBERTY" --tech-lef "$TECH_LEF" --cells-lef "$CELLS_LEF" \
  --pilot-id first-two-models
```

This runs **offline local preflight for both model IDs** and prints a redacted JSON readiness report. It creates no artifacts, sends no tokens to an LLM, and does not execute EDA. Both preflights must pass before a live run can begin.

## 2. Explicit opt-in live experiment

When the endpoint, licensing, cost and privacy conditions are accepted:

```bash
python3 -m chiprd.pilot_runner \
  --model-a your-model-a --model-b your-model-b \
  --liberty "$LIBERTY" --tech-lef "$TECH_LEF" --cells-lef "$CELLS_LEF" \
  --clock-period-ns 10 --io-delay-ns 0.5 \
  --runs 3 --max-iterations 2 --seed 20261113 \
  --max-agent-seconds 120 --max-experiment-seconds 600 \
  --max-wall-clock-seconds 3600 \
  --pilot-id first-two-models --execute
```

The live runner performs both local preflights **before** it creates output directories or runs the first model. It executes a fresh Phase 16 PPA campaign for model A then model B, preserving a unique campaign ID for each. After both complete, it calls the Phase 17 offline cross-model evaluator and writes the comparison if evidence is valid.

Default output:

```text
artifacts/real-model-pilots/first-two-models/
  preflight.json
  model-1/{campaign_manifest.json,campaign_report.json,runs/...}
  model-2/{campaign_manifest.json,campaign_report.json,runs/...}
  comparison.json            # only if both campaign records are comparable
  pilot_report.json
```

The command **refuses to reuse an existing pilot directory**: choose a new pilot ID for every study. If a model call or experiment fails, the report marks the study partial and does not invent a successful comparison or claim a winner.

**Runtime bounds:** the underlying EDA subprocesses can outlive the Agent Loop's aggregate wall-clock budget. For unattended runs, use a separate process/container-level timeout and appropriate resource limits. The CLI's `--execute` is an explicit permission to use model inference (possibly paid) and EDA resources; passing the default dry-run command does **not** grant such permission.

**Evidence and privacy:** Preflight records contain model ID, provider origin and local hashes but do not include API keys or endpoint paths. Full Agent/Campaign artifacts can contain prompts, responses or proprietary RTL; review them before publishing. The existing comparator checks hashes and controlled settings, but neither endpoint identity nor exact EDA binaries are independently attested. The task contains public verification gates, and mapping/WNS are pre-layout estimates, not tape-out signoff.

## Regression tests

```bash
python3 -m unittest discover -s tests -p 'test_pilot_runner.py' -v
python3 -m unittest discover -s tests -v
```
