# SPDX-FileCopyrightText: 2026 Harishankar Narayanan
# SPDX-License-Identifier: Apache-2.0
#
# Build the freestanding Tri-Queue C driver as a static library.
#   make -C sw          -> libtq.a
#   make -C sw test     -> pytest against the cycle model
#   make -C sw clean

CC      ?= cc
AR      ?= ar
CFLAGS  ?= -std=c99 -O2 -Wall -Wextra -Werror
PYTHON  ?= python3

OBJ := tq.o

.PHONY: all test clean

all: libtq.a

libtq.a: $(OBJ)
	$(AR) rcs $@ $^

tq.o: tq.c tq.h
	$(CC) $(CFLAGS) -c -o $@ $<

test:
	$(PYTHON) -m pytest test_tq.py -q

clean:
	rm -f $(OBJ) libtq.a
