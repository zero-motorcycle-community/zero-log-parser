"""
Tests for entry type 0x51 ("Vehicle State Telemetry") in
Gen2.vehicle_state_telemetry(), after the field fix in
analysis/vst_field_fix.md: odometer_meters/odometer_km/soc_raw/soc_percent/
ambient_temperature_* removed (never confirmed anywhere in this payload -
bytes 0-3 are the entry's own sub-second timestamp fraction, bytes 4-5 the
shared sequence/marker prefix, bytes 8-11 either always zero or an
unidentified state-correlated enum, not temperature); temperature_1..4_celsius
corrected to the real stride-4 offsets (48/52/56/60, not 48/49/50/51); byte 5
exposed raw as marker, unlabeled.

Same conventions as the other files in this directory: plain test_*
functions, stdlib only, synthetic in-process payloads built from the
confirmed layout, no dataset files checked in.
"""

import logging
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from zero_log_parser import Gen2

TELEMETRY_TAGS = ['RUN', 'PWSU', 'CHRG', 'WAIT', 'STOP', 'HIB', 'WAKE', 'FWUP', 'STRT', 'REV', 'PARK']


def _payload(length=64, tag=b'RUN\x00', subsecond=76000, sequence=0x30, marker=0xf9,
             temps=(20, 21, 22, 23)):
    buf = bytearray((i % 100) + 1 for i in range(length))
    if length >= 4:
        struct.pack_into('<I', buf, 0, subsecond)
    if length >= 5:
        buf[4] = sequence
    if length >= 6:
        buf[5] = marker
    if length >= 39:
        buf[35:39] = tag
    for offset, value in zip((48, 52, 56, 60), temps):
        if offset < length:
            buf[offset] = value & 0xff
    return buf


def _tag(name):
    return name.encode('ascii').ljust(4, b'\x00')


def test_decodes_state_prefix_and_temperatures():
    payload = _payload(64, _tag('CHRG'), subsecond=987000, sequence=0x22, marker=0xfc,
                        temps=(30, 25, 18, 17))
    out = Gen2.vehicle_state_telemetry(payload)
    sd = out['structured_data']
    assert sd['vehicle_state'] == 'CHRG'
    assert sd['subsecond_us'] == 987000
    assert sd['sequence'] == 0x22
    assert sd['marker'] == 0xfc
    assert (sd['temperature_1_celsius'], sd['temperature_2_celsius'],
            sd['temperature_3_celsius'], sd['temperature_4_celsius']) == (30, 25, 18, 17)


def test_odometer_and_soc_fields_are_gone():
    sd = Gen2.vehicle_state_telemetry(_payload())['structured_data']
    for key in ('odometer_meters', 'odometer_km', 'soc_raw', 'soc_percent',
                'ambient_temperature_raw', 'ambient_temperature_celsius'):
        assert key not in sd, key


def test_marker_is_not_labelled_utc_offset_or_anything_else():
    sd = Gen2.vehicle_state_telemetry(_payload())['structured_data']
    assert 'marker' in sd
    assert not any('utc' in key.lower() or 'offset' in key.lower() for key in sd)


def test_temperatures_read_from_the_stride_four_offsets_not_48_51():
    payload = _payload(temps=(0, 0, 0, 0))
    payload[48] = 55
    payload[49] = 99  # the old (wrong) temp2 offset - must not be read
    payload[50] = 98  # the old (wrong) temp3 offset - must not be read
    payload[51] = 97  # the old (wrong) temp4 offset - must not be read
    payload[52] = 44
    payload[56] = 33
    payload[60] = 22
    sd = Gen2.vehicle_state_telemetry(payload)['structured_data']
    assert (sd['temperature_1_celsius'], sd['temperature_2_celsius'],
            sd['temperature_3_celsius'], sd['temperature_4_celsius']) == (55, 44, 33, 22)


def test_all_eleven_known_states_decode_including_rev_and_park():
    for name in TELEMETRY_TAGS:
        sd = Gen2.vehicle_state_telemetry(_payload(64, _tag(name)))['structured_data']
        assert sd['vehicle_state'] == name, name


def test_both_real_lengths_decode():
    for length in (64, 68):
        out = Gen2.vehicle_state_telemetry(_payload(length))
        assert 'structured_data' in out, length


def test_riding_and_vehicle_state_event_naming_is_unchanged():
    assert Gen2.vehicle_state_telemetry(_payload(64, _tag('RUN')))['event'] == 'Riding'
    for name in ('PWSU', 'CHRG', 'HIB', 'PARK', 'REV'):
        out = Gen2.vehicle_state_telemetry(_payload(64, _tag(name)))
        assert out['event'] == f'Vehicle State ({name})'


def test_wrong_length_falls_back_to_raw_hex():
    for length in (0, 52, 60, 63, 65, 67, 69, 100):
        out = Gen2.vehicle_state_telemetry(_payload(length, _tag('RUN')))
        assert 'structured_data' not in out, length


def test_unknown_or_malformed_tag_falls_back_to_raw_hex():
    for tag in (b'ABCD', b'RUNX', b'RU\x00N', b'\x00\x00\x00\x00', b'run\x00', b'\xff\xff\xff\xff'):
        out = Gen2.vehicle_state_telemetry(_payload(64, tag))
        assert 'structured_data' not in out, tag


def test_implausible_subsecond_value_falls_back_to_raw_hex():
    assert 'structured_data' in Gen2.vehicle_state_telemetry(_payload(64, subsecond=1000000))
    for value in (1000001, 0x7fffffff, 0xffffffff):
        out = Gen2.vehicle_state_telemetry(_payload(64, subsecond=value))
        assert 'structured_data' not in out, value


def test_raw_hex_preserves_the_whole_payload():
    for length in (64, 68):
        payload = _payload(length)
        sd = Gen2.vehicle_state_telemetry(payload)['structured_data']
        assert bytes.fromhex(sd['raw_hex']) == bytes(payload)


def test_parse_entry_dispatches_end_to_end():
    logger = logging.getLogger('test_vehicle_state_telemetry_decode')
    payload = _payload(68, _tag('PWSU'))
    body = bytes([0x51]) + struct.pack('<I', 1_600_000_000) + bytes(payload)
    raw = bytearray([0xb2, len(body) + 2]) + bytearray(body)
    length, entry, _unhandled = Gen2.parse_entry(raw, 0, 0, logger)
    assert length == len(raw)
    assert entry['event'] == 'Vehicle State (PWSU)'
    assert entry['message_type'] == '0x51'
    assert entry['structured_data']['vehicle_state'] == 'PWSU'
