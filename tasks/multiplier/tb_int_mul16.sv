module tb_int_mul16;
    logic signed [15:0] a, b;
    logic signed [31:0] product;
    integer fd, rc, idx;
    integer raw_a, raw_b;

    int_mul16 dut(.a(a), .b(b), .product(product));

    initial begin
        fd = $fopen("vectors.txt", "r");
        if (fd == 0) $fatal(1, "cannot open vectors");
        idx = 0;
        while (!$feof(fd)) begin
            rc = $fscanf(fd, "%h %h", raw_a, raw_b);
            if (rc == 2) begin
                a = raw_a[15:0];
                b = raw_b[15:0];
                #1;
                $display("OUT %0d %08x", idx, product);
                idx = idx + 1;
            end else if (rc != -1) begin
                $fatal(1, "malformed vector input at index %0d (fscanf rc=%0d)", idx, rc);
            end
        end
        $fclose(fd);
        $finish;
    end
endmodule
