// SPDX-FileCopyrightText: 2026 Harishankar Narayanan
// SPDX-License-Identifier: Apache-2.0
//
// Tri-Queue in a Chipyard SoC (bench/soc/chipyard/README.md). The registers hang off the
// periphery bus as an AXI4 slave (TileLink -> AXI4, 4-byte beats), and the core's AXI4 memory
// master joins the front bus, which is coherent with the cores' caches, through AXI4 -> TileLink.
// The RTL is the repository's (rtl/files.f, mounted at /tq) plus the shim vsrc/tq_chipyard.sv.
package chipyard.triqueue

import chisel3._
import chisel3.experimental.IntParam
import chisel3.util.HasBlackBoxInline
import org.chipsalliance.cde.config.{Config, Field, Parameters}
import freechips.rocketchip.amba.axi4._
import freechips.rocketchip.diplomacy._
import freechips.rocketchip.resources.SimpleDevice
import freechips.rocketchip.subsystem.{BaseSubsystem, FBUS, PBUS}
import freechips.rocketchip.tilelink._
import testchipip.soc.{SubsystemInjector, SubsystemInjectorKey}

case class TriQueueParams(address: BigInt = 0x10040000L, tile: Int = 8, root: String = "/tq")
case object TriQueueKey extends Field[Option[TriQueueParams]](None)

class TriQueueBlackBox(t: Int, idBits: Int, root: String)
    extends BlackBox(Map("T" -> IntParam(t), "ID_W" -> IntParam(idBits))) with HasBlackBoxInline {
  override def desiredName = "tq_chipyard"
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
    val m_araddr = Output(UInt(32.W)); val m_arlen = Output(UInt(8.W)); val m_arsize = Output(UInt(3.W))
    val m_arburst = Output(UInt(2.W)); val m_arvalid = Output(Bool()); val m_arready = Input(Bool())
    val m_rdata = Input(UInt(vb.W)); val m_rresp = Input(UInt(2.W)); val m_rlast = Input(Bool())
    val m_rvalid = Input(Bool()); val m_rready = Output(Bool())
    val m_awaddr = Output(UInt(32.W)); val m_awlen = Output(UInt(8.W)); val m_awsize = Output(UInt(3.W))
    val m_awburst = Output(UInt(2.W)); val m_awvalid = Output(Bool()); val m_awready = Input(Bool())
    val m_wdata = Output(UInt(vb.W)); val m_wstrb = Output(UInt((vb / 8).W)); val m_wlast = Output(Bool())
    val m_wvalid = Output(Bool()); val m_wready = Input(Bool())
    val m_bresp = Input(UInt(2.W)); val m_bvalid = Input(Bool()); val m_bready = Output(Bool())
  })
  // One file, in rtl/files.f order (tq_pkg first), then the shim: Chipyard compiles black-box
  // sources in its own order, and the package must come before its users.
  private def read(f: String) = { val src = scala.io.Source.fromFile(f); try src.mkString finally src.close() }
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
    // one ID, up to 16 transactions in flight per direction (tq_axi_mem MAX_OUT), in order
    masters = Seq(AXI4MasterParameters(name = "triqueue", id = IdRange(0, 1), maxFlight = Some(16))))))

  lazy val module = new LazyModuleImp(this) {
    val (s, se) = regNode.in(0)
    val (m, _) = memNode.out(0)
    val bb = Module(new TriQueueBlackBox(params.tile, se.bundle.idBits, params.root))
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
    // memory (master)
    m.ar.bits := 0.U.asTypeOf(m.ar.bits)
    m.ar.bits.addr := bb.io.m_araddr; m.ar.bits.len := bb.io.m_arlen; m.ar.bits.size := bb.io.m_arsize
    m.ar.bits.burst := bb.io.m_arburst; m.ar.valid := bb.io.m_arvalid; bb.io.m_arready := m.ar.ready
    bb.io.m_rdata := m.r.bits.data; bb.io.m_rresp := m.r.bits.resp; bb.io.m_rlast := m.r.bits.last
    bb.io.m_rvalid := m.r.valid; m.r.ready := bb.io.m_rready
    m.aw.bits := 0.U.asTypeOf(m.aw.bits)
    m.aw.bits.addr := bb.io.m_awaddr; m.aw.bits.len := bb.io.m_awlen; m.aw.bits.size := bb.io.m_awsize
    m.aw.bits.burst := bb.io.m_awburst; m.aw.valid := bb.io.m_awvalid; bb.io.m_awready := m.aw.ready
    m.w.bits := 0.U.asTypeOf(m.w.bits)
    m.w.bits.data := bb.io.m_wdata; m.w.bits.strb := bb.io.m_wstrb; m.w.bits.last := bb.io.m_wlast
    m.w.valid := bb.io.m_wvalid; bb.io.m_wready := m.w.ready
    bb.io.m_bresp := m.b.bits.resp; bb.io.m_bvalid := m.b.valid; m.b.ready := bb.io.m_bready
  }
}

case object TriQueueInjector extends SubsystemInjector((p, baseSubsystem) => {
  p(TriQueueKey).foreach { params =>
    implicit val q: Parameters = p
    val pbus = baseSubsystem.locateTLBusWrapper(PBUS)
    val fbus = baseSubsystem.locateTLBusWrapper(FBUS)
    val tq = pbus { LazyModule(new TriQueueAXI(params)) }
    pbus.coupleTo("triqueue") {
      // TLToAXI4 carries TileLink state in AXI echo fields; the yanker keeps them per ID, since the
      // Verilog slave does not echo them
      tq.regNode := AXI4UserYanker() := AXI4Buffer() := TLToAXI4() :=
        TLFragmenter(4, pbus.blockBytes, holdFirstDeny = true) := TLWidthWidget(pbus.beatBytes) := _
    }
    fbus.coupleFrom("triqueue") {
      // AXI4ToTL reuses TileLink sources in order and needs responses in order per AXI ID; the
      // system bus orders only volatile regions, so cacheable DRAM gets a FIFO fixer here
      _ := TLFIFOFixer(TLFIFOFixer.all) := TLWidthWidget(tq.portBytes) := AXI4ToTL() := AXI4UserYanker(Some(16)) := AXI4Fragmenter() := tq.memNode
    }
  }
})

class WithTriQueue(params: TriQueueParams = TriQueueParams()) extends Config((site, here, up) => {
  case TriQueueKey => Some(params)
  case SubsystemInjectorKey => up(SubsystemInjectorKey) + TriQueueInjector
})

class TriQueueRocketConfig extends Config(
  new WithTriQueue ++
  new freechips.rocketchip.rocket.WithNHugeCores(1) ++
  new chipyard.config.WithSystemBusWidth(128) ++
  new chipyard.config.AbstractConfig)
