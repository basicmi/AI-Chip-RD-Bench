# Phase 33 proof experiment — source-bound CORDIC macrosteps

This adds a deliberately narrow, executable bitvector lemma ahead of the
expensive sequential EQY proof. It does **not** resolve PR #33's outstanding
`x_out`/`y_out` proof timeouts and must not make that PR mergeable by itself.

## What is checked

For each legal iteration boundary (0, 2, ..., 14 for Iter2; 0, 4, 8, 12
for Iter4), use SMT-LIB QF_BV and Z3 to ask whether arbitrary signed
32-bit `x, y, z` states could produce different outputs after the same
number of Golden microsteps and a candidate macrostep.

- Signed 32-bit arithmetic, wraparound and arithmetic right shifts are modeled.
- All 16 CORDIC constants are checked against the source files.
- Every generated intermediate update expression is checked against a strict,
  explicit source signature before a solver is invoked.
- The source SHA-256 and SMT query SHA-256 are recorded per case.
- A result other than explicit `unsat` is not accepted.
- Unit tests mutate the generated and Golden expressions and require rejection.

Run under a toolchain containing Z3:

```bash
python -m unittest tests.test_cordic_macrostep_proof -v
python -m chiprd.cordic_macrostep_proof --output artifacts/architecture/macrostep-lemma
```

## Evidence boundary

**This is a translation-level algebraic sanity check, not RTL netlist
equivalence.** The candidate formulas are modeled from a fixed source
signature, rather than independently lowered by a Verilog frontend. The
reference and macrostep symbolic expressions share a trusted mathematical
step specification, so the unsatisfiable result confirms the composition
identity of that specification; it must not be counted as an independent
hardware proof.

In particular, this phase does not prove:

1. That the implementation reaches correct boundary states or advances the
   iteration counter as intended.
2. That held states preserve the result during remaining cycles.
3. Reset, busy/input acceptance, output-valid latency, and transaction traces.
4. The actual elaborated RTL netlist against the Golden implementation.

Those remain mandatory obligations of the full sequential EQY experiment.
The next meaningful formal result is an inductive boundary-state relation
**over independently elaborated RTL**, with the step lemma used only as a
supporting obligation. Do not report an architecture candidate as equivalent
until both the RTL transition and interface obligations have passed.
