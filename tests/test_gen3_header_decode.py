"""
Regression tests for the Gen3/REV3 header field decode in
get_version_and_header() - VIN, Model, Board rev., Firmware rev., and
Firmware build read from real offsets instead of hardcoded 'Unknown'.

Same conventions as tests/test_rev3_detection.py (see that file's own
docstring for why: no prior test suite existed in this repo to mirror).
Synthetic in-process byte buffers only, no corpus files checked in.

Background: analysis/atomicdog_gen3_test.md confirmed five real header
fields in Gen3 MBB files - originally proposed in upstream PR #17
("AtomicDog gen3", zlog.yml) and untested against real data until that
report - validated against 418 real files. VIN sits at 0x29 in 97.1% of
files and one byte later, at 0x2A, in a further 2.6%; Model (0x19),
Board id (0x65), Firmware rev. (0x67), and Firmware build (0x6B) shift by
the same amount as VIN when it shifts. This file exercises the clean
case, the shifted case, and the no-valid-VIN fallback case.
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from zero_log_parser import LogFile, LogData, REV3, REV_UNKNOWN
from test_rev3_detection import make_vin, _write_temp, _parse


def build_gen3_buffer(vin, model="SRF", board_id=2, firmware_rev=29,
                       firmware_build="20784ff9", shift=0, total_len=200):
    """A minimal real Gen3-format buffer: raw()[0] == 0xb2 (so this is
    unambiguously routed to REV3 by the untouched, independent first
    clause in the routing condition - this file never touches that
    condition, only what REV3 does with the header once chosen), with
    the five Gen3 fields at AtomicDog's offsets, optionally shifted by
    one byte to exercise the 0x2A fallback the report found necessary
    for 2.6% of real files."""
    buf = bytearray(b"\x00" * total_len)
    buf[0] = 0xb2
    model_off = 0x19 + shift
    vin_off = 0x29 + shift
    board_off = 0x65 + shift
    fwrev_off = 0x67 + shift
    fwbuild_off = 0x6B + shift
    buf[model_off:model_off + len(model)] = model.encode("ascii")
    buf[vin_off:vin_off + 17] = vin.encode("ascii")
    buf[board_off] = board_id
    buf[fwrev_off] = firmware_rev
    buf[fwbuild_off:fwbuild_off + len(firmware_build)] = firmware_build.encode("ascii")
    return bytes(buf)


def test_clean_gen3_file_decodes_all_five_fields():
    vin = make_vin("12051")
    data = build_gen3_buffer(vin, model="SRF", board_id=2, firmware_rev=29,
                              firmware_build="20784ff9", shift=0)
    path = _write_temp(data, suffix=f"_{vin}_MBB.bin")
    try:
        log_version, header = _parse(path)
    finally:
        os.unlink(path)
    assert log_version == REV3
    assert header["VIN"] == vin
    assert header["Model"] == "SRF"
    assert header["Board rev."] == 2
    assert header["Firmware rev."] == 29
    assert header["Firmware build"] == "20784ff9"


def test_shifted_gen3_file_decodes_via_0x2a_fallback():
    """The 1-byte-later layout AtomicDog's spec didn't predict and the
    report couldn't explain with a clean formula - 0x29 must fail to
    validate here, and 0x2A (plus the same shift on every other field)
    must be tried next and succeed."""
    vin = make_vin("12961")
    data = build_gen3_buffer(vin, model="SRF", board_id=2, firmware_rev=18,
                              firmware_build="7fc7d5d", shift=1)
    path = _write_temp(data, suffix=f"_{vin}_MBB.bin")
    try:
        log_version, header = _parse(path)
    finally:
        os.unlink(path)
    assert log_version == REV3
    assert header["VIN"] == vin
    assert header["Model"] == "SRF"
    assert header["Board rev."] == 2
    assert header["Firmware rev."] == 18
    assert header["Firmware build"] == "7fc7d5d"


def test_gen3_file_no_valid_vin_falls_back_to_filename():
    """Neither 0x29 nor 0x2A validates (garbage/no VIN present) - must
    fall back to the filename VIN exactly as before this change, and the
    other four fields must stay 'Unknown' rather than reading whatever
    garbage happens to sit at their offsets. This is also the shape of
    the one known real file (a 644-byte malformed capture) with no
    recoverable header VIN at all - confirmed separately in
    analysis/gen3_header_decode.md that this file doesn't even reach the
    REV3 branch (raw()[0] != 0xb2 for it), but this test still covers
    the code path directly in case a genuine ring-buffer file with a
    corrupted header region ever does."""
    filename_vin = make_vin("99999")
    buf = bytearray(b"\x00" * 200)
    buf[0] = 0xb2
    # leave 0x19/0x29/0x65/0x67/0x6B as null bytes - no valid VIN anywhere
    path = _write_temp(bytes(buf), suffix=f"_{filename_vin}_MBB.bin")
    try:
        log_version, header = _parse(path)
    finally:
        os.unlink(path)
    assert log_version == REV3
    assert header["VIN"] == filename_vin
    assert header["Model"] == "Unknown"
    assert header["Board rev."] == "Unknown"
    assert header["Firmware rev."] == "Unknown"
    assert header["Firmware build"] == "Unknown"
