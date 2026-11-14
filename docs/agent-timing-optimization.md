# Phase 15 — Timing-aware Agent optimization (opt-in)

The default Agent loop keeps the original objective: minimize Yosys *generic cell count* after all six sealed experiment gates pass. Phase 15 adds an optional, separate **Liberty-mapped area objective subject to pre-layout setup timing**.

## Usage

```bash
python3 -m chiprd.cli agent-loop \
  --agent-command-json '["python3","examples/agents/llm_agent.py"]' \
  --ppa-objective \
  --liberty /path/to/NangateOpenCellLibrary_typical.lib \
  --tech-lef /path/to/NangateOpenCellLibrary.tech.lef \
  --cells-lef /path/to/NangateOpenCellLibrary.macro.lef \
  --clock-period-ns 10 \
  --io-delay-ns 0.5 \
  --max-iterations 2 \
  --max-wall-clock-seconds 3600 \
  --output artifacts/agent-loops/ppa-run-001
```

The external model needs explicit configuration (see the regular Agent-loop guide). The benchmark does not ship an API key, hosted model or private evaluation service.

1. The baseline is evaluated under the existing sealed six-gate workflow.
2. **Only after those gates pass**, map with Yosys and run OpenSTA with the same Liberty, LEFs and SDC for candidate and Golden reference. Baseline is also evaluated; a failed or missing baseline PPA leaves no automatically selected baseline.
3. Feed area, WNS, timing feasibility, candidate hash and report hash to the model as independent evaluator feedback.
4. Select a candidate only when sealed gates pass, the timing report validates, and setup WNS is nonnegative. Among eligible designs, select the smallest mapped area. The baseline remains eligible; do not call a candidate an improvement unless its area is strictly smaller than baseline.
5. Preserve `artifacts/agent-loops/ppa-run-001/ppa/*/timing_report.json`, mapped Verilog and OpenSTA logs alongside sealed experiment and Agent ledger. Report `objective_mode` differentiates the new mode from legacy generic cells.

Failures or unavailable PPA tools are **not** treated as feasible. Agent proposals are still not trusted as evidence. The model may see public PPA feedback for subsequent iterations, so this is not a private holdout. It does not replace the post-selection independent numerical audit. No automated security boundary prevents a locally executed third-party Agent from inspecting the local environment; run untrusted Agents in a suitable sandbox.

**Important budget limit:** the controller checks its wall-clock deadline before launching each PPA evaluation, but each underlying Yosys/OpenROAD subprocess uses its own timeout. The optional PPA mode therefore does not yet guarantee a *hard* aggregate wall-clock stop while EDA is running. Use conservative iteration limits and an outer process/container timeout when needed.

**Evidence boundary:** Nangate45 Liberty synthesis area + ideal-clock, pre-layout OpenSTA setup slack are not physical PPA, clock-tree closure, post-route STA, signoff, Fmax, or power estimates. Different Liberty, SDC and toolchain configurations must not be compared as the same benchmark. The Stage 14 scorecard supports strict provenance matching for offline comparisons.

## Tests

```bash
python3 -m unittest discover -s tests -p 'test_agent_ppa_loop.py' -v
python3 -m unittest discover -s tests -v
```
