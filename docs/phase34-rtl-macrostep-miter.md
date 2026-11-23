# Phase 34: RTL-backed CORDIC macrostep miter

The original Phase 33 lemma constructs both sides from the same abstract
microstep rule and therefore cannot detect an RTL frontend or translation
mismatch. This experiment independently extracts the actual combinational
wire expressions and angle tables from Golden and generated RTL, elaborates
each datapath using Yosys, and asks Yosys SAT for a counterexample to their
full-width (32-bit) macrostep output equality.

## Invocation

With the pinned OSS CAD Suite in PATH:

```bash
python -m unittest tests.test_cordic_rtl_macrostep -v
python -m chiprd.cordic_rtl_macrostep \
  --output artifacts/architecture/rtl-macrostep \
  --timeout-seconds 90
```

There are 8 legal boundaries for Iter2 and 4 for Iter4. Every boundary uses
unconstrained 32-bit x/y/z initial state, and the candidate iteration base is
fixed at the corresponding legal boundary. Each saved miter is a standalone
SystemVerilog file, and each solver run writes its own log and SHA-256
fingerprints. Only explicit Yosys SAT **SUCCESS** with no counterexample is
accepted as PASS; tool failures/timeouts are never promoted.

This is stricter than composing two copies of the same Python bitvector model:
the extracted expressions are independently read and lowered by the Verilog
frontend. Nonetheless, it is still *only* a combinational datapath comparison.
The generator currently checks exact RTL expression signatures; any code
change outside that supported subset requires a reviewed extractor update.
This does not establish the actual sequential state mapping, busy/reset,
transaction acceptance or output timing.

The existing PR #33 end-to-end EQY check is mandatory and remains red while
its x_out/y_out proofs are inconclusive. Do not merge the architecture
candidate based on this macrostep result.
