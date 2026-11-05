"""
Tests for Gen2._withhold_marker_corrupted_fields, the generic per-field
corruption detection added after a perturbation test on real logs found
that types 0x4B/0x4C/0x4D's decoded fields can silently read bytes the
00 f0 ff 00 page marker overwrote. These values are corrupted: the marker overwrote the original bytes, so the
value cannot be recovered or verified, matching this repo's existing
bytes_corrupted / corrupted_byte_count convention for entry-level
corruption.

Same conventions as test_fst_page_aware_walker.py: plain test_* functions,
stdlib only, synthetic in-process buffers built with the same _entry/_fill
helpers, Gen2.collect_paged_bms_entries called directly rather than through
a full synthetic file where a test does not need header/log-type detection
too.
"""

import logging
import struct
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from zero_log_parser import Gen2

MARK = b'\x00\xf0\xff\x00'
LOGGER = logging.getLogger('test_marker_field_corruption')


def _entry(message_type, timestamp, payload):
    body = bytes([message_type]) + struct.pack('<I', timestamp) + bytes(payload)
    return bytearray([0xb2, len(body) + 2]) + bytearray(body)


def _cell_telemetry_payload(subsecond_us=500_000, sequence=10, prefix_marker=7,
                             voltage_low=3800, voltage_unloaded=3750, voltage_high=3900,
                             soc=50, current_ma=-5000, pack_voltage_mv=108_000, tail=0xab):
    """A real, valid 0x4B (BMS Cell Telemetry) payload: 43 bytes, base
    offset 14 (Gen2.BMS_CELL_TELEMETRY_FIELD_BASE / TIERS[0x4b]), every
    field set to a distinct, checkable value."""
    payload = bytearray(43)
    struct.pack_into('<I', payload, 0, subsecond_us)
    payload[4] = sequence
    payload[5] = prefix_marker
    struct.pack_into('<H', payload, 14, voltage_low)
    struct.pack_into('<H', payload, 16, voltage_unloaded)
    struct.pack_into('<H', payload, 18, voltage_high)
    payload[20] = soc
    struct.pack_into('<i', payload, 21, current_ma)
    payload[39:42] = pack_voltage_mv.to_bytes(3, 'little')
    payload[42] = tail
    return payload


def _decode_one(buf):
    collected = Gen2.collect_paged_bms_entries(bytes(buf), LOGGER)
    assert len(collected) == 1
    return collected[0][1]


def test_field_whose_bytes_overlap_the_marker_is_corrupted_others_are_not():
    payload = _cell_telemetry_payload()
    # pack_voltage_volts's 3 source bytes are payload[39:42]; overwrite them
    # (plus one unidentified byte) with the page marker, exactly the shape
    # a real page boundary landing there produces.
    payload[39:43] = MARK
    entry = _decode_one(_entry(0x4b, 1_700_000_000, payload))
    sd = entry['structured_data']

    assert sd['corrupted_fields'] == ['pack_voltage_volts']
    assert sd['pack_voltage_volts'] is None
    # Every other decoded field survives untouched, at its real value.
    assert sd['voltage_low_cell_volts'] == 3.8
    assert sd['voltage_unloaded_cell_volts'] == 3.75
    assert sd['voltage_high_cell_volts'] == 3.9
    assert sd['state_of_charge_percent'] == 50
    assert sd['battery_current_amps'] == -5.0
    assert sd['subsecond_us'] == 500_000
    assert sd['sequence'] == 10
    assert sd['marker'] == 7


def test_raw_hex_is_unchanged_by_field_corruption():
    # raw_hex must keep showing the file's real, physical bytes (including
    # the marker itself) - withholding a corrupted field's decoded value
    # is not a second corruption mechanism over the same bytes.
    payload = _cell_telemetry_payload()
    payload[39:43] = MARK
    entry = _decode_one(_entry(0x4b, 1_700_000_000, payload))
    assert bytes.fromhex(entry['structured_data']['raw_hex']) == bytes(payload)


def test_entry_level_bytes_corrupted_is_never_set_by_field_corruption():
    payload = _cell_telemetry_payload()
    payload[39:43] = MARK
    entry = _decode_one(_entry(0x4b, 1_700_000_000, payload))
    sd = entry['structured_data']
    assert 'bytes_corrupted' not in sd
    assert 'corrupted_byte_count' not in sd
    assert entry['message_type'] != 'CORRUPTED'


def test_marker_outside_any_named_field_corrupts_nothing():
    # The marker lands in the unidentified byte range (payload[25:38], never
    # read by any named field) - every field decodes to its real value and
    # corrupted_fields is absent entirely.
    payload = _cell_telemetry_payload()
    payload[25:29] = MARK
    entry = _decode_one(_entry(0x4b, 1_700_000_000, payload))
    sd = entry['structured_data']
    assert 'corrupted_fields' not in sd
    assert sd['pack_voltage_volts'] == 108.0
    assert sd['battery_current_amps'] == -5.0


def test_marker_spanning_two_fields_corrupts_both():
    payload = _cell_telemetry_payload()
    # voltage_high is payload[18:20], soc is payload[20]; a 4-byte marker
    # starting at 18 covers both plus one byte of current_ma.
    payload[18:22] = MARK
    entry = _decode_one(_entry(0x4b, 1_700_000_000, payload))
    sd = entry['structured_data']
    assert set(sd['corrupted_fields']) >= {'voltage_high_cell_volts', 'state_of_charge_percent'}
    assert sd['voltage_high_cell_volts'] is None
    assert sd['state_of_charge_percent'] is None
    # Untouched fields on either side of the corrupted span still decode.
    assert sd['voltage_low_cell_volts'] == 3.8
    assert sd['pack_voltage_volts'] == 108.0


def test_message_type_is_unaffected_by_field_corruption():
    payload = _cell_telemetry_payload()
    payload[39:43] = MARK
    entry = _decode_one(_entry(0x4b, 1_700_000_000, payload))
    assert entry['message_type'] == '0x4B'
    assert entry['event'] == 'BMS Cell Telemetry'


def test_no_marker_in_payload_leaves_entry_byte_identical():
    payload = _cell_telemetry_payload()
    without_marker = _decode_one(_entry(0x4b, 1_700_000_000, payload))
    assert 'corrupted_fields' not in without_marker['structured_data']
    assert without_marker['structured_data']['pack_voltage_volts'] == 108.0


def test_a_decoder_that_raises_on_modified_bytes_corrupts_nothing_rather_than_crash():
    # A synthetic decoder that raises whenever the filler bytes appear -
    # exercises the "not testable" path directly (never observed in the
    # real logs checked, but handled rather than assumed impossible). The marker sits at
    # raw byte 7, not byte 0, so type_from_block still reads the real,
    # unmodified type byte under both fillers and this reaches
    # entry_parser rather than being skipped by the type-changed guard.
    def flaky_parser(x):
        if 0x00 in x or 0xAA in x:
            raise ValueError('synthetic decode failure under perturbation')
        return {'event': 'ok', 'structured_data': {'value': 42}}

    sd = {'value': 42}
    buf = bytearray([0x05]) + b'\x01' * 6 + bytearray(MARK) + b'\x01' * 4
    Gen2._withhold_marker_corrupted_fields(sd, flaky_parser, buf, -2, len(buf), 0x05)
    assert 'corrupted_fields' not in sd
    assert sd['value'] == 42


def test_marker_straddling_the_entrys_own_end_is_still_detected():
    # The marker's own 4 bytes can be split across this entry's own end
    # and whatever comes after (up to 3 of its bytes belonging to a
    # neighbor, or to unrelated trailing data) - this must still be found
    # and clipped to this entry's own [start+2, end) range, not silently
    # missed because the full 4-byte pattern isn't locally contained
    # within this one entry's own bytes. Found missing by this fix's own
    # regression run over real logs.
    payload = _cell_telemetry_payload()
    body = bytes([0x4b]) + struct.pack('<I', 1_700_000_000) + bytes(payload)
    framed = bytearray([0xb2, len(body) + 2]) + bytearray(body)
    buf = bytearray(b'\x01\x01\x01') + framed + bytearray(b'\x01\x01')
    start = 3
    escaped_start = start + 2  # = 5
    end = start + len(framed[2:]) + 2  # = start + length = 53
    # payload starts at buf[escaped_start + 5] (type+timestamp = 5 bytes);
    # pack_voltage_mv is payload[39:42], i.e. buf[escaped_start+5+39 : +42].
    payload_base = escaped_start + 5
    # Place the marker so its first 2 bytes (payload[41], the unidentified
    # tail byte payload[42]) sit inside [escaped_start, end), and its last
    # 2 bytes fall past `end`, into the trailing filler.
    mark_start = payload_base + 41
    assert mark_start + 4 == end + 2  # sanity: straddles end by 2 bytes
    buf[mark_start:mark_start + 4] = MARK

    collected = Gen2.collect_paged_bms_entries(bytes(buf), LOGGER)
    assert len(collected) == 1
    sd = collected[0][1]['structured_data']
    assert 'pack_voltage_volts' in sd.get('corrupted_fields', [])
    assert sd['pack_voltage_volts'] is None
    # Untouched fields decode normally.
    assert sd['voltage_low_cell_volts'] == 3.8
