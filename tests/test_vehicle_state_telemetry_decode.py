"""
Tests for entry type 0x51 ("Vehicle State Telemetry") in
Gen2.vehicle_state_telemetry(): the state prefix, the marker byte, both
payload lengths, and the four temperatures at 48/52/56/60 (tag+13..+25),
which are read once, as signed 32-bit values with flag handling, under
motor_temp_c, inverter_temp_c and pack_temperature_warmest_c/coldest_c.
The earlier unsigned-byte copies temperature_1..4_celsius are gone.

Same conventions as the other files in this directory: plain test_*
functions, stdlib only, synthetic in-process payloads built from the
confirmed layout, no real log files checked in.
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
        buf[10] = 0         # validity flag byte: both groups valid
        buf[40] = 0         # temp_flags: no sensor failed
    for offset, value in zip((48, 52, 56, 60), temps):
        if offset + 4 <= length:
            struct.pack_into('<i', buf, offset, value)
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
    assert (sd['motor_temp_c'], sd['inverter_temp_c'],
            sd['pack_temperature_warmest_c'], sd['pack_temperature_coldest_c']) == (30, 25, 18, 17)


def test_odometer_and_soc_fields_are_gone():
    sd = Gen2.vehicle_state_telemetry(_payload())['structured_data']
    for key in ('odometer_meters', 'odometer_km', 'soc_raw', 'soc_percent',
                'ambient_temperature_raw', 'ambient_temperature_celsius'):
        assert key not in sd, key


def test_marker_is_not_labelled_utc_offset_or_anything_else():
    sd = Gen2.vehicle_state_telemetry(_payload())['structured_data']
    assert 'marker' in sd
    assert not any('utc' in key.lower() or 'offset' in key.lower() for key in sd)


def test_unsigned_byte_temperature_copies_are_gone():
    out = Gen2.vehicle_state_telemetry(_payload())
    for n in range(1, 5):
        assert 'temperature_%d_celsius' % n not in out['structured_data']
    assert 'Temp1:' not in out['conditions'] and 'Temp4:' not in out['conditions']


def test_negative_temperatures_read_signed_not_as_unsigned_bytes():
    sd = Gen2.vehicle_state_telemetry(_payload(temps=(-7, -12, -3, -5)))['structured_data']
    assert (sd['motor_temp_c'], sd['inverter_temp_c'],
            sd['pack_temperature_warmest_c'], sd['pack_temperature_coldest_c']) == (-7, -12, -3, -5)


def test_flagged_sensors_are_withheld_instead_of_shown_as_zero_or_255():
    payload = _payload(temps=(0, -1, 0, -1))
    payload[40] = 0x3c   # temp_flags: motor, inverter, pack highest and pack lowest failed
    sd = Gen2.vehicle_state_telemetry(payload)['structured_data']
    assert (sd['motor_temp_c'], sd['inverter_temp_c'],
            sd['pack_temperature_warmest_c'], sd['pack_temperature_coldest_c']) == (None, None, None, None)


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


def test_decodes_soc_pack_voltage_and_current():
    payload = _payload(64, _tag('RUN'))
    payload[26] = 64
    struct.pack_into('<I', payload, 27, 105144)
    struct.pack_into('<i', payload, 31, 12273)
    sd = Gen2.vehicle_state_telemetry(payload)['structured_data']
    assert sd['state_of_charge_percent'] == 64
    assert sd['pack_voltage_volts'] == 105.144
    assert sd['battery_current_amps'] == 12.273


def test_current_is_signed_and_68_byte_variant_decodes():
    payload = _payload(68, _tag('CHRG'))
    struct.pack_into('<I', payload, 27, 99000)
    struct.pack_into('<i', payload, 31, -13000)
    sd = Gen2.vehicle_state_telemetry(payload)['structured_data']
    assert sd['pack_voltage_volts'] == 99.0
    assert sd['battery_current_amps'] == -13.0


def test_hibernate_zero_fields_are_none_when_the_bms_flag_is_set():
    payload = _payload(64, _tag('HIB'))
    payload[10] = 0x60      # BMS validity group set: the BMS is not reporting
    struct.pack_into('<I', payload, 27, 0)
    struct.pack_into('<i', payload, 31, 0)
    sd = Gen2.vehicle_state_telemetry(payload)['structured_data']
    assert sd['pack_voltage_volts'] is None and sd['battery_current_amps'] is None


def test_zero_voltage_with_the_bms_flag_clear_stays_a_raw_zero():
    payload = _payload(64, _tag('RUN'))
    payload[10] = 0         # BMS validity flag clear: report what was read
    struct.pack_into('<I', payload, 27, 0)
    struct.pack_into('<i', payload, 31, 0)
    sd = Gen2.vehicle_state_telemetry(payload)['structured_data']
    assert sd['pack_voltage_volts'] == 0.0 and sd['battery_current_amps'] == 0.0
