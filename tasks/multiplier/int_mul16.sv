// Signed 16x16 combinational multiplier Golden RTL.
module int_mul16 (
    input  logic signed [15:0] a,
    input  logic signed [15:0] b,
    output logic signed [31:0] product
);
    always_comb product = a * b;
endmodule
