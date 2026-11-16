module tb_int_mul16;
    logic signed [15:0] a, b;
    logic signed [31:0] product;
    integer fd, rc, idx;
    int_mul16 dut(.a(a), .b(b), .product(product));
    initial begin
        fd = $fopen("vectors.txt", "r");
        if (fd == 0) $fatal(1, "cannot open vectors");
        idx = 0;
        while (!$feof(fd)) begin
            rc = $fscanf(fd, "%h %h\n", a, b);
            if (rc == 2) begin
                #1;
                $display("OUT %0d %0d", idx, product);
                idx = idx + 1;
            end
        end
        $fclose(fd);
        $finish;
    end
endmodule
