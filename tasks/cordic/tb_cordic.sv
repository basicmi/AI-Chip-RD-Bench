// Evaluator-owned public harness. Commercial/held-out evaluation needs private tests.
module tb_cordic;
    parameter int NUM_VECTORS = 24;
    logic clk = 0;
    always #5 clk = ~clk;
    logic rst = 1;
    logic input_valid = 0;
    logic signed [15:0] angle_in = 0;
    logic output_valid;
    logic signed [15:0] x_out, y_out;
    logic [15:0] vectors [0:NUM_VECTORS-1];
    integer idx, cycles;
    bit found;

    cordic_rotator dut (
        .clk(clk), .rst(rst), .input_valid(input_valid),
        .angle_in(angle_in), .output_valid(output_valid),
        .x_out(x_out), .y_out(y_out)
    );

    initial begin
        $readmemh("vectors.mem", vectors);
        repeat (4) @(negedge clk);
        rst = 0;
        for (idx = 0; idx < NUM_VECTORS; idx = idx + 1) begin
            @(negedge clk);
            angle_in = $signed(vectors[idx]);
            input_valid = 1;
            @(negedge clk);
            input_valid = 0;
            found = 0;
            for (cycles = 0; cycles < 32; cycles = cycles + 1) begin
                @(posedge clk);
                #1;
                if (output_valid) begin
                    $display("OUT %0d %0d %0d", idx, x_out, y_out);
                    found = 1;
                    break;
                end
            end
            if (!found) $display("TIMEOUT %0d", idx);
        end
        $finish;
    end
endmodule
