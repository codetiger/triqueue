// SPDX-FileCopyrightText: 2026 Harishankar Narayanan
// SPDX-License-Identifier: Apache-2.0
//
// Tri-Queue in a Chipyard SoC (bench/soc/chipyard/README.md). The registers hang off the
// periphery bus as an AXI4 slave (TileLink -> AXI4, 4-byte beats), and the core's AXI4 memory
// master joins the system bus (sbus, 16 bytes wide like the core's port) or the front bus (fbus,
// 8 bytes), both coherent with the cores' caches, through AXI4 -> TileLink.
// The RTL is the repository's (rtl/files.f, mounted at /tq) plus the shim vsrc/tq_chipyard.sv.
package chipyard.triqueue

import chisel3._
import chisel3.experimental.IntParam
import chisel3.util.HasBlackBoxInline
import org.chipsalliance.cde.config.{Config, Field, Parameters}
import freechips.rocketchip.amba.axi4._
import freechips.rocketchip.diplomacy._
import freechips.rocketchip.resources.SimpleDevice
import freechips.rocketchip.subsystem.{BaseSubsystem, FBUS, PBUS, SBUS}
import freechips.rocketchip.tilelink._
import testchipip.soc.{SubsystemInjector, SubsystemInjectorKey}

// lines / wrIds / fifoDepth / maxOut: tq_top_axi's LINES, WR_IDS, FIFO_DEPTH and MAX_OUT (wrIds > 0:
// an AXI ID per read line slot and per write, docs/integration.md section 5); sbus: the memory
// master's bus (system bus, else front bus)
case class TriQueueParams(address: BigInt = 0x10040000L, tile: Int = 8, root: String = "/tq",
                          lines: Int = 4, wrIds: Int = 4, fifoDepth: Int = 8, maxOut: Int = 16,
                          sbus: Boolean = true)
case object TriQueueKey extends Field[Option[TriQueueParams]](None)

class TriQueueBlackBox(params: TriQueueParams, idBits: Int, midBits: Int)
    extends BlackBox(Map("T" -> IntParam(params.tile), "ID_W" -> IntParam(idBits), "MID_W" -> IntParam(midBits),
      "LINES" -> IntParam(params.lines), "WR_IDS" -> IntParam(params.wrIds),
      "FIFO_DEPTH" -> IntParam(params.fifoDepth), "MAX_OUT" -> IntParam(params.maxOut)))
    with HasBlackBoxInline {
  override def desiredName = "tq_chipyard"
  private val t = params.tile
  private val root = params.root
  val vb = 16 * t
  val io = IO(new Bundle {
    val clock = Input(Clock())
    val reset = Input(Bool())
    val irq = Output(Bool())
    val s_awid = Input(UInt(idBits.W)); val s_awaddr = Input(UInt(32.W)); val s_awprot = Input(UInt(3.W))
    val s_awvalid = Input(Bool()); val s_awready = Output(Bool())
    val s_wdata = Input(UInt(32.W)); val s_wstrb = Input(UInt(4.W))
    val s_wvalid = Input(Bool()); val s_wready = Output(Bool())
    val s_bid = Output(UInt(idBits.W)); val s_bresp = Output(UInt(2.W))
    val s_bvalid = Output(Bool()); val s_bready = Input(Bool())
    val s_arid = Input(UInt(idBits.W)); val s_araddr = Input(UInt(32.W)); val s_arprot = Input(UInt(3.W))
    val s_arvalid = Input(Bool()); val s_arready = Output(Bool())
    val s_rid = Output(UInt(idBits.W)); val s_rdata = Output(UInt(32.W)); val s_rresp = Output(UInt(2.W))
    val s_rlast = Output(Bool()); val s_rvalid = Output(Bool()); val s_rready = Input(Bool())
    val m_arid = Output(UInt(midBits.W)); val m_araddr = Output(UInt(32.W)); val m_arlen = Output(UInt(8.W))
    val m_arsize = Output(UInt(3.W)); val m_arburst = Output(UInt(2.W)); val m_arprot = Output(UInt(3.W))
    val m_arcache = Output(UInt(4.W)); val m_arqos = Output(UInt(4.W)); val m_arregion = Output(UInt(4.W))
    val m_arvalid = Output(Bool()); val m_arready = Input(Bool())
    val m_rid = Input(UInt(midBits.W)); val m_rdata = Input(UInt(vb.W)); val m_rresp = Input(UInt(2.W))
    val m_rlast = Input(Bool()); val m_rvalid = Input(Bool()); val m_rready = Output(Bool())
    val m_awid = Output(UInt(midBits.W)); val m_awaddr = Output(UInt(32.W)); val m_awlen = Output(UInt(8.W))
    val m_awsize = Output(UInt(3.W)); val m_awburst = Output(UInt(2.W)); val m_awprot = Output(UInt(3.W))
    val m_awcache = Output(UInt(4.W)); val m_awqos = Output(UInt(4.W)); val m_awregion = Output(UInt(4.W))
    val m_awvalid = Output(Bool()); val m_awready = Input(Bool())
    val m_wdata = Output(UInt(vb.W)); val m_wstrb = Output(UInt((vb / 8).W)); val m_wlast = Output(Bool())
    val m_wvalid = Output(Bool()); val m_wready = Input(Bool())
    val m_bid = Input(UInt(midBits.W)); val m_bresp = Input(UInt(2.W)); val m_bvalid = Input(Bool())
    val m_bready = Output(Bool())
  })
  // One file, in rtl/files.f order (tq_pkg first), then the shim: Chipyard compiles black-box
  // sources in its own order, and the package must come before its users.
  // Chipyard 1.15.0's Verilator (5.022) rejects default values on input ports ("input logic
  // ext_pending_i = 1'b0"), which the RTL uses to keep newer ports optional. Every such port is
  // connected in tq_top_axi, so the defaults are dropped from the inlined copy.
  private val portDefault = """(?m)^(\s*input\s+logic\b[^=\n]*?)\s*=\s*1'b[01]""".r
  private def read(f: String) = {
    val src = scala.io.Source.fromFile(f)
    try portDefault.replaceAllIn(src.mkString, m => scala.util.matching.Regex.quoteReplacement(m.group(1)))
    finally src.close()
  }
  private val files = scala.io.Source.fromFile(s"$root/rtl/files.f").getLines().map(_.trim).filter(_.nonEmpty)
    .map(f => s"$root/$f").toSeq :+ s"$root/bench/soc/chipyard/vsrc/tq_chipyard.sv"
  setInline("tq_chipyard_all.sv", files.map(f => s"// ---- $f\n" + read(f)).mkString("\n"))
}

class TriQueueAXI(params: TriQueueParams)(implicit p: Parameters) extends LazyModule {
  val portBytes = 2 * params.tile
  val device = new SimpleDevice("triqueue", Seq("triqueue,tq-1"))
  val regNode = AXI4SlaveNode(Seq(AXI4SlavePortParameters(
    slaves = Seq(AXI4SlaveParameters(
      address = Seq(AddressSet(params.address, 0xfff)),
      resources = device.reg,
      regionType = RegionType.UNCACHED,
      executable = false,
      supportsWrite = TransferSizes(1, 4),
      supportsRead = TransferSizes(1, 4),
      interleavedId = Some(0))),
    beatBytes = 4)))
  val memNode = AXI4MasterNode(Seq(AXI4MasterPortParameters(
    // wrIds = 0: one ID, up to maxOut transactions in flight per direction, in order. Otherwise an
    // ID per read line slot and per write ID, each with one transaction in flight (AXI4ToTL gives
    // every ID its own TileLink sources and FIFO domain, so the IDs do not wait for each other).
    masters = Seq(if (params.wrIds == 0)
      AXI4MasterParameters(name = "triqueue", id = IdRange(0, 1), maxFlight = Some(params.maxOut))
    else
      AXI4MasterParameters(name = "triqueue", id = IdRange(0, params.lines + params.wrIds), maxFlight = Some(1))))))

  lazy val module = new LazyModuleImp(this) {
    val (s, se) = regNode.in(0)
    val (m, me) = memNode.out(0)
    val bb = Module(new TriQueueBlackBox(params, se.bundle.idBits, me.bundle.idBits))
    bb.io.clock := clock
    bb.io.reset := reset.asBool
    // registers (slave)
    bb.io.s_awid := s.aw.bits.id; bb.io.s_awaddr := s.aw.bits.addr; bb.io.s_awprot := s.aw.bits.prot
    bb.io.s_awvalid := s.aw.valid; s.aw.ready := bb.io.s_awready
    bb.io.s_wdata := s.w.bits.data; bb.io.s_wstrb := s.w.bits.strb
    bb.io.s_wvalid := s.w.valid; s.w.ready := bb.io.s_wready
    s.b.bits.id := bb.io.s_bid; s.b.bits.resp := bb.io.s_bresp
    s.b.valid := bb.io.s_bvalid; bb.io.s_bready := s.b.ready
    bb.io.s_arid := s.ar.bits.id; bb.io.s_araddr := s.ar.bits.addr; bb.io.s_arprot := s.ar.bits.prot
    bb.io.s_arvalid := s.ar.valid; s.ar.ready := bb.io.s_arready
    s.r.bits.id := bb.io.s_rid; s.r.bits.data := bb.io.s_rdata; s.r.bits.resp := bb.io.s_rresp
    s.r.bits.last := bb.io.s_rlast; s.r.valid := bb.io.s_rvalid; bb.io.s_rready := s.r.ready
    // memory (master); rocket-chip's AXI4 bundle has no AxREGION, and tq_top_axi drives it 0
    m.ar.bits := 0.U.asTypeOf(m.ar.bits)
    m.ar.bits.id := bb.io.m_arid
    m.ar.bits.addr := bb.io.m_araddr; m.ar.bits.len := bb.io.m_arlen; m.ar.bits.size := bb.io.m_arsize
    m.ar.bits.burst := bb.io.m_arburst; m.ar.bits.prot := bb.io.m_arprot
    m.ar.bits.cache := bb.io.m_arcache; m.ar.bits.qos := bb.io.m_arqos
    m.ar.valid := bb.io.m_arvalid; bb.io.m_arready := m.ar.ready
    bb.io.m_rid := m.r.bits.id
    bb.io.m_rdata := m.r.bits.data; bb.io.m_rresp := m.r.bits.resp; bb.io.m_rlast := m.r.bits.last
    bb.io.m_rvalid := m.r.valid; m.r.ready := bb.io.m_rready
    m.aw.bits := 0.U.asTypeOf(m.aw.bits)
    m.aw.bits.id := bb.io.m_awid
    m.aw.bits.addr := bb.io.m_awaddr; m.aw.bits.len := bb.io.m_awlen; m.aw.bits.size := bb.io.m_awsize
    m.aw.bits.burst := bb.io.m_awburst; m.aw.bits.prot := bb.io.m_awprot
    m.aw.bits.cache := bb.io.m_awcache; m.aw.bits.qos := bb.io.m_awqos
    m.aw.valid := bb.io.m_awvalid; bb.io.m_awready := m.aw.ready
    m.w.bits := 0.U.asTypeOf(m.w.bits)
    m.w.bits.data := bb.io.m_wdata; m.w.bits.strb := bb.io.m_wstrb; m.w.bits.last := bb.io.m_wlast
    m.w.valid := bb.io.m_wvalid; bb.io.m_wready := m.w.ready
    bb.io.m_bid := m.b.bits.id
    bb.io.m_bresp := m.b.bits.resp; bb.io.m_bvalid := m.b.valid; m.b.ready := bb.io.m_bready
  }
}

case object TriQueueInjector extends SubsystemInjector((p, baseSubsystem) => {
  p(TriQueueKey).foreach { params =>
    implicit val q: Parameters = p
    val pbus = baseSubsystem.locateTLBusWrapper(PBUS)
    val mbus = baseSubsystem.locateTLBusWrapper(if (params.sbus) SBUS else FBUS)
    val tq = pbus { LazyModule(new TriQueueAXI(params)) }
    pbus.coupleTo("triqueue") {
      // TLToAXI4 carries TileLink state in AXI echo fields; the yanker keeps them per ID, since the
      // Verilog slave does not echo them
      tq.regNode := AXI4UserYanker() := AXI4Buffer() := TLToAXI4() :=
        TLFragmenter(4, pbus.blockBytes, holdFirstDeny = true) := TLWidthWidget(pbus.beatBytes) := _
    }
    mbus.coupleFrom("triqueue") {
      // AXI4ToTL reuses TileLink sources in order and needs responses in order per AXI ID; the
      // system bus orders only volatile regions, so cacheable DRAM gets a FIFO fixer here. The
      // fixer lets one transaction of an ID (one TileLink client, reads and writes together) into
      // cached memory at a time, hence wrIds > 0.
      _ := TLFIFOFixer(TLFIFOFixer.all) := TLWidthWidget(tq.portBytes) := AXI4ToTL() := AXI4UserYanker(Some(16)) := AXI4Fragmenter() := tq.memNode
    }
  }
})

class WithTriQueue(params: TriQueueParams = TriQueueParams()) extends Config((site, here, up) => {
  case TriQueueKey => Some(params)
  case SubsystemInjectorKey => up(SubsystemInjectorKey) + TriQueueInjector
})

// System bus, an ID per read line slot and per write (the recommended attachment)
class TriQueueRocketConfig extends Config(
  new WithTriQueue ++
  new freechips.rocketchip.rocket.WithNHugeCores(1) ++
  new chipyard.config.WithSystemBusWidth(128) ++
  new chipyard.config.AbstractConfig)

// Front bus (8 bytes wide), an ID per read line slot and per write
class TriQueueFbusConfig extends Config(
  new WithTriQueue(TriQueueParams(sbus = false)) ++
  new freechips.rocketchip.rocket.WithNHugeCores(1) ++
  new chipyard.config.WithSystemBusWidth(128) ++
  new chipyard.config.AbstractConfig)

// Front bus, one AXI ID (the v1.0 attachment)
class TriQueueOneIdConfig extends Config(
  new WithTriQueue(TriQueueParams(sbus = false, wrIds = 0)) ++
  new freechips.rocketchip.rocket.WithNHugeCores(1) ++
  new chipyard.config.WithSystemBusWidth(128) ++
  new chipyard.config.AbstractConfig)
