# Phase 26 — Generic Liberty mapping for combinational IP

Phase 26 removes the hard-coded CORDIC top-module assumption from the
Liberty-mapped area evaluator. The default remains `cordic_rotator`, preserving
existing CORDIC commands and reports. Other tasks may supply a validated HDL
identifier with `--top-module`.

## Multiplier mapping

The CI flow maps the experimental signed 16x16 multiplier against the pinned
Nangate45 typical Liberty using:

```bash
python3 -m chiprd.cli ppa \
  --candidate tasks/multiplier/int_mul16.sv \
  --no-reference \
  --top-module int_mul16 \
  --liberty NangateOpenCellLibrary_typical.lib \
  --platform-label nangate45-reference \
  --output artifacts/ppa/nangate45-multiplier
```

This produces the ordinary `ppa_report.json`, Yosys mapping log and mapped
netlist. It is a technology-mapped synthesis area estimate only. It is not
placed/routed area, timing, power or signoff. The public Nangate45 library is a
reproducible reference technology, not a modern production process.

## Scope and next gates

This phase deliberately does not generalize OpenSTA timing. A combinational
multiplier has no clock port; it needs an explicit input-to-output delay
constraint model rather than inheriting CORDIC's clock/reset/valid SDC.

The multiplier remains experimental until a separate combinational timing
contract, independent post-selection audit and task-aware scorecard exist.
Do not compare its raw area directly with CORDIC's sequential implementation.
