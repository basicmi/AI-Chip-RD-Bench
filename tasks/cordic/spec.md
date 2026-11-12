# CORDIC Rotation — Golden Task v1

**Interface**: `cordic_rotator(clk, rst, input_valid, angle_in, output_valid, x_out, y_out)`

- All signals synchronous to rising clock; reset `rst` is synchronous, active-high.
- `angle_in` is signed Q3.13 radians. Valid task input range: `[-round(pi/2*8192), +round(pi/2*8192)]`.
- On `input_valid` while idle, device accepts a request.
- Requests submitted while busy may be ignored.
- `output_valid` is asserted for one cycle on completion.
- `x_out, y_out` are signed Q2.14 approximations of `cos(angle)` and `sin(angle)`.
- For the default task, each output must be within **32 LSB** of the independently calculated analytic result.
- A request must complete within **32 clock cycles**. Implementations may use a different latency within this bound.
- Pure SystemVerilog RTL; no proprietary IP or external memory required.

The example implementation is *not* a complete formal functional proof or production-ready silicon IP. Golden numerical verification uses an independent analytic math oracle. Published tests are open examples, not secret tests.

## Evidence level

- Verilator + analytic oracle: simulation-based numerical correctness on deterministic public vectors.
- SymbiYosys: protocol properties (one-cycle completion pulse, no spurious completion, completion within 32 cycles, output-register stability) under the documented reset assumptions.
- Yosys: generic synthesis only.

The formal protocol proof does **not** prove trigonometric numerical equivalence, physical timing, area, power, CDC, reset recovery/removal, or silicon correctness. The resulting `generic_cell_count` is **not** cell area, frequency, power, physical closure, or sign-off.
