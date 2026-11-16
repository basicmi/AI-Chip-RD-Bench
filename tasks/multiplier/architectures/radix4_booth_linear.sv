// Signed 16x16 radix-4 Booth multiplier with linear partial-product accumulation.
// Kept separate from the balanced-tree candidate to expose reduction topology tradeoffs.
module int_mul16 (
    input  logic signed [15:0] a,
    input  logic signed [15:0] b,
    output logic signed [31:0] product
);
    logic [18:0] recode_bits;
    logic signed [33:0] multiplicand;
    logic signed [33:0] partial [0:8];
    logic signed [33:0] booth_value;
    logic signed [33:0] accumulator;
    logic [2:0] code;
    integer i;

    always_comb begin
        recode_bits = {{2{b[15]}}, b, 1'b0};
        multiplicand = {{18{a[15]}}, a};
        for (i = 0; i < 9; i = i + 1) begin
            code = recode_bits[2*i +: 3];
            case (code)
                3'b001, 3'b010: booth_value = multiplicand;
                3'b011:         booth_value = multiplicand <<< 1;
                3'b100:         booth_value = -(multiplicand <<< 1);
                3'b101, 3'b110: booth_value = -multiplicand;
                default:        booth_value = '0;
            endcase
            partial[i] = booth_value <<< (2*i);
        end

        accumulator = '0;
        for (i = 0; i < 9; i = i + 1)
            accumulator = accumulator + partial[i];
        product = accumulator[31:0];
    end
endmodule
