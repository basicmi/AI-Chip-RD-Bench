# Timing-constrained area scorecards (Phase 14)

This offline evaluator ranks **Liberty-mapped synthesis area** only among candidates meeting the **supplied pre-layout setup timing constraint**. It consumes Phase 13's `timing_report.json` and makes no model calls or EDA reruns.

```bash
python3 -m chiprd.ppa_scorecard \
  artifacts/timing-run-a/timing_report.json \
  artifacts/timing-run-b/timing_report.json \
  --output artifacts/timing-scorecard.json
```

Each input must have `status=pass`, both mapped sides, finite positive area, finite WNS, a reported critical path and internally consistent timing flags and candidate-minus-reference deltas. Reports must agree on exact source reference, Liberty/LEF and SDC hashes, clock/I/O constraints, tool versions, and reference area/WNS. Otherwise evaluation **fails closed**, rather than silently aggregating incomparable runs.

The scorecard preserves each candidate's provenance and two objectives, excludes negative-WNS candidates from the feasible pool, identifies nondominated area/slack points, and selects the lowest-area feasible candidate (stable input-order tie-break). It separately reports whether that selected candidate improves on the reference. A feasible candidate is **not** automatically an improvement. When no candidates meet timing, selection is null.

Limitations: No placement, clock-tree synthesis, wire parasitics, extracted timing, multi-corner signoff, power, or silicon characterization. These scorecards do not replace the separate functional/formal/audit gates. This is not a general area-delay-product or measured-Fmax ranking.

Run focused tests:

```bash
python3 -m unittest discover -s tests -p 'test_ppa_scorecard.py' -v
```
