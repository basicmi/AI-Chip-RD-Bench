# Signed 16x16 Integer Multiplier — Golden Task v1 (experimental)

- Combinational signed two's-complement operands: `a`, `b`, each 16 bits.
- Result: exact signed 32-bit product `product = a * b`.
- No clock, reset, handshake or latency; combinational propagation only.
- Overflow is impossible for the full 16x16-to-32-bit mathematical product.
- Pure SystemVerilog; no proprietary IP or memory macro required.

## Initial evaluator maturity

This experimental task currently has:
- Independent Python integer oracle.
- Verilator vector simulation including corner cases and seeded random inputs.
- Yosys generic synthesis smoke check.

It is **not yet a fully runnable benchmark task**: protocol formal, equivalence,
mutation/negative controls, mapped PPA and post-selection audit are not yet
implemented for this task. Do not compare its scores to the CORDIC task or use
it in the six-gate model ranking until those evaluators are added.
