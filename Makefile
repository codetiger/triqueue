# SPDX-FileCopyrightText: 2026 Harishankar Narayanan
# SPDX-License-Identifier: Apache-2.0
#
# make            model tests, driver tests, RTL lint, RTL regression, formal, synthesis check
# make <target>   one of the targets below

PYTHON    ?= python3
VERILATOR ?= verilator
YOSYS     ?= yosys
SBY       ?= sby

RTL       := $(shell cat rtl/files.f)
LINT_CFGS := "" "-GCLK_GATE=0" "-GWR_ACK=1" "-GPANEL_BYTES=1024" "-GMAC_PIPE=1" "-GPERF=0" "-GT=4" "-GT=16" "-GT=32" "-GSHARED_PORT=1" "-GFIFO_DEPTH=2 -GMACQ_DEPTH=2" "-GACC_FORM=0"
ACC_FORMS := cpa:1 csa:0

.PHONY: all model-test sw-test model-results flow-results lock-check spdx-check rtl-lint rtl-test formal formal-nightly synth-check fpga ui-data ui-serve dist clean

all: model-test sw-test spdx-check lock-check rtl-lint rtl-test formal synth-check

VERSION ?= $(shell sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml | head -1)

model-test:
	cd model && $(PYTHON) -m pytest -q

# C driver (sw/) checked against the model's program builder and packing
sw-test:
	$(PYTHON) -m pytest sw -q

# Every checked-in source/config file carries an SPDX license identifier
spdx-check:
	$(PYTHON) tools/check_spdx.py

model-results:
	cd model && $(PYTHON) bench.py > /dev/null && $(PYTHON) dse.py > /dev/null

# Rewrites flow/results.md from the committed run records (flow/records/). Running the physical
# design flow itself is manual: flow/ppa.py, flow/power.py, then flow/results.py collect.
flow-results:
	$(PYTHON) flow/results.py > /dev/null

# Verifies that floorplan utilization, flow/records/, and 3D UI layouts match flow/LAYOUT_LOCK.json
lock-check:
	$(PYTHON) flow/check_lock.py

rtl-lint:
	@for p in $(LINT_CFGS); do \
	  echo "verilator lint $$p"; \
	  $(VERILATOR) --lint-only -Wall -f rtl/files.f --top-module tq_top $$p || exit 1; \
	done
	@echo "yosys elaborate PANEL_BYTES=1024"; $(YOSYS) -q -p "read_verilog -sv -DSYNTHESIS $(RTL); \
	  chparam -set PANEL_BYTES 1024 tq_top; hierarchy -check -top tq_top; proc; check -assert"
	@for t in tq_top_axil tq_top_axi tq_top_xif; do \
	  echo "verilator lint $$t"; $(VERILATOR) --lint-only -Wall -f rtl/files.f --top-module $$t || exit 1; \
	  echo "yosys elaborate $$t"; $(YOSYS) -q -p "read_verilog -sv -DSYNTHESIS $(RTL); \
	    hierarchy -check -top $$t; proc; check -assert" || exit 1; \
	done
	@echo "verilator lint tq_top_axi -GBURST_BEATS=4"; $(VERILATOR) --lint-only -Wall -f rtl/files.f --top-module tq_top_axi -GBURST_BEATS=4 || exit 1
	@echo "yosys elaborate tq_top_axi BURST_BEATS=4"; $(YOSYS) -q -p "read_verilog -sv -DSYNTHESIS $(RTL); \
	  chparam -set BURST_BEATS 4 tq_top_axi; hierarchy -check -top tq_top_axi; proc; check -assert" || exit 1
	@for f in $(ACC_FORMS); do \
	  echo "yosys elaborate ACC_FORM=$${f#*:}"; \
	  $(YOSYS) -q -p "read_verilog -sv -DSYNTHESIS $(RTL); chparam -set ACC_FORM $${f#*:} tq_top; \
	    hierarchy -check -top tq_top; proc; check -assert" || exit 1; \
	done

rtl-test:
	$(PYTHON) -m pytest verif -q

# formal: clock-gating template (pattern check + tq_cg.sby), FIFO, AXI4-Lite and CV-X-IF adapters, AXI master covers, core proof (split port). formal-nightly: core proof, shared port (1-80 min, varies widely).
formal:
	$(PYTHON) verif/formal/check_cg_pattern.py
	cd verif/formal && $(SBY) -f tq_cg.sby && $(SBY) -f tq_fifo.sby && $(SBY) -f tq_axil_regs.sby && $(SBY) -f tq_xif_regs.sby && $(SBY) -f tq_axi_mem.sby cover burst && $(SBY) -f tq_core.sby split

formal-nightly:
	cd verif/formal && $(SBY) -f tq_core.sby shared && $(SBY) -f tq_core.sby ack && $(SBY) -f tq_core.sby panel && $(SBY) -f tq_axi_mem.sby prove

# Synthesizability check with generic Yosys cells: no latches, no loops, no undriven nets.
# The clock-gating cell (tq_icg, a latch by design) is kept as its own module and is the only
# place a latch may appear. Gate counts here are technology-independent and are not area results.
synth-check:
	mkdir -p build/synth
	@for f in $(ACC_FORMS); do \
	  n=$${f%%:*}; v=$${f#*:}; echo "yosys synth T=8 ACC_FORM=$$n"; \
	  $(YOSYS) -q -l build/synth/tq_top_t8_$$n.log -p "read_verilog -sv -DSYNTHESIS $(RTL); \
	    chparam -set ACC_FORM $$v tq_top; hierarchy -check -top tq_top; \
	    setattr -mod -set keep_hierarchy 1 tq_icg; synth -top tq_top -flatten; \
	    check -assert -noinit; select -assert-none tq_top/t:\$$dlatch tq_top/t:\$$_DLATCH_*; \
	    tee -o build/synth/tq_top_t8_$${n}_stat.txt stat" || exit 1; \
	done

# ECP5 out-of-context build (flow/fpga, needs nextpnr-ecp5; not part of all)
fpga:
	$(PYTHON) flow/fpga/fpga.py all t8_cpa

# Interactive RTL-to-Silicon UI Pipeline (ui/)
ui-data:
	$(PYTHON) ui/pipeline/build_ui_data.py

ui-serve:
	$(PYTHON) -m http.server 8080 --directory ui

# Release artifacts: source tarball, a representative golden-vector set, and checksums.
dist:
	rm -rf dist
	mkdir -p dist
	git archive --format=tar.gz --prefix=triqueue-$(VERSION)/ -o dist/triqueue-$(VERSION).tar.gz HEAD
	cd model && $(PYTHON) -m tri_queue.vectors --m 16 --n 16 --k 16 -o ../dist/vectors/16x16x16
	cd model && $(PYTHON) -m tri_queue.vectors --m 20 --n 9 --k 33 -o ../dist/vectors/20x9x33
	cd model && $(PYTHON) -m tri_queue.vectors --m 64 --n 64 --k 64 -o ../dist/vectors/64x64x64
	tar -czf dist/triqueue-$(VERSION)-vectors.tar.gz -C dist vectors
	cd dist && $(PYTHON) -c "import hashlib,glob,os; files=sorted(f for f in glob.glob('*') if os.path.isfile(f)); open('SHA256SUMS','w').write(''.join(hashlib.sha256(open(f,'rb').read()).hexdigest()+'  '+f+'\n' for f in files))"

clean:
	rm -rf build dist verif/formal/tq_fifo verif/formal/tq_fifo_* verif/formal/tq_core verif/formal/tq_core_*/ verif/formal/tq_axil_regs verif/formal/tq_axil_regs_*/ verif/formal/tq_xif_regs verif/formal/tq_xif_regs_*/ verif/formal/tq_axi_mem verif/formal/tq_axi_mem_*/ verif/formal/tq_cg verif/formal/tq_cg_*/
