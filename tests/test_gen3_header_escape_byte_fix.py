"""
Tests for the Gen3 header escape-byte fix.

get_version_and_header()'s REV3 branch read Board rev./Firmware rev./
Firmware build from fixed whole-file byte offsets taken directly from
the raw file bytes, with no concept of the entry-level byte-stuffing
(BinaryTools.unescape_block) every real entry decoder in this parser
already accounts for. A literal 0xfe escape byte anywhere in the entry
before those offsets silently shifted what got read there - confirmed
on 7 real files, all misreading Board rev./Firmware rev. as 0/0.

Fix: get_version_and_header() now also tries entry type 0xfb's own
decode (Gen2.mbb_system_information(), reused directly rather than
duplicated) when byte 0 is 0xb2, and prefers its VIN/Model/Board rev./
Firmware rev. when it succeeds. Firmware build is left exactly as the
fixed-offset method computes it either way, since the entry decoder
doesn't read that field.

Real samples: truncated real files (first 140 bytes, enough to cover
every fixed offset this function reads, hex-embedded the same way
tests/test_type_0xfb_system_information.py embeds message bytes).
Synthetic samples reuse test_gen3_header_decode.py's build_gen3_buffer
helper for the "override must decline" cases, the same convention
that file already established.
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from zero_log_parser import LogFile, LogData, REV3
from test_rev3_detection import _write_temp, _parse
from test_gen3_header_decode import build_gen3_buffer


def _write_and_parse(data, filename):
    path = _write_temp(data, suffix=f"_{filename}")
    try:
        return _parse(path)
    finally:
        os.unlink(path)


# 20200116_01.16_538ZFAZ7XLCK90009_MBB1.bin, first 140 bytes. The raw
# entry (before unescape_block runs) carries one literal 0xfe byte at
# raw offset 0x5D, between this file's Model field and its Board rev./
# Firmware rev. fields - confirmed present at this exact position
# before writing this fix. Real values,
# independently confirmed via Gen2.mbb_system_information(): Board
# rev. 2, Firmware rev. 6.
ESCAPE_BYTE_FILE_TRUNCATED = bytes.fromhex(
    'b274fb19b81f5e581501003afb4d424200000000000000e84e53524600000000000000'
    '0000000000003533385a46415a37584c434b39303030390000736a343031387a657239'
    '3930340000000000000000000000000000000000000058fe4d00000000000000020006'
    '00646662376132370000000000000000000000000000004f50454e00b229fdca5a195e'
)

# 20200205_15.13_538ZFAZ76LCK90010_MBB.bin, first 140 bytes - a clean
# file, no escape byte anywhere in this span, both access paths already
# agreed before this fix. Board rev. 2, Firmware rev. 14, Firmware
# build "1da02c1".
CLEAN_FILE_TRUNCATED = bytes.fromhex(
    'b275fb83da3a5ee80300003b004d424200000000000000e84e53524600000000000000'
    '0000000000003533385a46415a37364c434b39303031300000534a303431395a455239'
    '3930310000000000000000000000000000000000000034302d30383135350002000e00'
    '26013164613032633100000000000000000000000000006174732c2048696265726e61'
)


def test_escape_byte_file_now_decodes_board_rev_and_firmware_rev_correctly():
    log_version, header = _write_and_parse(
        ESCAPE_BYTE_FILE_TRUNCATED, '538ZFAZ7XLCK90009_MBB1.bin')
    assert log_version == REV3
    assert header['VIN'] == '538ZFAZ7XLCK90009'
    assert header['Model'] == 'SRF'
    assert header['Board rev.'] == 2
    assert header['Firmware rev.'] == 6


def test_escape_byte_file_reproduces_the_bug_on_unpatched_logic():
    # Confirms the test fixture is meaningful, not coincidentally
    # passing: reading Board rev./Firmware rev. straight off the raw
    # bytes at their fixed offsets (bypassing this fix entirely, the
    # same way the header code did before it) gives the wrong values on
    # this exact fixture.
    data = ESCAPE_BYTE_FILE_TRUNCATED
    board_rev_raw = data[0x65]
    firmware_rev_raw = data[0x67]
    assert board_rev_raw == 0
    assert firmware_rev_raw == 0


def test_clean_file_unaffected_including_firmware_build():
    log_version, header = _write_and_parse(
        CLEAN_FILE_TRUNCATED, '538ZFAZ76LCK90010_MBB.bin')
    assert log_version == REV3
    assert header['VIN'] == '538ZFAZ76LCK90010'
    assert header['Model'] == 'SRF'
    assert header['Board rev.'] == 2
    assert header['Firmware rev.'] == 14
    assert header['Firmware build'] == '1da02c1'


def test_firmware_build_left_as_fixed_offset_computed_even_when_escape_present():
    # Deliberately not fixed by this change:
    # the entry decoder doesn't read Firmware build at all, so this
    # field stays exactly what the old fixed-offset method produces,
    # bug and all, on the escape-byte file.
    _log_version, header = _write_and_parse(
        ESCAPE_BYTE_FILE_TRUNCATED, '538ZFAZ7XLCK90009_MBB1.bin')
    assert header['Firmware build'] == 'fb7a27'


def test_override_declines_when_entry_at_address_zero_is_not_type_0xfb():
    # A synthetic file with a real, valid Gen3 header at every fixed
    # offset the old method reads, but the entry at address 0 is some
    # other type (0x51 here) - the override must not fire, and every
    # field must come out exactly as the fixed-offset method alone
    # would produce, unchanged from before this fix.
    vin = '538SD5Z2XECB90002'
    buf = build_gen3_buffer(vin, model='SRF', board_id=4, firmware_rev=9,
                             firmware_build='abc1234', shift=0, total_len=200)
    buf = bytearray(buf)
    buf[1] = 200  # entry length byte, spans the whole synthetic buffer
    buf[2] = 0x51  # type byte: not 0xfb
    log_version, header = _write_and_parse(bytes(buf), f'{vin}_MBB.bin')
    assert log_version == REV3
    assert header['VIN'] == vin
    assert header['Model'] == 'SRF'
    assert header['Board rev.'] == 4
    assert header['Firmware rev.'] == 9
    assert header['Firmware build'] == 'abc1234'


def test_override_declines_when_no_coherent_entry_framing_present():
    # No 0xb2-headed entry with a plausible length/type at address 0 at
    # all (byte 1 stays 0, so the "entry" span is empty) - the override
    # must no-op silently, same fixed-offset values as always. This is
    # also exercised implicitly by every test in
    # test_gen3_header_decode.py (unchanged by this fix), but
    # covered directly here too.
    vin = '538SD5Z2XECB90002'
    data = build_gen3_buffer(vin, model='SRF', board_id=2, firmware_rev=29,
                              firmware_build='20784ff9', shift=0)
    log_version, header = _write_and_parse(data, f'{vin}_MBB.bin')
    assert log_version == REV3
    assert header['VIN'] == vin
    assert header['Board rev.'] == 2
    assert header['Firmware rev.'] == 29
