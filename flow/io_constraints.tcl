# SPDX-FileCopyrightText: 2026 Harishankar Narayanan
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
#
# Deterministic perimeter I/O pin constraints for tq_top (sourced at floorplan).
# Mirrors the West -> Center -> East datapath topology so pins spread across all
# four die edges and global placement fills the entire core uniformly:
#   - Left  (West) : SRAM read port (rd_*) -> feeds tq_stream, QA FIFO, QB FIFO
#   - Right (East) : SRAM write port (wr_*) <- fed by Accumulator Banks & tq_drain
#   - Top   (North): Clock/reset/IRQ + host CSR command/write channel
#   - Bottom(South): Host CSR readback channel (reg_rvalid_o, reg_rdata_o)

proc tq_collect_pins { patterns } {
  set names {}
  foreach pat $patterns {
    foreach p [get_ports -quiet $pat] {
      lappend names [get_name $p]
    }
  }
  return [lsort -dictionary -unique $names]
}

set west_pins  [tq_collect_pins {rd_*}]
set east_pins  [tq_collect_pins {wr_*}]
set south_pins [tq_collect_pins {reg_rvalid_o reg_rdata_o*}]
set north_pins [tq_collect_pins {clk_i rst_ni bus_err_i irq_o reg_req_i reg_gnt_o reg_we_i reg_be_i* reg_addr_i* reg_wdata_i*}]

if { [llength $west_pins] > 0 } {
  set_io_pin_constraint -pin_names $west_pins -region left:*
}
if { [llength $east_pins] > 0 } {
  set_io_pin_constraint -pin_names $east_pins -region right:*
}
if { [llength $north_pins] > 0 } {
  set_io_pin_constraint -pin_names $north_pins -region top:*
}
if { [llength $south_pins] > 0 } {
  set_io_pin_constraint -pin_names $south_pins -region bottom:*
}
