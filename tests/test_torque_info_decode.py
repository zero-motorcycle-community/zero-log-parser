"""
Tests for entry type 0x54 ("Torque Info") in Gen2.torque_info(): a 22-byte
FST/Gen3 MBB entry. Layout from John Myers' (atomicdog) Gen3 log format
datasheet: the shared 6-byte prefix, then four little-endian s32 values at
payload offsets 6 (torque, micro-Nm), 10 (torque demand, percent x 10),
14 (filtered motor power, raw, not watts) and 18 (discharge power limit, W,
-1 when the BMS is not active).

Same conventions as the other files in this directory: plain test_*
functions, stdlib only, synthetic in-process buffers with invented values,
no real log files checked in.
"""

import logging
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from zero_log_parser import Gen2, LogFile


def _payload(torque_unm=0, demand_x10=0, filtered_power=0, power_limit=-1,
             subsecond=250000, sequence=7, marker=1):
    buf = bytearray(22)
    struct.pack_into('<IBB', buf, 0, subsecond, sequence, marker)
    struct.pack_into('<iiii', buf, 6, torque_unm, demand_x10, filtered_power, power_limit)
    return buf


def _mbb_gen3_parser():
    return Gen2._entry_parsers(LogFile.log_type_mbb, rev3=True)[0x54]


def test_positive_torque_while_driving():
    out = Gen2.torque_info(_payload(torque_unm=87_654_321, demand_x10=455,
                                    filtered_power=12_345, power_limit=61_500))
    assert out['event'] == 'Torque Info'
    sd = out['structured_data']
    assert sd['torque_nm'] == 87.654321
    assert sd['torque_demand_percent'] == 45.5
    assert sd['filtered_motor_power_raw'] == 12_345
    assert sd['discharge_power_limit_watts'] == 61_500
    assert sd['bms_power_limit_active'] is True
    assert sd['subsecond_us'] == 250000
    assert sd['sequence'] == 7
    assert sd['marker'] == 1
    assert 'Torque: 87.654321 Nm' in out['conditions']
    assert 'Discharge power limit: 61500 W' in out['conditions']


def test_regen_gives_negative_torque_and_negative_demand():
    sd = Gen2.torque_info(_payload(torque_unm=-23_456_789, demand_x10=-370,
                                   filtered_power=4_321, power_limit=48_000))['structured_data']
    assert sd['torque_nm'] == -23.456789
    assert sd['torque_demand_percent'] == -37.0
    assert sd['discharge_power_limit_watts'] == 48_000


def test_demand_range_ends_decode_as_plus_and_minus_100_percent():
    assert Gen2.torque_info(_payload(demand_x10=1000))['structured_data']['torque_demand_percent'] == 100.0
    assert Gen2.torque_info(_payload(demand_x10=-1000))['structured_data']['torque_demand_percent'] == -100.0


def test_power_limit_minus_one_means_bms_not_active():
    out = Gen2.torque_info(_payload(power_limit=-1))
    sd = out['structured_data']
    assert sd['discharge_power_limit_watts'] is None
    assert sd['bms_power_limit_active'] is False
    assert 'Discharge power limit: BMS not active' in out['conditions']


def test_power_limit_zero_is_a_real_limit_not_inactive():
    sd = Gen2.torque_info(_payload(power_limit=0))['structured_data']
    assert sd['discharge_power_limit_watts'] == 0
    assert sd['bms_power_limit_active'] is True


def test_micro_nm_resolution_is_kept():
    assert Gen2.torque_info(_payload(torque_unm=-400))['structured_data']['torque_nm'] == -0.0004
    assert Gen2.torque_info(_payload(torque_unm=1))['structured_data']['torque_nm'] == 0.000001
    assert Gen2.torque_info(_payload(torque_unm=0))['structured_data']['torque_nm'] == 0


def test_old_odometer_and_sensor_fields_are_gone():
    sd = Gen2.torque_info(_payload(torque_unm=1_000_000, power_limit=50_000))['structured_data']
    for key in ('odometer_meters', 'odometer_km', 'sensor_1_value', 'sensor_2_value',
                'sensor_3_value', 'sensor_4_value', 'status_flags', 'status_hex'):
        assert key not in sd
    assert set(sd) == {'subsecond_us', 'sequence', 'marker', 'torque_nm', 'torque_demand_percent',
                       'filtered_motor_power_raw', 'discharge_power_limit_watts',
                       'bms_power_limit_active'}


def test_wrong_length_falls_back_to_the_raw_hex_report():
    for length in (21, 23, 30):
        buf = bytearray(range(1, length + 1))
        out = Gen2.torque_info(buf)
        assert out == Gen2.unhandled_entry_format(0x54, buf)
        assert 'structured_data' not in out
        assert out['conditions'].startswith('Raw data: ')


def test_sub_second_above_the_fst_maximum_falls_back_to_the_raw_hex_report():
    buf = _payload(subsecond=Gen2.FST_SUBSECOND_MAX_US + 1, torque_unm=5_000_000)
    assert Gen2.torque_info(buf) == Gen2.unhandled_entry_format(0x54, buf)


def test_gen3_mbb_dispatch_uses_torque_info():
    buf = _payload(torque_unm=12_000_000, demand_x10=200, power_limit=75_000)
    assert _mbb_gen3_parser()(buf) == Gen2.torque_info(buf)


def test_parse_entry_end_to_end_in_a_gen3_mbb_file():
    payload = _payload(torque_unm=-5_500_000, demand_x10=-120, filtered_power=99, power_limit=-1)
    body = bytes([0x54]) + struct.pack('<I', 1_700_000_000) + bytes(payload)
    raw = bytes([0xb2, len(body) + 2]) + body
    _length, entry, _unhandled = Gen2.parse_entry(raw, 0, 0, logging.getLogger('test'),
                                                  log_type=LogFile.log_type_mbb, rev3=True)
    assert entry['event'] == 'Torque Info'
    assert entry['structured_data']['torque_nm'] == -5.5
    assert entry['structured_data']['bms_power_limit_active'] is False


def test_every_payload_byte_is_read():
    base = _payload(torque_unm=33_333_333, demand_x10=250, filtered_power=777, power_limit=40_000)
    baseline = Gen2.torque_info(base)['structured_data']
    for offset in range(22):
        flipped = bytearray(base)
        flipped[offset] ^= 0x01 if offset not in (0, 1, 2, 3) else 0x10
        assert Gen2.torque_info(flipped).get('structured_data') != baseline, offset


def test_dispatch_covers_unknown_type_gen3_files_but_not_bms_or_legacy_files():
    buf = _payload(torque_unm=12_000_000)
    assert Gen2._entry_parsers(LogFile.log_type_unknown, rev3=True)[0x54](buf)['event'] == 'Torque Info'
    assert Gen2._entry_parsers(LogFile.log_type_bms, rev3=True)[0x54](buf)['event'] == 'Sensor Data'
    for log_type in (LogFile.log_type_mbb, LogFile.log_type_bms, LogFile.log_type_unknown, None):
        assert Gen2._entry_parsers(log_type, rev3=False)[0x54](buf)['event'] == 'Sensor Data'
