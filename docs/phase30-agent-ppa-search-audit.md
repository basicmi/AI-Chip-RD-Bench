# Phase 30 — multi-round PPA-aware Agent search and independent audit

Phase 30 connects the existing bounded Agent loop, timing-constrained PPA
objective and repeated-campaign runner into one reproducible CI experiment.

## What CI now exercises

1. Two independent runs start from the same immutable CORDIC Golden RTL.
2. Each run allows three Agent turns. The deterministic CI fixture submits a
   behavior-preserving first-generation patch, receives sealed-gate + PPA
   feedback, submits a second equivalent candidate with a different expression
   ordering, receives the second feedback record, then stops. This exercises
   two candidate generations and lineage hand-off without credentials or a live LLM.
3. Every candidate must pass all six sealed public gates before PPA is measured.
4. The PPA objective is minimum Nangate45 Liberty-mapped area subject to
   non-negative pre-layout OpenSTA setup slack, using the same library, LEFs,
   clock and I/O constraints for baseline and candidate.
5. After the campaign is complete, a separate command verifies the campaign
   manifest, each loop-report hash and each selected candidate's archived
   experiment evidence. It then audits every selected candidate with fresh
   Verilator/oracle vectors and a seed distinct from the search seed.
6. Audit results are written separately and are never sent back to the Agent.

## Reproduce

The workflow uses `examples/agents/multi_round_mock_agent.py` to exercise two
candidate generations; the original single-patch mock fixture remains unchanged.
The workflow runs the campaign and audit automatically. To run locally after
installing the pinned OSS CAD Suite, Verilator, OpenROAD and the pinned
Nangate45 Liberty/LEF inputs:

```bash
source /path/to/oss-cad-suite/environment
python3 -m chiprd.cli ppa-campaign \
  --agent-command-json '["python3","examples/agents/multi_round_mock_agent.py"]' \
  --liberty /path/to/NangateOpenCellLibrary_typical.lib \
  --tech-lef /path/to/NangateOpenCellLibrary.tech.lef \
  --cells-lef /path/to/NangateOpenCellLibrary.macro.lef \
  --runs 2 --max-iterations 2 \
  --output artifacts/agent-ppa-campaigns/local \
  --campaign-id local-phase30 --seed 20261116

python3 -m chiprd.cli ppa-campaign-audit \
  --campaign-dir artifacts/agent-ppa-campaigns/local \
  --audit-seed 991977 --audit-vectors 128
```

Choose the audit seed only after candidate selection in real studies. The
example value is illustrative. The CI seed is public and is a regression
fixture, not a secret holdout.

## Output

- `campaign_manifest.json`: baseline, seed, budget, PPA input fingerprints
  and Agent configuration (credentials are not captured).
- `campaign_report.json`: per-run loop report hashes, selected candidates,
  timing/area evidence and aggregate descriptive statistics.
- `runs/run-NNN/`: full Agent loop, sealed experiment, patch and PPA evidence.
- `post_selection_audit/run-NNN/audit_report.json`: independent numerical
  audit for each selected candidate.
- `post_selection_audit/audit_summary.json`: terminal audit summary bound to
  campaign manifest and report hashes.

## Interpretation and limits

A passing campaign is evidence that the configured Agent/controller can
complete the bounded optimization workflow under this task and toolchain. It
does not establish that an LLM is generally capable of chip design. The CI
fixture is deterministic orchestration coverage, not a model-quality result.
The audit is independent of the Agent feedback loop but remains public sampled
verification, not hidden holdout or formal numerical proof. Nangate45 mapped
area and ideal-clock pre-layout STA are not routed PPA, power, or silicon
signoff. Descriptive statistics over two runs are not statistical significance.

The Phase 29 multiplier architecture sweep remains a separate experimental
track; its results are not pooled with sequential CORDIC results.
