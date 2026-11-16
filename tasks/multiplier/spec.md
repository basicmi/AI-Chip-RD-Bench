# Signed 16x16 Integer Multiplier — Golden Task v1 (experimental)

- Combinational signed two's-complement operands: `a`, `b`, each 16 bits.
- Result: exact signed 32-bit product `product = a * b`.
- No clock, reset, handshake or latency; combinational propagation only.
- Overflow is impossible for the full 16x16-to-32-bit mathematical product.
- Pure SystemVerilog; no proprietary IP or memory macro required.

## Evaluator maturity and architecture exploration

The multiplier remains an experimental, standalone architecture study. The
repository includes three candidates in `tasks/multiplier/architectures.json`:

- `inferred_multiply`: behavioral `a * b` baseline.
- `split8x8_linear`: four signed/unsigned 8x8 partial products with serial accumulation.
- `split8x8_tree`: the same four partial products with balanced reduction.

Each candidate is evaluated with the same seeded Verilator vectors, Yosys
combinational equivalence against the immutable golden RTL, Nangate45 Liberty
mapping, and clockless OpenSTA input-to-output max-delay constraints. The CI
aggregator rejects source-hash mismatches, inconsistent mapped netlists,
different Liberty libraries, or different timing constraints, then reports
area-delay deltas and a Pareto frontier.

The comparison is still an experimental architecture study, not a fully
qualified benchmark track: candidate-specific mutation campaigns, independent
post-selection audit, power measurement, physical implementation, and hidden
holdout evaluation remain future work. Do not compare its scores to the
sequential CORDIC task or include it in six-gate model rankings.
