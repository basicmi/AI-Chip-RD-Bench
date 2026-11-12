# CORDIC Rotation — Golden Task v1

**Interface**: `cordic_rotator(clk, rst, input_valid, angle_in, output_valid, x_out, y_out)`

- All signals synchronous to rising clock; reset `rst` is synchronous, active-high.
- `angle_in` is signed Q3.13 radians. Valid task input range: `[-round(pi/2*8192), +round(pi/2*8192)]`.
- On `input_valid` while idle, device accepts a request.
- Requests submitted while busy may be ignored.
- `output_valid` is asserted for one cycle on completion.
- `x_out, y_out` are signed Q2.14 approximations of `cos(angle)` and `sin(angle)`.
- For the default task, each output must be within **32 LSB** of the independently calculated analytic result.
- No throughput or fixed latency is promised beyond completion within 32 clocks for the golden 16-stage sequential implementation.
- Pure SystemVerilog RTL; no proprietary IP or external memory required.

The example implementation is *not* a formal functional proof or production-ready silicon IP. Golden verification uses an independent analytic math oracle. Published tests are open examples, not secret tests.

## Evidence level

Phase 0 runs independent functional simulation and technology-independent Yosys synthesis. The resulting `generic_cell_count` is **not** cell area, frequency, power, physical closure, or sign-off.
