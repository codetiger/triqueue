# SPDX-FileCopyrightText: 2026 Harishankar Narayanan
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
#
# Activity-based power of the routed design. Run by flow/power.py through ORFS `make run`.
# Inputs (environment): TQ_SAIF (flattened, scaled to the flow clock), TQ_REGS (register bit
# activity table from power.py), TQ_POWER_RPT (report file).

source $::env(SCRIPTS_DIR)/load.tcl
load_design 6_final.odb 6_final.sdc
if { [file exists $::env(RESULTS_DIR)/6_final.spef] } {
  read_spef $::env(RESULTS_DIR)/6_final.spef
} else {
  puts "TQ: no 6_final.spef, using placement parasitics estimate"
  estimate_parasitics -placement
}
read_saif -scope tq_top $::env(TQ_SAIF)

# Register outputs from the register's cell name (RTL bit name + Yosys suffix), so that they
# are annotated even where the named net is gone (QN-only flops, renamed nets). Q_N/QN outputs
# get the inverted duty; the toggle rate is the same.
source $::env(TQ_REGS)
set n_reg 0
set n_pin 0
set missing {}
foreach reg [all_registers -cells] {
  incr n_reg
  set bit [regsub {\$_[A-Za-z0-9_]+$} [get_full_name $reg] ""]
  if { ![dict exists $tq_bits $bit] } {
    lappend missing [get_full_name $reg]
    continue
  }
  lassign [dict get $tq_bits $bit] act duty
  foreach pin [get_pins -of_objects $reg -filter "direction==output"] {
    set port [lindex [split [get_full_name $pin] /] end]
    if { $port eq "QN" || $port eq "Q_N" } {
      set_power_activity -pins $pin -activity $act -duty [expr { 1.0 - $duty }]
    } else {
      set_power_activity -pins $pin -activity $act -duty $duty
    }
    incr n_pin
  }
}
puts "TQ: registers $n_reg, output pins annotated $n_pin, registers not in SAIF [llength $missing]"
foreach m [lrange $missing 0 9] {
  puts "TQ:   not in SAIF: $m"
}

report_activity_annotation
set rpt $::env(TQ_POWER_RPT)
report_power -digits 6 > $rpt
puts "TQ: power report $rpt"
