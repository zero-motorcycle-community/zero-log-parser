"""
Tests for entry type 0x48 ("Charger Info") in Gen2.charger_info(): a 6-byte
prefix followed by one 49-byte charger record (55-byte payload) or two
(104-byte payload, the second record using the same layout as the first).

Same conventions as the other files in this directory: plain test_*
functions, stdlib only, synthetic in-process buffers built from the layout
documented in analysis/fst_part1_decoders.md, no dataset files checked in.
Filler bytes in the undecoded regions are 1..100, which avoids 0xB2 and
0xFE (the entry header and escape bytes), so the same payload can be
wrapped in a raw entry for the parse_entry() test.

Field offsets inside each 49-byte record: name 0-9, flags 10-11, measurement
block 12-28 (undecoded), hertz 29, one unidentified byte 30, id 31, version
32-33 (u16), serial number 34-37 (u32), trailer 38-48 (undecoded).
"""

import logging
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from zero_log_parser import Gen2


def _record(name=b'Charger', flags=(0x80, 0x04), hertz=60, charger_id=0x10,
            version=101, serial=1904243, fill=1):
    rec = bytearray(((fill + i) % 100) + 1 for i in range(49))
    rec[0:10] = name.ljust(10, b'\x00')
    rec[10:12] = bytes(flags)
    rec[29] = hertz
    rec[31] = charger_id
    struct.pack_into('<H', rec, 32, version)
    struct.pack_into('<I', rec, 34, serial)
    return rec


def _payload(*records, subsecond=250000, prefix=0xf9aa):
    head = bytearray(struct.pack('<IH', subsecond, prefix))
    out = head
    for rec in records:
        out = out + rec
    return out


def test_one_record_payload_is_55_bytes_and_decodes_every_field():
    payload = _payload(_record())
    assert len(payload) == 55
    out = Gen2.charger_info(payload)
    assert out['event'] == 'Charger Info'
    sd = out['structured_data']
    assert sd['charger_count'] == 1
    assert sd['subsecond_us'] == 250000
    assert sd['sequence'] == 0xaa
    assert sd['marker'] == 0xf9
    c = sd['chargers'][0]
    assert c['name'] == 'Charger'
    assert c['flags'] == 0x0480          # bytes 0x80 0x04, little-endian
    assert c['hertz'] == 60
    assert c['id'] == 0x10
    assert c['version'] == 101
    assert c['serial_number'] == 1904243


def test_two_record_payload_is_104_bytes_and_decodes_both_chargers():
    first = _record(charger_id=0x10, serial=1904243)
    second = _record(name=b'Charger', charger_id=0x11, serial=1909305, hertz=0, fill=9)
    payload = _payload(first, second)
    assert len(payload) == 104
    sd = Gen2.charger_info(payload)['structured_data']
    assert sd['charger_count'] == 2
    assert [c['id'] for c in sd['chargers']] == [0x10, 0x11]
    assert [c['serial_number'] for c in sd['chargers']] == [1904243, 1909305]
    assert [c['hertz'] for c in sd['chargers']] == [60, 0]


def test_names_seen_in_practice_are_stripped_of_padding_and_spaces():
    names = {b' 3kW ': '3kW', b' 6kW ': '6kW', b'Flt 6': 'Flt 6', b' 3kWFCC ': '3kWFCC', b'Charger': 'Charger'}
    for raw, expected in names.items():
        sd = Gen2.charger_info(_payload(_record(name=raw)))['structured_data']
        assert sd['chargers'][0]['name'] == expected


def test_flags_are_not_restricted_to_0x80_0x04():
    # The zlog.yml guess for this field was a constant 0x80 0x40; real
    # records also carry 0x20 in the first byte (no AC input) and others.
    for flags in [(0x80, 0x00), (0x80, 0x04), (0x20, 0x40), (0xa0, 0x01), (0x00, 0x00)]:
        sd = Gen2.charger_info(_payload(_record(flags=flags)))['structured_data']
        assert sd['chargers'][0]['flags'] == flags[0] | (flags[1] << 8)


def test_hertz_is_read_by_position_not_by_searching_for_0x3c():
    # A 50 Hz or unplugged (0) charger has no 0x3C anywhere at the hertz
    # position; the fields after it must still line up.
    for hertz in (0, 50, 51, 60, 61):
        rec = _record(hertz=hertz, charger_id=0x11, version=206, serial=2010113)
        c = Gen2.charger_info(_payload(rec))['structured_data']['chargers'][0]
        assert (c['hertz'], c['id'], c['version'], c['serial_number']) == (hertz, 0x11, 206, 2010113)


def test_undecoded_bytes_are_preserved_per_record():
    rec = _record()
    c = Gen2.charger_info(_payload(rec))['structured_data']['chargers'][0]
    assert bytes.fromhex(c['raw_hex']) == bytes(rec)
    assert len(bytes.fromhex(c['raw_hex'])) == 49


def test_conditions_summarise_each_charger():
    out = Gen2.charger_info(_payload(_record(name=b' 3kW ', serial=1234567, version=206), _record(charger_id=0x11)))
    parts = out['conditions'].split('; ')
    assert len(parts) == 2
    assert '3kW' in parts[0] and '1234567' in parts[0] and 'version 206' in parts[0]


def test_lengths_that_are_not_prefix_plus_whole_records_fall_back():
    good = _payload(_record())
    for length in (0, 5, 6, 54, 56, 103, 105):
        payload = bytearray((i % 100) + 1 for i in range(length)) if length else bytearray()
        out = Gen2.charger_info(payload)
        assert 'structured_data' not in out, length
    # A 6-byte prefix alone (zero records) is not a charger entry either.
    assert 'structured_data' not in Gen2.charger_info(good[:6])


def test_non_printable_or_empty_name_falls_back():
    for name in (b'', b'\x01\x02', b'Char\x00ger', b'\xff\xfe'):
        rec = _record()
        rec[0:10] = name.ljust(10, b'\x00')
        out = Gen2.charger_info(_payload(rec))
        assert 'structured_data' not in out, name
        assert out['conditions'].startswith('Raw data:')


def test_second_record_with_bad_name_falls_back_as_a_whole():
    bad = _record()
    bad[0:10] = b'\x01' * 10
    out = Gen2.charger_info(_payload(_record(), bad))
    assert 'structured_data' not in out


def test_fallback_keeps_the_unknown_type_label():
    # Anything not decoded reports exactly what it did before this decoder
    # existed, so output for entries that fall back is unchanged.
    assert Gen2.charger_info(bytearray(b'\x01\x02\x03'))['event'] == 'Unknown Type 72'


def test_parse_entry_dispatches_end_to_end():
    logger = logging.getLogger('test_charger_info_decode')
    payload = _payload(_record(), _record(charger_id=0x11))
    body = bytes([0x48]) + struct.pack('<I', 1_600_000_000) + bytes(payload)
    raw = bytearray([0xb2, len(body) + 2]) + bytearray(body)
    length, entry, _unhandled = Gen2.parse_entry(raw, 0, 0, logger)
    assert length == len(raw)
    assert entry['event'] == 'Charger Info'
    assert entry['message_type'] == '0x48'
    assert entry['structured_data']['charger_count'] == 2


def test_implausible_subsecond_value_falls_back_to_raw_hex():
    assert 'structured_data' in Gen2.charger_info(_payload(_record(), subsecond=1000000))
    for value in (1000001, 0x7fffffff, 0xffffffff):
        out = Gen2.charger_info(_payload(_record(), subsecond=value))
        assert 'structured_data' not in out, value
