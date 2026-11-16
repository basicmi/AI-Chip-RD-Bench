# Phase 27 — Clockless combinational timing

## Why a separate timing contract exists

The CORDIC timing flow models a sequential block: a clock, input/output
register boundaries, setup slack and a clock period. The signed 16x16
multiplier is purely combinational. Giving it a fabricated clock would make
the reported number misleading and would mix two different timing semantics.

Phase 27 adds `chiprd.combinational_timing`, which maps a combinational top
with the same pinned Liberty flow and constrains explicit top-level
input-to-output paths using SDC `set_max_delay`. It has no `create_clock`,
reset, launch/capture or cycle-throughput assumptions.

## Reproduce in CI

```bash
python3 -m chiprd.cli comb-timing \
  --candidate tasks/multiplier/int_mul16.sv \
  --top-module int_mul16 \
  --input-ports a,b \
  --output-ports product \
  --liberty NangateOpenCellLibrary_typical.lib \
  --tech-lef NangateOpenCellLibrary.tech.lef \
  --cells-lef NangateOpenCellLibrary.macro.lef \
  --max-delay-ns 10 \
  --input-transition-ns 0.05 \
  --output-load-pf 0.002 \
  --platform-label nangate45-reference \
  --output artifacts/timing/nangate45-multiplier
```

The report records source/library/LEF hashes, tool version, port constraints,
SDC hash, mapped area and the worst input-to-output path. It derives the
estimated path delay as `max_delay - worst_slack` under this explicit
zero-external-delay constraint model.

## Interpretation and limits

- A nonnegative max-delay slack means the constrained path meets the supplied
  max-delay budget under the chosen input transition and output load.
- The result is pre-layout gate-level timing. No placement, routing, SPEF,
  interconnect parasitics, clock tree, process corners or signoff are modeled.
- The reported delay is not a clock frequency, throughput, or silicon timing
  guarantee. Compare results only when the Liberty, LEFs, toolchain, port
  constraints, transition and load are identical.
- This task remains experimental. The timing runner does not independently
  prove functional correctness; formal/equivalence and audit evidence remain
  separate required gates.

## Next

Phase 28 should connect the selected candidate's exact source hash to
independent functional audit, mapped netlist hash, timing report hash and a
single immutable evidence manifest. Only then should a task-aware scorecard
rank multiplier implementations.
