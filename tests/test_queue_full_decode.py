"""
Tests for entry type 0x4F ("Queue Full") in Gen2.queue_full(): a 24-byte
report of a write to a full FreeRTOS queue. Layout: the shared 6-byte prefix,
a 10-byte NUL-padded ASCII queue name at 6-15, then two little-endian u32
values at 16 and 20.

Same conventions as the other files in this directory: plain test_*
functions, stdlib only, synthetic in-process buffers built from the layout
documented in analysis/issue16_entry_types.md and
analysis/fst_part1_decoders.md, no dataset files checked in.
"""

import logging
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from zero_log_parser import Gen2

QUEUE_NAMES = ['CAN1txQ', 'CAN2txQ', 'logQ', 'CAN1RxQ', 'CAN0RxQ', 'uarttxQ',
               'dashQ', 'uartrxQ', 'pduQ', 'CAN2RxQ', 'lssQ']


def _payload(name=b'logQ', a=8, b=7, subsecond=910000, prefix=0x30fc):
    buf = bytearray(24)
    struct.pack_into('<IH', buf, 0, subsecond, prefix)
    buf[6:16] = name.ljust(10, b'\x00')
    struct.pack_into('<II', buf, 16, a, b)
    return buf


def test_fields_decode_from_their_offsets():
    out = Gen2.queue_full(_payload(b'CAN2txQ', a=16, b=34, subsecond=450000, prefix=0xfc49))
    assert out['event'] == 'Queue Full'
    sd = out['structured_data']
    assert sd['queue_name'] == 'CAN2txQ'
    assert sd['value_a'] == 16
    assert sd['value_b'] == 34
    assert sd['subsecond_us'] == 450000
    assert sd['sequence'] == 0x49
    assert sd['marker'] == 0xfc
    assert 'CAN2txQ' in out['conditions']


def test_all_eleven_known_queue_names_decode():
    for name in QUEUE_NAMES:
        sd = Gen2.queue_full(_payload(name.encode('ascii')))['structured_data']
        assert sd['queue_name'] == name


def test_full_width_u32_values_are_not_truncated():
    sd = Gen2.queue_full(_payload(a=0xffffffff, b=0x01020304))['structured_data']
    assert sd['value_a'] == 0xffffffff
    assert sd['value_b'] == 0x01020304


def test_name_field_may_fill_all_ten_bytes():
    sd = Gen2.queue_full(_payload(b'ABCDEFGHIJ'))['structured_data']
    assert sd['queue_name'] == 'ABCDEFGHIJ'


def test_unlisted_but_well_formed_name_still_decodes():
    sd = Gen2.queue_full(_payload(b'newQ'))['structured_data']
    assert sd['queue_name'] == 'newQ'


def test_wrong_length_falls_back_to_raw_hex():
    for length in (0, 1, 23, 25, 46):
        payload = bytearray((i % 100) + 1 for i in range(length))
        out = Gen2.queue_full(payload)
        assert 'structured_data' not in out, length
        assert out['event'] == 'Unknown Type 79'


def test_empty_or_non_printable_name_falls_back_to_raw_hex():
    for name in (b'', b'\x01\x02', b'lo\x00gQ', b'\xff\xfe\xfd'):
        out = Gen2.queue_full(_payload(name))
        assert 'structured_data' not in out, name
        assert out['conditions'].startswith('Raw data:')


def test_fallback_keeps_the_unknown_type_label():
    # Anything not decoded reports exactly what it did before this decoder
    # existed, so output for entries that fall back is unchanged.
    assert Gen2.queue_full(bytearray(b'\x01\x02\x03'))['event'] == 'Unknown Type 79'


def test_parse_entry_dispatches_end_to_end():
    logger = logging.getLogger('test_queue_full_decode')
    payload = _payload(b'logQ', a=8, b=7)
    body = bytes([0x4f]) + struct.pack('<I', 1_600_000_000) + bytes(payload)
    raw = bytearray([0xb2, len(body) + 2]) + bytearray(body)
    length, entry, _unhandled = Gen2.parse_entry(raw, 0, 0, logger)
    assert length == len(raw)
    assert entry['event'] == 'Queue Full'
    assert entry['message_type'] == '0x4F'
    assert entry['structured_data']['queue_name'] == 'logQ'


def test_implausible_subsecond_value_falls_back_to_raw_hex():
    # Real case: a 24-byte ASCII text entry in a legacy BMS file ("CAN Link I"
    # plus binary-looking bytes) whose leading u32 reads as 1,196,769,861.
    assert 'structured_data' in Gen2.queue_full(_payload(subsecond=1000000))
    for value in (1000001, 1196769861, 0xffffffff):
        out = Gen2.queue_full(_payload(b'CAN Link I', subsecond=value))
        assert 'structured_data' not in out, value
