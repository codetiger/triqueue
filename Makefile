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
LINT_CFGS := "" "-GCLK_GATE=0" "-GT=4" "-GT=16" "-GT=32" "-GSHARED_PORT=1" "-GFIFO_DEPTH=2 -GMACQ_DEPTH=2" "-GACC_FORM=0"
ACC_FORMS := cpa:1 csa:0

.PHONY: all model-test sw-test model-results flow-results rtl-lint rtl-test formal formal-nightly synth-check clean

all: model-test sw-test rtl-lint rtl-test formal synth-check

model-test:
	cd model && $(PYTHON) -m pytest -q

# C driver (sw/) checked against the model's program builder and packing
sw-test:
	$(PYTHON) -m pytest sw -q

model-results:
	cd model && $(PYTHON) bench.py > /dev/null && $(PYTHON) dse.py > /dev/null

# Rewrites flow/results.md from the committed run records (flow/records/). Running the physical
# design flow itself is manual: flow/ppa.py, flow/power.py, then flow/results.py collect.
flow-results:
	$(PYTHON) flow/results.py > /dev/null

rtl-lint:
	@for p in $(LINT_CFGS); do \
	  echo "verilator lint $$p"; \
	  $(VERILATOR) --lint-only -Wall -f rtl/files.f --top-module tq_top $$p || exit 1; \
	done
	@echo "verilator lint tq_top_axil"; $(VERILATOR) --lint-only -Wall -f rtl/files.f --top-module tq_top_axil
	@echo "yosys elaborate tq_top_axil"; $(YOSYS) -q -p "read_verilog -sv -DSYNTHESIS $(RTL); \
	  hierarchy -check -top tq_top_axil; proc; check -assert"
	@for f in $(ACC_FORMS); do \
	  echo "yosys elaborate ACC_FORM=$${f#*:}"; \
	  $(YOSYS) -q -p "read_verilog -sv -DSYNTHESIS $(RTL); chparam -set ACC_FORM $${f#*:} tq_top; \
	    hierarchy -check -top tq_top; proc; check -assert" || exit 1; \
	done

rtl-test:
	$(PYTHON) -m pytest verif -q

# formal: FIFO proof + AXI4-Lite adapter proof + core proof, split port. formal-nightly: core proof, shared port (1-80 min, varies widely).
formal:
	cd verif/formal && $(SBY) -f tq_fifo.sby && $(SBY) -f tq_axil_regs.sby && $(SBY) -f tq_core.sby split

formal-nightly:
	cd verif/formal && $(SBY) -f tq_core.sby shared

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

clean:
	rm -rf build verif/formal/tq_fifo verif/formal/tq_fifo_* verif/formal/tq_core verif/formal/tq_core_*/ verif/formal/tq_axil_regs
