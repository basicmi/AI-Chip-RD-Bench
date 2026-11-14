# Phase 16 — Repeatable LLM + PPA campaigns

Use a local or remote **OpenAI-compatible Chat Completions** model to propose RTL and collect controlled, independent optimization runs. This new command does not make model calls unless you explicitly configure a model Agent; the CI/unit tests use offline fixtures. It does **not** claim a successful live-model optimization.

## Preparation

Supply Nangate45 or your own Liberty and LEFs, Yosys, OpenROAD/OpenSTA and the six original sealed-gate dependencies (Verilator, SymbiYosys, EQY, etc.). Use a trusted isolated environment: an Agent is a locally executed process; read the provider's privacy/cost policy when using hosted inference.

```bash
export CHIPRD_LLM_BASE_URL=http://127.0.0.1:8000/v1
export CHIPRD_LLM_MODEL=your-openai-compatible-model
# For a remote HTTPS endpoint, optionally:
# export CHIPRD_LLM_API_KEY=... (never put this in CLI args or tracked files)

python3 -m chiprd.cli ppa-campaign \
  --agent-command-json '["python3","examples/agents/llm_agent.py"]' \
  --liberty /path/to/NangateOpenCellLibrary_typical.lib \
  --tech-lef /path/to/NangateOpenCellLibrary.tech.lef \
  --cells-lef /path/to/NangateOpenCellLibrary.macro.lef \
  --clock-period-ns 10 --io-delay-ns 0.5 \
  --runs 3 --max-iterations 2 \
  --max-agent-seconds 120 --max-experiment-seconds 600 \
  --max-wall-clock-seconds 3600 \
  --seed 20261113 --campaign-id model-a-run1 \
  --output artifacts/ppa-campaigns/model-a-run1
```

## Evidence

Every run begins from the same checked-in Golden CORDIC source, seed, model adapter and budget. Candidate ranking requires **all six sealed functional/equivalence/formal gates** and nonnegative setup WNS. Ranking uses **Liberty-mapped area**, not Yosys generic cell count. A candidate's area improvement is measured against the same independently evaluated Golden baseline. Missing EDA evidence fails closed; missing token usage remains unknown.

The campaign produces:
- `campaign_manifest.json`: task/baseline hashes, controlled settings, model ID and credential-free origin, PPA input hashes.
- `runs/run-NNN/agent_loop_report.json` and EDA/Agent evidence: per-candidate outcomes and logs.
- `campaign_report.json`: per-run best mapped area, area reduction, WNS, candidate pass rates, failure categories, provider-reported token statistics and descriptive distributions.

Do not compare campaigns on disparate model/EDA settings, different tasks, or library/SDC constraints. Repeat-run mean/stddev are descriptive and do not establish statistically significant superiority. The manifest records declared settings; it does not cryptographically attest to a remote provider, runtime immutability or toolchain identity across runs.

**Known limits:** PPA subprocess timeouts are independent of the Agent's overall wall-clock budget. An outer process timeout is recommended. Public benchmark gates are optimizable by the Agent and not a blind test. Use the separate post-selection audit for additional sampled numerical checks. Ideal-clock pre-layout STA and synthesized cell area are not routed/signoff PPA, power or silicon performance. Agent stdout and RTL proposals can include sensitive content; keep artifacts private unless reviewed and scrubbed.

## Test

```bash
python3 -m unittest discover -s tests -p 'test_ppa_campaign.py' -v
python3 -m unittest discover -s tests -v
```
