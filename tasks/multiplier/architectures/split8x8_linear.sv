// Signed 16x16 multiplier decomposed into four signed/unsigned 9x9 products.
// Each 16-bit operand is represented as signed high byte * 256 + unsigned low byte.
module int_mul16 (
    input  logic signed [15:0] a,
    input  logic signed [15:0] b,
    output logic signed [31:0] product
);
    logic signed [8:0] a_hi, b_hi, a_lo, b_lo;
    logic signed [17:0] p_ll, p_hl, p_lh, p_hh;
    logic signed [33:0] term_ll, term_hl, term_lh, term_hh;
    logic signed [33:0] accumulator;

    always_comb begin
        a_hi = {a[15], a[15:8]};
        b_hi = {b[15], b[15:8]};
        a_lo = {1'b0, a[7:0]};
        b_lo = {1'b0, b[7:0]};

        p_ll = a_lo * b_lo;
        p_hl = a_hi * b_lo;
        p_lh = a_lo * b_hi;
        p_hh = a_hi * b_hi;

        term_ll = {{16{1'b0}}, p_ll};
        term_hl = {{16{p_hl[17]}}, p_hl} <<< 8;
        term_lh = {{16{p_lh[17]}}, p_lh} <<< 8;
        term_hh = {{16{p_hh[17]}}, p_hh} <<< 16;

        accumulator = term_ll;
        accumulator = accumulator + term_hl;
        accumulator = accumulator + term_lh;
        accumulator = accumulator + term_hh;
        product = accumulator[31:0];
    end
endmodule
