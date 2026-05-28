`timescale 1ns/1ps

module bnn_top (
    input  wire sys_clk,
    input  wire rst_n,
    
    // SPI Slave Pins
    input  wire spi_sclk,
    input  wire spi_cs_n,
    input  wire spi_mosi,
    output wire spi_miso,
    
    // Interrupt / Done pin
    output wire fpga_done
);

    // Interconnects
    wire        bnn_start;
    wire [15:0] bnn_spike_vector;
    wire        bram_we;
    wire [7:0]  bram_waddr;
    wire [7:0]  bram_wdata;
    
    wire [1:0]  bnn_decision;
    wire        bnn_done;
    
    wire [4:0]  bram_raddr;
    wire [63:0] bram_rdata;

    // SPI Slave
    spi_slave u_spi_slave (
        .rst_n(rst_n),
        .sclk(spi_sclk),
        .cs_n(spi_cs_n),
        .mosi(spi_mosi),
        .miso(spi_miso),
        
        .sys_clk(sys_clk),
        .bnn_start(bnn_start),
        .bnn_spike_vector(bnn_spike_vector),
        
        .bram_we(bram_we),
        .bram_waddr(bram_waddr),
        .bram_wdata(bram_wdata),
        
        .bnn_decision(bnn_decision)
    );

    // BRAM Weights
    bram_weights u_bram_weights (
        .clk(sys_clk),
        
        // Write port (from SPI)
        .we(bram_we),
        .waddr(bram_waddr),
        .wdata(bram_wdata),
        
        // Read port (to BNN Core)
        .raddr(bram_raddr),
        .rdata(bram_rdata)
    );

    // BNN Core
    bnn_core u_bnn_core (
        .clk(sys_clk),
        .rst_n(rst_n),
        
        // SPI Control
        .start(bnn_start),
        .spike_vector(bnn_spike_vector),
        .done(bnn_done),
        .decision(bnn_decision),
        
        // BRAM Interface
        .bram_raddr(bram_raddr),
        .bram_rdata(bram_rdata)
    );

    // Route done signal to external pin
    // Using a simple RS latch pattern or directly assigning since done is pulsed.
    // Wait, bnn_done is a 1-cycle pulse. The ESP32 is waiting for a level!
    // If we just output a pulse, the ESP32 GPIO polling might miss it.
    // We need to latch it.
    reg done_latch;
    reg [2:0] spi_cs_sync;
    wire spi_cs_falling;

    always @(posedge sys_clk or negedge rst_n) begin
        if (!rst_n) begin
            spi_cs_sync <= 3'b111;
        end else begin
            spi_cs_sync <= {spi_cs_sync[1:0], spi_cs_n};
        end
    end

    assign spi_cs_falling = (spi_cs_sync[2:1] == 2'b10);

    always @(posedge sys_clk or negedge rst_n) begin
        if (!rst_n) begin
            done_latch <= 1'b0;
        end else begin
            if (spi_cs_falling || bnn_start) begin
                done_latch <= 1'b0; // Clear when ESP32 starts any SPI transaction
            end else if (bnn_done) begin
                done_latch <= 1'b1; // Set when inference completes
            end
        end
    end
    
    assign fpga_done = done_latch;

endmodule
