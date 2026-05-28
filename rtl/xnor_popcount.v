`timescale 1ns/1ps

module xnor_popcount #(
    parameter WIDTH = 16
) (
    input  wire [WIDTH-1:0] inputs,
    input  wire [WIDTH-1:0] weights,
    output wire [$clog2(WIDTH):0] popcount
);

    wire [WIDTH-1:0] xnor_out;
    
    // Bipolar multiplication: XNOR
    // +1 (logic 1) * +1 (logic 1) = +1 (logic 1)
    // -1 (logic 0) * -1 (logic 0) = +1 (logic 1)
    // +1 (logic 1) * -1 (logic 0) = -1 (logic 0)
    // -1 (logic 0) * +1 (logic 1) = -1 (logic 0)
    assign xnor_out = ~(inputs ^ weights);

    // Popcount tree
    integer i;
    reg [$clog2(WIDTH):0] sum;
    
    always @(*) begin
        sum = 0;
        for (i = 0; i < WIDTH; i = i + 1) begin
            sum = sum + xnor_out[i];
        end
    end

    assign popcount = sum;

endmodule
