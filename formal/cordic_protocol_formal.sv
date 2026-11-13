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
    reg past_reset = 1'b1;

    always @(posedge clk) begin
        f_past_valid <= 1'b1;
        if (!f_past_valid) assume(rst);
        else assume(!rst);

        // Avoid using $past before the first post-reset sample.
        if (f_past_valid && !rst && !$past(rst)) begin
            // A completion pulse is exactly one cycle wide.
            assert(!($past(output_valid) && output_valid));
            // Completion must correspond to a previously accepted request.
            assert(!output_valid || pending);
            // Task contract permits implementation-specific latency, bounded by 32 cycles.
            if (pending) begin
                if (output_valid) assert(age <= 6'd32);
                else assert(age < 6'd32);
            end
            // Registered results may change only on a completion pulse.
            if (!output_valid) begin
                assert(x_out == $past(x_out));
                assert(y_out == $past(y_out));
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
            // The DUT is idle in the cycle output_valid is observed. It may
            // accept the next request on this same edge if input_valid is high.
            pending <= input_valid;
            age <= 6'd0;
        end else begin
            age <= age + 1'b1;
        end
    end

    always @(posedge clk)
        cover(output_valid);
endmodule
