# SPDX-FileCopyrightText: © 2024 Tiny Tapeout
# SPDX-License-Identifier: Apache-2.0
#
# CHROMA chip tests (run on both RTL and the post-layout gate-level netlist).
# Full-frame image and audio checks live in sim/ (CXXRTL), which runs ~100x faster.

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, RisingEdge

H_TOTAL = 800
H_SYNC = 96


def hsync(dut):
    return (dut.uo_out.value.to_unsigned() >> 7) & 1


def rgb(dut):
    return dut.uo_out.value.to_unsigned() & 0b0111_0111


async def reset(dut):
    clock = Clock(dut.clk, 39.722, unit="ns")  # 25.175 MHz
    cocotb.start_soon(clock.start())
    dut.ena.value = 1
    dut.ui_in.value = 0
    dut.uio_in.value = 0
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 10)
    dut.rst_n.value = 1


@cocotb.test()
async def test_audio_pin_direction(dut):
    await reset(dut)
    await ClockCycles(dut.clk, 2)
    assert dut.uio_oe.value.to_unsigned() == 0x80, "only uio[7] (audio) should be an output"


@cocotb.test()
async def test_hsync_timing_and_blanking(dut):
    """hsync period is 800 clocks, pulse is 96 clocks, and RGB is black during sync."""
    await reset(dut)

    falls = []
    widths = []
    prev = 1
    low_since = None
    for cycle in range(H_TOTAL * 4):
        await RisingEdge(dut.clk)
        h = hsync(dut)
        if prev == 1 and h == 0:
            falls.append(cycle)
            low_since = cycle
        if prev == 0 and h == 1 and low_since is not None:
            widths.append(cycle - low_since)
        if h == 0:
            assert rgb(dut) == 0, f"color output during hsync at cycle {cycle}"
        prev = h

    assert len(falls) >= 3, f"expected several hsync pulses, saw {len(falls)}"
    periods = [b - a for a, b in zip(falls, falls[1:])]
    assert all(p == H_TOTAL for p in periods), f"hsync periods {periods}"
    assert widths and all(w == H_SYNC for w in widths), f"hsync widths {widths}"
