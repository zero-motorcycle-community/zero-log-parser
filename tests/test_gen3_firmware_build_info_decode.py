"""
Tests for entry type 0x4E ("Firmware Build Info") in
Gen2.gen3_firmware_build_info(): the FST/Gen3 platform's analogue of the
classic MBB's already-decoded type 0x32 (Gen2.firmware_build_info) - a
build date/time string followed by a flash bank identifier ("banka" /
"bankb"), the same two literal values 0x32 and the MBB firmware image
strings use.

Layout, offsets relative to the payload: the shared 6-byte prefix (see
fst_entry_prefix), two u16 LE values at 6-7/8-9 of unconfirmed meaning,
then a NUL-terminated ASCII date string starting at offset 10 in one of
two formats ("Sep 20 2019 16:26:26" or "2021-11-08_134947"), then zero
padding, then "banka"/"bankb" (NUL-terminated), then - only when
something follows the bank field's own NUL, seen on the 60-byte form -
a short ASCII build identifier. Payloads are exactly 58 or 60 bytes;
anything else falls back to the raw-hex report.

Same conventions as the other files in this directory: plain test_*
functions, stdlib only, synthetic in-process buffers, no real log files
checked in.
"""

import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from zero_log_parser import Gen2


def _payload(date_str=b'Sep 20 2019 16:26:26', bank=b'banka', build_id=b'',
             length=60, subsecond=220000, seq=0, marker=0xfc, u16a=6, u16b=8):
    buf = bytearray(length)
    struct.pack_into('<I', buf, 0, subsecond)
    buf[4] = seq
    buf[5] = marker
    struct.pack_into('<H', buf, 6, u16a)
    struct.pack_into('<H', buf, 8, u16b)
    buf[10:10 + len(date_str)] = date_str
    buf[10 + len(date_str)] = 0
    bank_field = bank + b'\x00'
    bank_start = length - len(bank_field) - (len(build_id) + 1 if build_id else 0)
    buf[bank_start:bank_start + len(bank_field)] = bank_field
    if build_id:
        id_start = bank_start + len(bank_field)
        buf[id_start:id_start + len(build_id)] = build_id
        buf[id_start + len(build_id)] = 0
    return bytes(buf)


def test_c_style_date_and_bank_decode():
    out = Gen2.gen3_firmware_build_info(_payload(
        date_str=b'Sep 20 2019 16:26:26', bank=b'banka', build_id=b'1da02c1', length=60))
    assert out['event'] == 'Firmware Build Info'
    sd = out['structured_data']
    assert sd['build_date'] == '2019-09-20 16:26:26'
    assert sd['flash_bank'] == 'banka'
    assert sd['build_id'] == '1da02c1'
    assert 'banka' in out['conditions']


def test_iso_style_date_decodes():
    out = Gen2.gen3_firmware_build_info(_payload(
        date_str=b'2021-11-08_134947', bank=b'bankb', build_id=b'803fb0ba', length=60))
    sd = out['structured_data']
    assert sd['build_date'] == '2021-11-08 13:49:47'
    assert sd['flash_bank'] == 'bankb'
    assert sd['build_id'] == '803fb0ba'


def test_58_byte_form_without_trailing_build_id():
    out = Gen2.gen3_firmware_build_info(_payload(
        date_str=b'Jun  4 2019 17:39:46', bank=b'bankb', build_id=b'', length=58))
    assert out['event'] == 'Firmware Build Info'
    sd = out['structured_data']
    assert sd['flash_bank'] == 'bankb'
    assert 'build_id' not in sd


def test_shared_prefix_fields_present():
    sd = Gen2.gen3_firmware_build_info(_payload(subsecond=723000, seq=6, marker=2))['structured_data']
    assert sd['subsecond_us'] == 723000
    assert sd['sequence'] == 6
    assert sd['marker'] == 2


def test_wrong_length_falls_back():
    buf = bytearray(_payload(length=60))
    out = Gen2.gen3_firmware_build_info(bytes(buf) + b'\x00\x00')
    assert out['event'] != 'Firmware Build Info'
    assert 'Raw data' in out['conditions']


def test_unparseable_date_falls_back():
    buf = _payload(date_str=b'not a date here')
    out = Gen2.gen3_firmware_build_info(buf)
    assert out['event'] != 'Firmware Build Info'


def test_missing_bank_string_falls_back():
    buf = bytearray(60)
    struct.pack_into('<I', buf, 0, 220000)
    date_str = b'Sep 20 2019 16:26:26'
    buf[10:10 + len(date_str)] = date_str
    buf[10 + len(date_str)] = 0
    # no "banka"/"bankb" anywhere in the rest of the buffer
    out = Gen2.gen3_firmware_build_info(bytes(buf))
    assert out['event'] != 'Firmware Build Info'


def test_subsecond_gate_falls_back():
    buf = bytearray(_payload())
    struct.pack_into('<I', buf, 0, 1_000_001)  # above FST_SUBSECOND_MAX_US
    out = Gen2.gen3_firmware_build_info(bytes(buf))
    assert out['event'] != 'Firmware Build Info'


def test_dispatched_through_parse_entry():
    payload = _payload(date_str=b'Sep 20 2019 16:26:26', bank=b'banka', build_id=b'1da02c1')
    parsers = Gen2._entry_parsers()
    assert 0x4e in parsers
    out = parsers[0x4e](payload)
    assert out['event'] == 'Firmware Build Info'
