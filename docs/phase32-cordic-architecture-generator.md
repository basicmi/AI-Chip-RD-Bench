# Phase 32 — CORDIC Architecture Generator

## Objective

Turn the Phase 31 architecture inventory into concrete, reproducible RTL
microarchitectures. The first controlled parameter is the number of CORDIC
micro-iterations unrolled into one compute cycle: 1, 2 or 4.

## Why the response latency is held constant

The current sequential-equivalence gate uses EQY with strict cycle-by-cycle
top-level trace comparison. A 2x or 4x unrolled CORDIC would naturally finish
its arithmetic sooner, changing the externally visible `output_valid` cycle
and failing that contract even when the numerical result is correct.

For this phase, all candidates therefore retain the same 16-active-cycle
response latency as the Golden RTL. The 2x and 4x candidates complete their
arithmetic in 8 and 4 compute cycles respectively, then hold the result until
the common response cycle. This isolates the datapath unroll factor while
preserving the current formal comparison semantics.

This is intentionally a controlled architecture experiment, not a claim of
lower end-to-end latency or higher transaction throughput. The unrolled
datapath is deeper and may increase combinational delay or mapped area; the
Liberty/STA evidence determines whether either implementation is useful.

## Generate and verify

Generate the checked-in RTL:

```bash
python -m chiprd.cli architecture-generate
```

Verify that checked-in generated files exactly match the generator without
modifying the working tree:

```bash
python -m chiprd.cli architecture-generate --check-generated
python -m chiprd.cli architecture-space-validate
```

Generated sources:

- `tasks/cordic/generated/cordic_iter2.sv`
- `tasks/cordic/generated/cordic_iter4.sv`

The generator rejects unsupported factors, duplicate configurations, output
paths outside the repository and symlink traversal. Its output is
deterministic and SHA-256 fingerprints are included in the sealed experiment
evaluator manifest.

## Validation

CI runs each generated candidate through the sealed experiment flow:

1. Verilator plus independent analytic numerical oracle.
2. Protocol formal properties.
3. Formal negative control.
4. Cycle-accurate EQY against the Golden implementation.
5. Mutation sensitivity.
6. Yosys generic synthesis.

The separate Liberty mapping/ideal-clock STA job remains the source for
pre-layout area and timing evidence. These estimates are not routed physical
signoff or power.

## Current search space

| Candidate | Iterations per compute cycle | Arithmetic compute cycles | External response |
|---|---:|---:|---:|
| Golden 1x | 1 | 16 | 16 cycles |
| Generated 2x | 2 | 8 | 16 cycles |
| Generated 4x | 4 | 4 | 16 cycles |

All candidates preserve the same module interface, reset semantics, numeric
algorithm, 32-LSB public tolerance and output-valid cycle.

## Known limitation and next phase

The current benchmark does not yet support latency-insensitive equivalence.
A later phase should add a transaction-level miter that compares accepted
requests and returned values while allowing implementation-specific response
latency within the task's 32-cycle bound. Only after that proof exists should
the benchmark expose genuine latency/throughput tradeoffs as searchable
objectives.
