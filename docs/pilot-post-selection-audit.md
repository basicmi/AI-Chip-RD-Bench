# Phase 21 — Independent post-selection audit of model PPA pilots

The Phase 16–20 flow measures public-gate feasibility, mapped Liberty area and
pre-layout OpenSTA timing during model optimization. **Do not confuse those
selection-time checks with an independent post-selection audit.** This command
runs the already available Verilator/analytic-oracle audit against *each* frozen
best candidate from both model campaigns, after selection is complete.

## Order of operations

1. Run the two-model pilot normally and retain its full evidence directory
   (`pilot_report.json`, both campaigns and all sealed loop archives).
2. After the models finish, **independently choose a new numeric audit seed**.
   It must differ from the search seed; choose it after candidate selection,
   and never disclose results to the optimizing Agent.
3. Run the audit:

```bash
python3 -m chiprd.pilot_audit \
  --pilot-dir artifacts/real-model-pilots/first-two-models \
  --audit-seed 91919 --audit-vectors 256
```

`91919` is only an illustrative seed. To preserve independence, choose your
own seed *after* the experiment has finished, rather than copying this value
into a precommitted study plan.

The command re-verifies the Phase 17 archived campaign report hashes,
selection/PPA evidence and comparability controls **before running** any
fresh Verilator checks. Then it audits every run's selected candidate against
new oracle vectors. A failed or missing candidate never becomes a pass.

## Output and interpretation

```text
<the-pilot-directory>/post_selection_audit/
  audit_progress.json            # only while running
  model-1-run-001/audit_report.json
  model-1-run-002/audit_report.json
  ...
  model-2-run-003/audit_report.json
  audit_summary.json             # terminal summary
```

The final summary includes the independently selected seed, vectors per run,
candidate hash, frozen source-loop report SHA, per-run status and total passes.
It does not replace or modify the original PPA campaign/comparison reports.
It refuses to reuse an existing output directory. An interrupted audit keeps
its progress report; it does not automatically rerun partial checks.

**A complete study requires both:** valid PPA/campaign evidence under the
public six-gate/timing objective, **and** passing post-selection audits.
A PPA-valid run whose independent numerical audit fails must be reported as
a failure of post-selection generalization, not a verified improvement.

**Limits:** this is public audit code using fresh sampled Verilator vectors,
not hidden holdout evaluation, mathematical proof of exact numeric behavior,
routed timing signoff or physical PPA. The controller is not a security
sandbox; isolate untrusted models and inspect raw artifacts for secrets.

## Tests

```bash
python3 -m unittest discover -s tests -p 'test_pilot_audit.py' -v
python3 -m unittest discover -s tests -v
```
