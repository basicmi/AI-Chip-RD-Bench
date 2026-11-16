// Signed 16x16 radix-4 Booth multiplier with a balanced partial-product tree.
// Experimental architecture candidate; equivalence to the golden task is mandatory.
module int_mul16 (
    input  logic signed [15:0] a,
    input  logic signed [15:0] b,
    output logic signed [31:0] product
);
    logic [18:0] recode_bits;
    logic signed [33:0] multiplicand;
    logic signed [33:0] partial [0:8];
    logic signed [33:0] level1 [0:4];
    logic signed [33:0] level2 [0:2];
    logic signed [33:0] level3 [0:1];
    logic signed [33:0] level4;
    logic signed [33:0] booth_value;
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

        level1[0] = partial[0] + partial[1];
        level1[1] = partial[2] + partial[3];
        level1[2] = partial[4] + partial[5];
        level1[3] = partial[6] + partial[7];
        level1[4] = partial[8];
        level2[0] = level1[0] + level1[1];
        level2[1] = level1[2] + level1[3];
        level2[2] = level1[4];
        level3[0] = level2[0] + level2[1];
        level3[1] = level2[2];
        level4 = level3[0] + level3[1];
        product = level4[31:0];
    end
endmodule
