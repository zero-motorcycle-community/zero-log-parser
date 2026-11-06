"""
Tests for entry types 0x52 and 0x53 in Gen2.vehicle_state_telemetry_tier():
the medium and large tiers of the state-tagged telemetry family whose small
tier is 0x51. The 4-byte NUL-padded ASCII state tag sits at payload offset
39 (0x52) or 43 (0x53); each tier has a variant 4 bytes longer than its base
form (0x52: 81 and 85 bytes; 0x53: 95 and 99, plus a rare 93).

Same conventions as the other files in this directory: plain test_*
functions, stdlib only, synthetic in-process buffers built from the layout
described above, no real log files checked in. Filler bytes are 1..100, which avoids 0xB2 and 0xFE (the entry header
and escape bytes), so the same payload can be wrapped in a raw entry for
the parse_entry() test.
"""

import logging
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from zero_log_parser import Gen2

TIERS = {
    0x52: ('medium', (81, 85), 39),
    0x53: ('large', (93, 95, 99), 43),
}
STATES = ['RUN', 'PWSU', 'CHRG', 'WAIT', 'STOP', 'HIB', 'WAKE', 'FWUP', 'STRT', 'REV', 'PARK']


def _payload(message_type, length, tag=b'RUN\x00', subsecond=76000, prefix=0x02e6,
             tag_offset=None):
    offset = TIERS[message_type][2] if tag_offset is None else tag_offset
    buf = bytearray((i % 100) + 1 for i in range(length))
    struct.pack_into('<I', buf, 0, subsecond)
    struct.pack_into('<H', buf, 4, prefix)
    buf[offset:offset + len(tag)] = tag
    buf[offset - 25] = 0    # validity flag byte: both groups valid
    return buf


def _tag(name):
    return name.encode('ascii').ljust(4, b'\x00')


def test_every_accepted_length_decodes_state_and_tier():
    for message_type, (tier, lengths, _offset) in TIERS.items():
        for length in lengths:
            out = Gen2.vehicle_state_telemetry_tier(message_type, _payload(message_type, length, _tag('CHRG')))
            assert out['event'] == 'Vehicle State Telemetry'
            sd = out['structured_data']
            assert sd['state'] == 'CHRG'
            assert sd['telemetry_tier'] == tier
            assert 'CHRG' in out['conditions'] and tier in out['conditions']


def test_every_known_state_name_decodes():
    for message_type, (_tier, lengths, _offset) in TIERS.items():
        for name in STATES:
            out = Gen2.vehicle_state_telemetry_tier(message_type, _payload(message_type, lengths[0], _tag(name)))
            assert out['structured_data']['state'] == name, (message_type, name)


def test_prefix_fields_and_raw_hex():
    payload = _payload(0x52, 85, _tag('WAKE'), subsecond=987000, prefix=0xfc49)
    sd = Gen2.vehicle_state_telemetry_tier(0x52, payload)['structured_data']
    assert sd['subsecond_us'] == 987000
    assert sd['sequence'] == 0x49
    assert sd['marker'] == 0xfc
    assert bytes.fromhex(sd['raw_hex']) == bytes(payload)


def test_leading_field_is_not_labelled_odometer():
    sd = Gen2.vehicle_state_telemetry_tier(0x53, _payload(0x53, 99))['structured_data']
    assert not any('odo' in key for key in sd)


def test_tag_is_read_at_the_tier_specific_offset():
    # A valid tag at 0x52's offset (39) must not make a 0x53 payload decode:
    # 0x53's own slot (43) holds filler here.
    out = Gen2.vehicle_state_telemetry_tier(0x53, _payload(0x53, 99, _tag('RUN'), tag_offset=39))
    assert 'structured_data' not in out
    assert out['conditions'].startswith('Raw data:')


def test_unaccepted_lengths_fall_back_to_raw_hex():
    # 49/31/9/0 are real truncated captures seen for 0x52 (the 49-byte one
    # even carries a readable tag at 39); 79 and 0 are the same for 0x53.
    cases = [(0x52, 49), (0x52, 31), (0x52, 9), (0x52, 0), (0x52, 82), (0x52, 89),
             (0x53, 79), (0x53, 0), (0x53, 94), (0x53, 97), (0x53, 81)]
    for message_type, length in cases:
        payload = _payload(message_type, length, _tag('CHRG')) if length > 47 else bytearray((i % 100) + 1 for i in range(length))
        out = Gen2.vehicle_state_telemetry_tier(message_type, payload)
        assert 'structured_data' not in out, (message_type, length)


def test_unknown_or_malformed_tag_falls_back_to_raw_hex():
    for tag in (b'ABCD', b'RUNX', b'RU\x00N', b'\x00\x00\x00\x00', b'run\x00', b'\xff\xff\xff\xff'):
        out = Gen2.vehicle_state_telemetry_tier(0x52, _payload(0x52, 81, tag))
        assert 'structured_data' not in out, tag


def test_fallback_keeps_the_unknown_type_label():
    # Anything not decoded reports exactly what it did before this decoder
    # existed, so output for entries that fall back is unchanged.
    for message_type, label in ((0x52, 'Unknown Type 82'), (0x53, 'Unknown Type 83')):
        out = Gen2.vehicle_state_telemetry_tier(message_type, bytearray(b'\x01\x02\x03'))
        assert out['event'] == label


def test_parse_entry_dispatches_both_types_end_to_end():
    logger = logging.getLogger('test_telemetry_tier_decode')
    for message_type, length, state in ((0x52, 85, 'RUN'), (0x53, 99, 'CHRG')):
        payload = _payload(message_type, length, _tag(state))
        body = bytes([message_type]) + struct.pack('<I', 1_600_000_000) + bytes(payload)
        raw = bytearray([0xb2, len(body) + 2]) + bytearray(body)
        consumed, entry, _unhandled = Gen2.parse_entry(raw, 0, 0, logger)
        assert consumed == len(raw)
        assert entry['event'] == 'Vehicle State Telemetry'
        assert entry['message_type'] == '0x%X' % message_type
        assert entry['structured_data']['state'] == state


def test_implausible_subsecond_value_falls_back_to_raw_hex():
    assert 'structured_data' in Gen2.vehicle_state_telemetry_tier(0x52, _payload(0x52, 81, subsecond=1000000))
    for message_type, length in ((0x52, 81), (0x53, 99)):
        for value in (1000001, 0x7fffffff, 0xffffffff):
            out = Gen2.vehicle_state_telemetry_tier(message_type, _payload(message_type, length, subsecond=value))
            assert 'structured_data' not in out, (message_type, value)


def _set_triple(buf, tag_offset, soc, mv, ma):
    buf[tag_offset - 9] = soc
    struct.pack_into('<I', buf, tag_offset - 8, mv)
    struct.pack_into('<i', buf, tag_offset - 4, ma)


def test_pack_triple_decodes_at_tag_relative_offsets_on_every_length():
    for message_type, (_, lengths, tag_offset) in TIERS.items():
        for length in lengths:
            payload = _payload(message_type, length, _tag('RUN'))
            _set_triple(payload, tag_offset, 64, 105144, 12273)
            sd = Gen2.vehicle_state_telemetry_tier(message_type, payload)['structured_data']
            assert sd['state_of_charge_percent'] == 64
            assert sd['pack_voltage_volts'] == 105.144
            assert sd['battery_current_amps'] == 12.273


def test_battery_current_is_signed():
    for message_type, (_, lengths, tag_offset) in TIERS.items():
        payload = _payload(message_type, lengths[0], _tag('CHRG'))
        _set_triple(payload, tag_offset, 50, 99000, -13000)
        out = Gen2.vehicle_state_telemetry_tier(message_type, payload)
        assert out['structured_data']['battery_current_amps'] == -13.0
        assert 'I:-13.000A' in out['conditions']


def test_zero_voltage_and_current_in_hib_are_none_when_the_bms_flag_is_set():
    for message_type, (_, lengths, tag_offset) in TIERS.items():
        payload = _payload(message_type, lengths[0], _tag('HIB'))
        payload[tag_offset - 25] = 0x60
        _set_triple(payload, tag_offset, 71, 0, 0)
        out = Gen2.vehicle_state_telemetry_tier(message_type, payload)
        sd = out['structured_data']
        assert sd['pack_voltage_volts'] is None and sd['battery_current_amps'] is None
        assert sd['state_of_charge_percent'] == 71
        assert 'Vpack:n/aV' in out['conditions']


def test_zero_voltage_with_the_bms_flag_clear_stays_a_raw_zero():
    for message_type, (_, lengths, tag_offset) in TIERS.items():
        payload = _payload(message_type, lengths[0], _tag('RUN'))
        payload[tag_offset - 25] = 0
        _set_triple(payload, tag_offset, 71, 0, 0)
        sd = Gen2.vehicle_state_telemetry_tier(message_type, payload)['structured_data']
        assert sd['pack_voltage_volts'] == 0.0 and sd['battery_current_amps'] == 0.0


def test_rejected_payloads_still_fall_back_to_raw_hex():
    payload = _payload(0x52, 92, _tag('RUN'))
    assert Gen2.vehicle_state_telemetry_tier(0x52, payload)['event'] != 'Vehicle State Telemetry'
