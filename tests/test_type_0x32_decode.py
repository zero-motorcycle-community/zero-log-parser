"""
Regression tests for entry type 0x32 ("Firmware Build Info") in
Gen2.firmware_build_info() - a build date/time, build number, and flash
bank identifier, previously undecoded (fell through to
unhandled_entry_format() like any unrecognized type).

Same conventions as tests/test_rev3_detection.py and
tests/test_gen3_header_decode.py (see the former's docstring for why: no
prior test suite existed in this repo to mirror). Unlike those two files,
this one tests the entry decoder directly - Gen2.firmware_build_info(x)
takes the same raw message bytes Gen2.parse_entry() would hand it (the
bytes after the 0xb2 header, length byte, message-type byte, and 4-byte
timestamp), so no LogFile/LogData scaffolding is needed to exercise it.

Byte sequences below are real corpus samples (not synthetic), pulled
directly from decoded entries in the corpus this repo's private analysis
was built against - see analysis/type_0x32.md and
analysis/type_0x32_decode.md for the source files and the extraction
method. Every length variant this repo's corpus scan actually found is
covered: 22 bytes (date only, no trailing fields), 31 bytes (date + a
2-digit build number + flash bank - a length type_0x32.md flagged as
"other/unexplained" before this task's corpus re-scan showed it's just a
shorter build-number digit count, not a fourth format), 32 bytes (the
dominant form), and 38 bytes (date + a longer, hex-shaped build
identifier + flash bank - confirmed here for the first time; previously
"presumed, not confirmed" per type_0x32.md).

The 59-byte case is deliberately NOT a genuine type-0x32 payload: it's a
real corpus occurrence pulled from a raw firmware-image file
(`75-08163-30_..._firmware_banka_...bin`, one of the non-log binaries this
project's CLAUDE.md says to ignore) that happens to carry byte value 0x32
at an entry's type-byte position by coincidence. It's included specifically
to confirm the decoder degrades to the same raw-hex report
unhandled_entry_format() already gives this type, rather than misreading
binary noise as a date.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from zero_log_parser import Gen2, ZERO_TIME_FORMAT
from datetime import datetime


def _hex(s):
    return bytearray(bytes.fromhex(s.replace(' ', '')))


# Real corpus sample, 22 bytes: 538sm7z29fca05015_MBB, byte0=0x38, date only.
SAMPLE_22 = _hex(
    '38 53 65 70 20 31 30 20 32 30 31 39 20 31 30 3a 35 36 3a 34 34 00'
)

# Real corpus sample, 31 bytes: 538SDBZ50HCB06573_MBB, byte0=0x0d,
# 2-digit build number.
SAMPLE_31 = _hex(
    '0d 4a 61 6e 20 20 39 20 32 30 31 37 20 31 39 3a 33 31 3a 31 35 00 '
    '33 33 00 62 61 6e 6b 61 00'
)

# Real corpus sample, 32 bytes: 0_538SDDZ61KCG10738_MBB, byte0=0x1d,
# 3-digit build number, bank b.
SAMPLE_32 = _hex(
    '1d 41 75 67 20 20 39 20 32 30 31 39 20 31 34 3a 32 32 3a 33 32 00 '
    '35 35 32 00 62 61 6e 6b 62 00'
)

# Real corpus sample, 38 bytes: 538smnzb5nca17987_MBB, byte0=0x23,
# 9-character hex-shaped build identifier instead of a plain digit count.
SAMPLE_38 = _hex(
    '23 4a 75 6c 20 32 35 20 32 30 32 33 20 31 34 3a 35 34 3a 34 39 00 '
    '35 34 33 65 35 64 33 37 37 00 62 61 6e 6b 61 00'
)

# Real corpus occurrence, 59 bytes, from a raw firmware-image file
# (not a genuine log entry - see module docstring).
SAMPLE_59_NON_LOG = _hex(
    '1b 17 f8 01 ec 02 2a 08 d3 be f1 0d 0f 05 d1 a2 5d 0a 2a 1e d0 4f f0 '
    '0d 0e 02 e0 be f1 0a 0f 18 d0 e6 b9 be f1 3d 0f 05 d1 0a f1 01 0a ba '
    'f1 02 0f 04 d9 13 e0 4f fa 8e f2 00 2a'
)


def test_22_byte_form_decodes_date_only():
    entry = Gen2.firmware_build_info(SAMPLE_22)
    sd = entry['structured_data']
    assert sd['leading_byte'] == 0x38
    assert sd['build_date'] == datetime(2019, 9, 10, 10, 56, 44).strftime(ZERO_TIME_FORMAT)
    assert 'build_number' not in sd
    assert 'flash_bank' not in sd
    assert entry['event'] == 'Firmware Build Info'


def test_31_byte_form_decodes_two_digit_build_number():
    """The length type_0x32.md's original evidence-gathering pass called
    'other, unexplained' - a real corpus re-scan for this task showed it's
    the same 22-byte-form-plus-suffix shape as the 32-byte form, just with
    a 2-digit build number instead of 3 digits (33 vs 552), one byte
    shorter as a direct result. Not a fourth format."""
    entry = Gen2.firmware_build_info(SAMPLE_31)
    sd = entry['structured_data']
    assert sd['leading_byte'] == 0x0d
    assert sd['build_date'] == datetime(2017, 1, 9, 19, 31, 15).strftime(ZERO_TIME_FORMAT)
    assert sd['build_number'] == 33
    assert isinstance(sd['build_number'], int)
    assert sd['flash_bank'] == 'banka'


def test_32_byte_form_decodes_all_fields():
    entry = Gen2.firmware_build_info(SAMPLE_32)
    sd = entry['structured_data']
    assert sd['leading_byte'] == 0x1d
    assert sd['build_date'] == datetime(2019, 8, 9, 14, 22, 32).strftime(ZERO_TIME_FORMAT)
    assert sd['build_number'] == 552
    assert isinstance(sd['build_number'], int)
    assert sd['flash_bank'] == 'bankb'


def test_38_byte_form_decodes_hex_shaped_build_identifier():
    """Confirmed here for the first time against a real sample -
    type_0x32.md flagged this length as 'presumed same pattern... never
    confirmed'. The build-number field is a 9-character hex-shaped string
    here, not a plain digit count, so it must stay a string rather than
    being coerced to int."""
    entry = Gen2.firmware_build_info(SAMPLE_38)
    sd = entry['structured_data']
    assert sd['leading_byte'] == 0x23
    assert sd['build_date'] == datetime(2023, 7, 25, 14, 54, 49).strftime(ZERO_TIME_FORMAT)
    assert sd['build_number'] == '543e5d377'
    assert isinstance(sd['build_number'], str)
    assert sd['flash_bank'] == 'banka'


def test_non_log_garbage_degrades_to_raw_hex_not_a_guessed_date():
    """A real corpus occurrence of byte value 0x32 at an entry's
    type-byte position inside a raw firmware-image file, not a real log
    entry (see module docstring). Nothing in this payload parses as the
    confirmed date format, so the decoder must fall back to the same
    raw-hex report unhandled_entry_format() gives any undecoded type,
    not synthesize a plausible-looking but wrong date."""
    entry = Gen2.firmware_build_info(SAMPLE_59_NON_LOG)
    assert 'structured_data' not in entry
    assert entry['conditions'].startswith('Raw data:')


def test_empty_payload_degrades_gracefully():
    entry = Gen2.firmware_build_info(bytearray(b''))
    assert entry['conditions'] == 'No additional data'


def test_too_short_for_a_date_degrades_gracefully():
    """One leading byte and nothing else - too short to contain even a
    null-terminated empty date string."""
    entry = Gen2.firmware_build_info(bytearray(b'\x1d'))
    assert 'structured_data' not in entry
