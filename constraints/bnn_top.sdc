# Generic timing constraints for bnn_top.
# Validate syntax and object names in the Renesas Go Configure Software Hub flow.

create_clock -name sys_clk -period 10.000 [get_ports sys_clk]
create_clock -name spi_sclk -period 12.500 [get_ports spi_sclk]

set_clock_groups -asynchronous \
    -group [get_clocks sys_clk] \
    -group [get_clocks spi_sclk]

# CDC is handled inside spi_slave by a completed-packet toggle synchronizer.
set_false_path -from [get_ports spi_sclk] -to [get_clocks sys_clk]
set_false_path -from [get_clocks sys_clk] -to [get_ports spi_miso]

# External interrupt should settle well within one ESP32 polling/interrupt interval.
set_max_delay 20.000 -from [get_clocks sys_clk] -to [get_ports fpga_done]

# Reset is asynchronous by design.
set_false_path -from [get_ports rst_n]
