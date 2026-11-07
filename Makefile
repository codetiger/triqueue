# SPDX-FileCopyrightText: 2026 Harishankar Narayanan
# SPDX-License-Identifier: Apache-2.0
#
# make            model tests, RTL lint, RTL regression, formal, synthesis check
# make <target>   one of the targets below

PYTHON    ?= python3
VERILATOR ?= verilator
YOSYS     ?= yosys
SBY       ?= sby

RTL       := $(shell cat rtl/files.f)
LINT_CFGS := "" "-GT=4" "-GT=16" "-GT=32" "-GSHARED_PORT=1" "-GFIFO_DEPTH=2 -GMACQ_DEPTH=2"

.PHONY: all model-test model-results rtl-lint rtl-test formal synth-check clean

all: model-test rtl-lint rtl-test formal synth-check

model-test:
	cd model && $(PYTHON) -m pytest -q

model-results:
	cd model && $(PYTHON) bench.py > /dev/null && $(PYTHON) dse.py > /dev/null

rtl-lint:
	@for p in $(LINT_CFGS); do \
	  echo "verilator lint $$p"; \
	  $(VERILATOR) --lint-only -Wall -f rtl/files.f --top-module tq_top $$p || exit 1; \
	done
	$(YOSYS) -q -p "read_verilog -sv -DSYNTHESIS $(RTL); hierarchy -check -top tq_top; proc; check -assert"

rtl-test:
	$(PYTHON) -m pytest verif -q

formal:
	cd verif/formal && $(SBY) -f tq_fifo.sby

# Synthesizability check with generic Yosys cells: no latches, no loops, no undriven nets.
# Gate counts here are technology-independent and are not area results (docs/metrics.md).
synth-check:
	mkdir -p build/synth
	$(YOSYS) -q -l build/synth/tq_top_t8.log -p "read_verilog -sv -DSYNTHESIS $(RTL); \
	  hierarchy -check -top tq_top; synth -top tq_top -flatten; check -assert -noinit; \
	  select -assert-none t:\$$dlatch t:\$$_DLATCH_*; tee -o build/synth/tq_top_t8_stat.txt stat"

clean:
	rm -rf build verif/formal/tq_fifo verif/formal/tq_fifo_*
