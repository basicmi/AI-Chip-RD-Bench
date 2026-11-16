# Phase 25 — Multiplier combinational proof and mutation gates

Phase 25 adds evaluator types that match a combinational arithmetic IP rather
than reusing the sequential CORDIC proof contract.

## Commands

```bash
python3 -m chiprd.cli multiplier-eval \
  --candidate tasks/multiplier/int_mul16.sv \
  --output artifacts/multiplier/functional

python3 -m chiprd.cli multiplier-formal \
  --candidate tasks/multiplier/int_mul16.sv \
  --output artifacts/multiplier/formal

python3 -m chiprd.cli multiplier-mutation \
  --candidate tasks/multiplier/int_mul16.sv \
  --audit-vectors 128 \
  --output artifacts/multiplier/mutation
```

## Combinational formal semantics

The formal evaluator builds a Yosys equivalence miter between the immutable
multiplier Golden RTL and the submitted candidate, then runs
`equiv_make`, `equiv_simple`, and `equiv_status -assert`. The proof covers
all input combinations at the declared top-level combinational interface,
assuming the SystemVerilog frontend and Yosys equivalence model. It does not
reuse CORDIC's clock, reset, handshake, bounded-latency or sequential EQY
contract.

A proof is reported as equivalent only when Yosys exits successfully and emits
its explicit success marker. An unproven miter is a failure; missing tools,
timeouts or ambiguous logs are errors/inconclusive, never passes.

## Mutation sensitivity

The public mutation campaign injects three deterministic faults:
- Replace multiplication with addition.
- Shift the product right by one.
- Treat signed operands as unsigned.

Each mutant must still compile and pass Yosys generic synthesis, while the
independent Verilator/Python arithmetic oracle must reject its behavior.
Compilation or synthesis failures do **not** count as a detected mutation.
The report includes the baseline and mutant hashes, seed, vector budget and
per-mutant outcome.

This is a small, explicit mutation set, not hidden-test coverage or a claim of
complete arithmetic correctness.

## CI and maturity

The main Phase 0 workflow now runs the multiplier functional evaluator,
combinational formal proof and mutation sensitivity checks in addition to the
existing CORDIC gates. Evidence is uploaded with the CI artifacts.

The multiplier remains **experimental**: it does not yet have a task-specific
Liberty-mapped PPA + timing flow, independent post-selection audit, or a
verified end-to-end Agent campaign. Generic Yosys synthesis is not PPA.
Do not pool multiplier and CORDIC scores or promote the task to fully runnable
until those gates and their task-aware scorecard are implemented.

## Tests

```bash
python3 -m unittest discover -s tests -p 'test_multiplier_validation.py' -v
python3 -m unittest discover -s tests -p 'test_task_adapters.py' -v
python3 -m unittest discover -s tests -v
```
