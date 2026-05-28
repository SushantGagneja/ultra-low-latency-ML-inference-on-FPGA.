`timescale 1ns/1ps

module cosim_tb;

    localparam MAX_VECTORS = 4096;
    localparam MAX_DONE_CYCLES = 64;

    reg clk;
    reg rst_n;
    reg start;
    reg [15:0] spike_vector;
    wire done;
    wire [1:0] decision;
    wire [4:0] bram_raddr;
    wire [63:0] bram_rdata;

    reg [15:0] vectors [0:MAX_VECTORS-1];
    integer n_vectors;
    integer input_file;
    integer output_file;
    integer scan_rc;
    integer i;
    integer latency_cycles;
    integer max_latency_cycles;
    reg [255:0] input_path;
    reg [255:0] output_path;

    bnn_core dut (
        .clk(clk),
        .rst_n(rst_n),
        .start(start),
        .spike_vector(spike_vector),
        .done(done),
        .decision(decision),
        .bram_raddr(bram_raddr),
        .bram_rdata(bram_rdata)
    );

    bram_weights weights (
        .clk(clk),
        .we(1'b0),
        .waddr(8'd0),
        .wdata(8'd0),
        .raddr(bram_raddr),
        .rdata(bram_rdata)
    );

    initial begin
        clk = 1'b0;
        forever #5 clk = ~clk;
    end

    initial begin
        if (!$value$plusargs("INPUT=%s", input_path)) begin
            input_path = "sim/cosim_input.txt";
        end
        if (!$value$plusargs("OUTPUT=%s", output_path)) begin
            output_path = "sim/cosim_output.txt";
        end

        input_file = $fopen(input_path, "r");
        if (input_file == 0) begin
            $fatal(1, "Could not open cosim input file: %0s", input_path);
        end

        scan_rc = $fscanf(input_file, "%d\n", n_vectors);
        if (scan_rc != 1 || n_vectors <= 0 || n_vectors > MAX_VECTORS) begin
            $fatal(1, "Invalid cosim vector count: %0d", n_vectors);
        end

        for (i = 0; i < n_vectors; i = i + 1) begin
            scan_rc = $fscanf(input_file, "%h\n", vectors[i]);
            if (scan_rc != 1) begin
                $fatal(1, "Failed reading vector %0d", i);
            end
        end
        $fclose(input_file);

        output_file = $fopen(output_path, "w");
        if (output_file == 0) begin
            $fatal(1, "Could not open cosim output file: %0s", output_path);
        end

        rst_n = 1'b0;
        start = 1'b0;
        spike_vector = 16'd0;
        max_latency_cycles = 0;

        repeat (8) @(posedge clk);
        rst_n = 1'b1;
        repeat (4) @(posedge clk);

        for (i = 0; i < n_vectors; i = i + 1) begin
            @(posedge clk);
            spike_vector <= vectors[i];
            start <= 1'b1;
            @(posedge clk);
            start <= 1'b0;

            latency_cycles = 0;
            while (done != 1'b1 && latency_cycles < MAX_DONE_CYCLES) begin
                @(posedge clk);
                latency_cycles = latency_cycles + 1;
            end

            if (done != 1'b1) begin
                $fatal(1, "Timeout waiting for done on vector %0d", i);
            end
            if (latency_cycles > max_latency_cycles) begin
                max_latency_cycles = latency_cycles;
            end

            $fwrite(output_file, "%0d,%04h,%0d,%0d\n",
                    i, vectors[i], decision, latency_cycles);
            repeat (2) @(posedge clk);
        end

        $display("COSIM_COMPLETE vectors=%0d max_latency_cycles=%0d",
                 n_vectors, max_latency_cycles);
        $fclose(output_file);
        $finish;
    end

endmodule
