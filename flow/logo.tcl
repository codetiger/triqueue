# SPDX-FileCopyrightText: 2026 Harishankar Narayanan
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
#
# Layout signature: "codetiger" drawn in physical-only cells, placed FIRM (the placers never move
# them) right after the platform's tap cells (ORFS POST_TAPCELL_TCL, before any logic is placed).
# Every pixel is one row high and PIXEL_SITES sites wide, at the bottom centre of the core: the
# south edge carries only the low-activity register readback pins (io_constraints.tcl). "On"
# pixels are decap cells, "off" pixels filler cells, so the letters show in the layout without any
# routing or netlist change; tap cells already in the box are kept and worked around. Every cell
# is named TQ_LOGO_<n>; the cell count and the area of cells the platform counts as logic (sky130hd
# decaps are CLASS CORE) go to $RESULTS_DIR/tq_logo.json so results.py can keep them out of the
# cell area.
#
# Environment (from ppa.py): TQ_LOGO_TEXT, TQ_LOGO_PIXEL_SITES, TQ_LOGO_ROW0 (rows above the core
# bottom), TQ_LOGO_DECAPS / TQ_LOGO_FILLERS (masters, widest first).

# 5x7 glyphs, top row first; '#' is on
array set ::tq_glyph {
  c {..... .###. #.... #.... #.... .###. .....}
  o {..... .###. #...# #...# #...# .###. .....}
  d {....# .#### #...# #...# #...# .#### .....}
  e {..... .###. #...# ##### #.... .###. .....}
  t {.#... ####. .#... .#... .#..# ..##. .....}
  i {..#.. ..... .##.. ..#.. ..#.. .###. .....}
  g {..... .#### #...# #...# .#### ....# .###.}
  r {..... #.##. ##..# #.... #.... #.... .....}
}

proc tq_logo_bitmap { text } {
  # rows of '0'/'1', top row first; one blank column between glyphs

  set rows {}
  for { set r 0 } { $r < 7 } { incr r } {
    set line ""
    set first 1
    foreach ch [split $text ""] {
      if { !$first } { append line "0" }
      set first 0
      append line [string map {# 1 . 0} [lindex $::tq_glyph($ch) $r]]
    }
    lappend rows $line
  }
  return $rows
}

# fill [x0, x1) of a row (DBU) with masters from `masters` (widest first, widths in DBU),
# greedily; return the list of {master x width}
proc tq_logo_tile { x0 x1 masters } {
  set out {}
  set x $x0
  foreach m $masters {
    lassign $m master w
    while { $x + $w <= $x1 } {
      lappend out [list $master $x $w]
      set x [expr { $x + $w }]
    }
  }
  return [list $out $x]
}

set block [ord::get_db_block]
set db    [ord::get_db]
set bitmap [tq_logo_bitmap $::env(TQ_LOGO_TEXT)]
set ncols  [string length [lindex $bitmap 0]]

set decaps {}
foreach n $::env(TQ_LOGO_DECAPS) {
  set m [$db findMaster $n]
  if { $m eq "NULL" } { error "TQ logo: master $n not found" }
  lappend decaps [list $m [$m getWidth]]
}
set fillers {}
foreach n $::env(TQ_LOGO_FILLERS) {
  set m [$db findMaster $n]
  if { $m eq "NULL" } { error "TQ logo: master $n not found" }
  lappend fillers [list $m [$m getWidth]]
}

# core rows, bottom to top
set rows [lsort -integer -index 0 [lmap row [$block getRows] { list [[$row getBBox] yMin] $row }]]
set row0 $::env(TQ_LOGO_ROW0)
if { [llength $rows] < $row0 + 7 } { error "TQ logo: core has too few rows" }
set site_w [[[lindex $rows 0 1] getSite] getWidth]
set px_w   [expr { $site_w * $::env(TQ_LOGO_PIXEL_SITES) }]
set core   [$block getCoreArea]
set box_w  [expr { $ncols * $px_w }]
# bottom centre, on the site grid of the first logo row
set rbox   [[lindex $rows $row0 1] getBBox]
set rx0    [$rbox xMin]
set bx0    [expr { $rx0 + (((([$core xMin] + [$core xMax] - $box_w) / 2) - $rx0) / $site_w) * $site_w }]
set bx1    [expr { $bx0 + $box_w }]

set n 0
set logic_area 0
set logic_cells 0
set on_px 0
for { set r 0 } { $r < 7 } { incr r } {
  lassign [lindex $rows [expr { $row0 + 6 - $r }]] ry row
  set orient [$row getOrient]
  set rh [[$row getBBox] dy]
  # cells already in this row's span of the box (tap cells), as [x0, x1) intervals
  set taken {}
  foreach inst [$block getInsts] {
    set b [$inst getBBox]
    if { [$b yMin] < $ry + $rh && [$b yMax] > $ry && [$b xMin] < $bx1 && [$b xMax] > $bx0 } {
      lappend taken [list [$b xMin] [$b xMax]]
    }
  }
  set line [lindex $bitmap $r]
  for { set c 0 } { $c < $ncols } { incr c } {
    set on [expr { [string index $line $c] eq "1" }]
    set p0 [expr { $bx0 + $c * $px_w }]
    set p1 [expr { $p0 + $px_w }]
    # free intervals of this pixel
    set free [list [list $p0 $p1]]
    foreach t $taken {
      lassign $t t0 t1
      set next {}
      foreach f $free {
        lassign $f f0 f1
        if { $t1 <= $f0 || $t0 >= $f1 } { lappend next $f; continue }
        if { $t0 > $f0 } { lappend next [list $f0 $t0] }
        if { $t1 < $f1 } { lappend next [list $t1 $f1] }
      }
      set free $next
    }
    if { $on } { incr on_px }
    foreach f $free {
      lassign $f f0 f1
      set x $f0
      if { $on } {
        lassign [tq_logo_tile $x $f1 $decaps] cells x
        foreach cell $cells {
          lassign $cell m cx w
          set inst [odb::dbInst_create $block $m "TQ_LOGO_$n"]
          $inst setOrient $orient
          $inst setLocation $cx $ry
          $inst setPlacementStatus FIRM
          if { ![$m isFiller] } {
            set logic_area [expr { $logic_area + double($w) * [$m getHeight] }]
            incr logic_cells
          }
          incr n
        }
      }
      lassign [tq_logo_tile $x $f1 $fillers] cells x
      foreach cell $cells {
        lassign $cell m cx w
        set inst [odb::dbInst_create $block $m "TQ_LOGO_$n"]
        $inst setOrient $orient
        $inst setLocation $cx $ry
        $inst setPlacementStatus FIRM
        incr n
      }
      if { $x != $f1 } { error "TQ logo: cannot tile \[$f0, $f1) exactly" }
    }
  }
}

set dbu [$block getDbUnitsPerMicron]
set area_um2 [expr { $logic_area / double($dbu) / double($dbu) }]
puts [format "TQ logo: \"%s\", %d x 7 pixels (%d on), %d cells, box %.2f x %.2f um at (%.2f, %.2f), %.3f um2 counted as logic" \
  $::env(TQ_LOGO_TEXT) $ncols $on_px $n [expr { double($box_w) / $dbu }] \
  [expr { 7.0 * $rh / $dbu }] [expr { double($bx0) / $dbu }] [expr { double([lindex $rows $row0 0]) / $dbu }] $area_um2]
set fh [open $::env(RESULTS_DIR)/tq_logo.json w]
puts $fh [format "{\"text\": \"%s\", \"cells\": %d, \"on_pixels\": %d, \"logic_cells\": %d, \"logic_area_um2\": %.6f}" \
  $::env(TQ_LOGO_TEXT) $n $on_px $logic_cells $area_um2]
close $fh
