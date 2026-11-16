# Phase 24 — Task adapter foundation and experimental multiplier Golden

Phase 23 made task maturity explicit. This phase adds a registry-backed
`TaskDefinition` loader and a second, deliberately **experimental** task to
exercise a different RTL shape: a combinational signed multiplier.

## Inspect task definitions

```bash
python3 -m chiprd.cli task-list
python3 -m chiprd.cli task-validate
```

The shared adapter resolves task identity, track, maturity, interface,
reference/Golde RTL and evaluator declarations from `tasks/registry.json`.
It is metadata plumbing, not yet a universal Agent/EDA runtime: the existing
CORDIC Agent/PPA campaign remains CORDIC-specific.

## Run the multiplier experiment

Install Verilator and Yosys, then:

```bash
python3 -m chiprd.cli multiplier-eval \
  --candidate tasks/multiplier/int_mul16.sv \
  --output artifacts/multiplier-smoke
```

The evaluator checks all 72 signed boundary/edge pairs plus 2,048 seeded
random vectors by default against Python's independent arbitrary-precision
integer multiplication. It then runs Yosys generic synthesis and preserves
compiler, simulator and synthesis logs with a JSON report.

The output is a functional/synthesis smoke experiment, **not** a PPA result.
Generic synthesis statistics are not comparable with CORDIC's Nangate45 mapped
area and timing measurements.

## Maturity boundary

| Task | Maturity | Available checks |
| --- | --- | --- |
| CORDIC Q14 rotation | Runnable | Six public gates + pre-layout PPA |
| Signed 16x16 multiplier | Experimental | Verilator oracle + Yosys generic synthesis |
| Legacy IP reimplementation | Planned | None |
| IP porting | Planned | None |

The multiplier is not promoted to `runnable` until protocol/formal semantics
(if applicable), equivalence, mutation and negative controls, mapped PPA,
independent post-selection audit, and reproducible scorecard integration are
implemented. Do not pool its metrics with CORDIC.

## Tests

```bash
python3 -m unittest discover -s tests -p 'test_task_adapters.py' -v
python3 -m unittest discover -s tests -p 'test_multiplier_harness.py' -v
python3 -m chiprd.multiplier_harness --output artifacts/multiplier-smoke
python3 -m unittest discover -s tests -v
```
