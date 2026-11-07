# SPDX-FileCopyrightText: 2026 Harishankar Narayanan
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
#
# Activity-based power of the routed design. Run by flow/power.py through ORFS `make run`.
# Inputs (environment): TQ_SAIF (activity, already scaled to the flow clock), TQ_POWER_RPT.

source $::env(SCRIPTS_DIR)/load.tcl
load_design 6_final.odb 6_final.sdc
if { [file exists $::env(RESULTS_DIR)/6_final.spef] } {
  read_spef $::env(RESULTS_DIR)/6_final.spef
} else {
  puts "TQ: no 6_final.spef, using placement parasitics estimate"
  estimate_parasitics -placement
}
read_saif -scope tq_top $::env(TQ_SAIF)
report_activity_annotation
set rpt $::env(TQ_POWER_RPT)
report_power > $rpt
puts "TQ: power report $rpt"
