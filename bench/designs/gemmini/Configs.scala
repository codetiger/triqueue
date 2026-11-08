// SPDX-FileCopyrightText: 2026 Harishankar Narayanan
// SPDX-License-Identifier: Apache-2.0
//
// Gemmini baseline for the same-flow comparison (bench/designs/gemmini/README.md).
// Copied into Chipyard's config directory by generate.sh. Base: Gemmini's leanConfig (the one
// Chipyard's LeanGemminiRocketConfig uses: weight-stationary, int8 inputs, int32 accumulators),
// with the array cut to 8 x 8 PEs = 64 int8 MACs per cycle, the Tri-Queue T = 8 peak. (The
// taped-out chipConfig does not elaborate at this Gemmini commit: its single-ported accumulator
// path dereferences an external-memory port that only exists with shared external memory.)
// Scratchpad 16 KB, accumulator 8 KB (README: where the SRAM boundary goes is still open).
package chipyard

import org.chipsalliance.cde.config.Config
import gemmini._

object TriqueueGemmini {
  val mesh8x8 = GemminiConfigs.leanConfig.copy(
    meshRows = 8, meshColumns = 8, tileRows = 1, tileColumns = 1,
    sp_capacity = CapacityInKilobytes(16), acc_capacity = CapacityInKilobytes(8))
}

class Gemmini8x8RocketConfig extends Config(
  new gemmini.DefaultGemminiConfig(TriqueueGemmini.mesh8x8) ++
  new freechips.rocketchip.rocket.WithNHugeCores(1) ++
  new chipyard.config.WithSystemBusWidth(128) ++
  new chipyard.config.AbstractConfig)
