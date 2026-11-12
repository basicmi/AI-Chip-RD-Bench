// Protocol-only formal contract. Numerical accuracy is checked by the independent simulation oracle.
module cordic_formal(input logic clk);
    (* anyseq *) logic rst;
    (* anyseq *) logic input_valid;
    (* anyseq *) logic signed [15:0] angle_in;
    logic output_valid;
    logic signed [15:0] x_out, y_out;

    cordic_rotator dut (
        .clk(clk), .rst(rst), .input_valid(input_valid), .angle_in(angle_in),
        .output_valid(output_valid), .x_out(x_out), .y_out(y_out)
    );

    reg f_past_valid = 1'b0;
    reg pending = 1'b0;
    reg [5:0] age = 6'd0;
    reg prev_valid = 1'b0;
    reg past_reset = 1'b1;
    reg signed [15:0] prev_x = 16'sd0, prev_y = 16'sd0;

    always @(posedge clk) begin
        f_past_valid <= 1'b1;
        if (!f_past_valid) assume(rst);
        else assume(!rst);

        if (f_past_valid && !rst) begin
            assert(!(prev_valid && output_valid));
            assert(!output_valid || pending);
            // Task contract: implementation-specific latency, bounded by 32 cycles.
            if (pending) begin
                if (output_valid) assert(age <= 6'd32);
                else assert(age < 6'd32);
            end
            if (!past_reset && !output_valid) begin
                assert(x_out == prev_x);
                assert(y_out == prev_y);
            end
        end

        if (rst) begin
            pending <= 1'b0;
            age <= 6'd0;
        end else if (!pending) begin
            if (input_valid) begin
                pending <= 1'b1;
                age <= 6'd0;
            end
        end else if (output_valid) begin
            pending <= 1'b0;
            age <= 6'd0;
        end else begin
            age <= age + 1'b1;
        end

        prev_valid <= output_valid;
        prev_x <= x_out;
        prev_y <= y_out;
        past_reset <= rst;
    end

    always @(posedge clk)
        cover(output_valid);
endmodule
