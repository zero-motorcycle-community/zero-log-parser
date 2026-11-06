"""
Tests for the 0x51 / 0x52 / 0x53 fields whose meaning depends on the MBB
firmware revision an entry was written under (Gen2.telemetry_extended_fields,
Gen2.telemetry_ambient, Gen2.telemetry_flag_map_is_shifted and
Gen3TelemetryFirmware), and for the final field names: temp_flags,
motor_temp_c, inverter_temp_c, speed_kmh and range_km.

Layouts and revision rules from John Myers' (atomicdog) Gen3 log format
datasheet: ambient is C x 100 on rev 13-17 and 20+ and plain C on rev 18-19;
temp_flags bits 1-5 (ambient, motor, inverter, pack warmest, pack coldest) on
rev 18+, one bit higher on rev 13-17.

Same conventions as the other files in this directory: plain test_*
functions, stdlib only, synthetic in-process buffers with invented values,
no real log files checked in.
"""

import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from zero_log_parser import Gen2, Gen3TelemetryFirmware

TAG = {0x51: 35, 0x52: 39, 0x53: 43}
TEMPS = ('ambient_temperature_c', 'motor_temp_c', 'inverter_temp_c',
         'pack_temperature_warmest_c', 'pack_temperature_coldest_c')
T0 = 1_700_000_000


def _payload(message_type=0x52, length=85, status=0, temp_flags=0, ambient=2350,
             temps=(41, 33, 22, 21), speed_mm_h=56_250_000, range_raw=12345):
    tag = TAG[message_type]
    buf = bytearray(length)
    struct.pack_into('<IBB', buf, 0, 400000, 3, 1)
    buf[tag:tag + 4] = b'RUN\x00'
    buf[tag - 25] = status
    buf[tag - 21:tag - 18] = (104000).to_bytes(3, 'little')
    struct.pack_into('<i', buf, tag - 17, 25000)
    struct.pack_into('<H', buf, tag - 13, 3100)
    buf[tag - 9] = 77
    struct.pack_into('<I', buf, tag - 8, 108000)
    struct.pack_into('<i', buf, tag - 4, 9000)
    buf[tag + 5] = temp_flags
    struct.pack_into('<i', buf, tag + 9, ambient)
    for rel, value in zip((13, 17, 21, 25), temps):
        struct.pack_into('<i', buf, tag + rel, value)
    if (message_type, length) in ((0x52, 85), (0x53, 99)):
        struct.pack_into('<i', buf, tag + 39, speed_mm_h)
        struct.pack_into('<H', buf, tag + 43, range_raw)
    elif (message_type, length) in ((0x52, 81), (0x53, 95)):
        struct.pack_into('<i', buf, tag + 35, speed_mm_h)
        struct.pack_into('<H', buf, tag + 39, range_raw)
    return buf


def _sd(payload, firmware=None, timestamp=T0, message_type=0x52):
    if message_type == 0x51:
        out = Gen2.vehicle_state_telemetry(payload, firmware=firmware, timestamp=timestamp)
    else:
        out = Gen2.vehicle_state_telemetry_tier(message_type, payload, firmware=firmware, timestamp=timestamp)
    return out['structured_data']


def _fw(header_rev, updates=(), first_x100=None):
    return Gen3TelemetryFirmware(header_rev, updates, first_x100)


# ---- temp_flags ----

def test_each_temp_flags_bit_withholds_its_temperature_on_the_rev_18_map():
    for rev in (18, 21, 44):
        for bit, name in enumerate(TEMPS, start=1):
            sd = _sd(_payload(temp_flags=1 << bit), _fw(rev, first_x100=T0 - 60))
            assert sd[name] is None, (rev, name)
            for other in TEMPS:
                if other != name:
                    assert sd[other] is not None, (rev, name, other)


def test_each_temp_flags_bit_withholds_its_temperature_on_the_shifted_rev_13_17_map():
    for rev in (13, 15, 17):
        for bit, name in enumerate(TEMPS, start=2):
            sd = _sd(_payload(temp_flags=1 << bit), _fw(rev))
            assert sd[name] is None, (rev, name)
            for other in TEMPS:
                if other != name:
                    assert sd[other] is not None, (rev, name, other)


def test_bit_0_and_bit_6_follow_the_map():
    # bit 6 is pack coldest on the shifted map and unused on the rev 18 map
    assert _sd(_payload(temp_flags=0x40), _fw(15))['pack_temperature_coldest_c'] is None
    assert _sd(_payload(temp_flags=0x40), _fw(21, first_x100=T0))['pack_temperature_coldest_c'] == 21
    # bit 1 is ambient on the rev 18 map and unused on the shifted map
    assert _sd(_payload(temp_flags=0x02), _fw(15))['ambient_temperature_c'] == 23.5
    assert all(_sd(_payload(temp_flags=0x01), _fw(21, first_x100=T0))[n] is not None for n in TEMPS)


def test_a_status_bit_that_belongs_to_one_map_overrides_the_firmware_revision():
    # status 0x80 occurs only in the shifted map: bit 2 of temp_flags is then ambient
    sd = _sd(_payload(status=0x80, temp_flags=0x04), _fw(21, first_x100=T0))
    assert sd['ambient_temperature_c'] is None and sd['motor_temp_c'] == 41
    # status 0x20 occurs only in the rev 18 map: bit 2 is then the motor temperature
    sd = _sd(_payload(status=0x20, temp_flags=0x04), _fw(15))
    assert sd['motor_temp_c'] is None and sd['ambient_temperature_c'] == 23.5
    assert Gen2.telemetry_flag_map_is_shifted(0x14, 21) is True
    assert Gen2.telemetry_flag_map_is_shifted(0x0a, 15) is False
    assert Gen2.telemetry_flag_map_is_shifted(0x40, 15) is True      # bit 6 is in both maps
    assert Gen2.telemetry_flag_map_is_shifted(0x40, 21) is False
    assert Gen2.telemetry_flag_map_is_shifted(0, None) is False


# ---- ambient scale ----

def test_ambient_is_plain_celsius_on_rev_18_and_19():
    for rev in (18, 19):
        assert _sd(_payload(ambient=23), _fw(rev))['ambient_temperature_c'] == 23.0
        assert _sd(_payload(ambient=-7), _fw(rev))['ambient_temperature_c'] == -7.0


def test_ambient_is_celsius_x100_on_rev_13_17_and_20_plus():
    for rev in (13, 17, 20, 46):
        assert _sd(_payload(ambient=2350), _fw(rev))['ambient_temperature_c'] == 23.5
        assert _sd(_payload(ambient=-725), _fw(rev))['ambient_temperature_c'] == -7.25
    # a reading near 0 C on rev 13-17 is still x100
    assert _sd(_payload(ambient=23), _fw(15))['ambient_temperature_c'] == 0.23


def test_a_raw_value_outside_the_plain_range_is_x100_even_on_rev_18_19():
    assert _sd(_payload(ambient=2350), _fw(19))['ambient_temperature_c'] == 23.5


def test_near_zero_reading_on_rev_20_plus_is_x100_after_an_earlier_x100_reading():
    sd = _sd(_payload(ambient=23), _fw(21, first_x100=T0 - 120), timestamp=T0)
    assert sd['ambient_temperature_c'] == 0.23


def test_unsettled_ambient_is_withheld():
    # rev 20+, raw value in the plain range, no earlier x100 reading in the file
    assert _sd(_payload(ambient=23), _fw(21))['ambient_temperature_c'] is None
    # the only x100 reading is later than this entry
    assert _sd(_payload(ambient=23), _fw(21, first_x100=T0 + 60))['ambient_temperature_c'] is None
    # a 0x4E update to rev 19 between the x100 reading and this entry
    fw = _fw(21, updates=[(T0 - 60, 21, 19), (T0 + 600, 19, 21)], first_x100=T0 - 120)
    assert fw.rev_at(T0) == 19
    assert _sd(_payload(ambient=23), fw)['ambient_temperature_c'] == 23.0
    fw = _fw(21, updates=[(T0 - 60, 21, 19)], first_x100=T0 - 120)
    assert fw.x100_seen_before(T0 + 30) is False
    # no firmware context at all
    assert _sd(_payload(ambient=23))['ambient_temperature_c'] is None


def test_implausible_ambient_is_withheld():
    for rev in (18, 21):
        assert _sd(_payload(ambient=-9500), _fw(rev, first_x100=T0))['ambient_temperature_c'] is None
        assert _sd(_payload(ambient=-4100), _fw(rev, first_x100=T0))['ambient_temperature_c'] is None
        assert _sd(_payload(ambient=8100), _fw(rev, first_x100=T0))['ambient_temperature_c'] is None
        assert _sd(_payload(ambient=-4000), _fw(rev, first_x100=T0))['ambient_temperature_c'] == -40.0
        assert _sd(_payload(ambient=8000), _fw(rev, first_x100=T0))['ambient_temperature_c'] == 80.0


def test_entries_before_a_0x4e_update_use_the_previous_revision():
    fw = _fw(23, updates=[(T0, 19, 23)])
    assert fw.rev_at(T0 - 1) == 19 and fw.rev_at(T0) == 23 and fw.rev_at(T0 + 1) == 23
    assert _sd(_payload(ambient=30), fw, timestamp=T0 - 3600)['ambient_temperature_c'] == 30.0
    fw = _fw(30, updates=[(T0, 27, 30), (T0 - 86400, 21, 27)])
    assert fw.rev_at(T0 - 100000) == 21 and fw.rev_at(T0 - 3600) == 27
    # an entry that only repeats the revision is not an update
    assert _fw(21, updates=[(T0, 21, 21)]).updates == []
    # a header revision that is not a number is unknown
    assert _fw('Unknown').rev_at(T0) is None


# ---- speed and range ----

def test_speed_is_km_per_hour_from_mm_per_hour():
    assert _sd(_payload(speed_mm_h=56_250_000))['speed_kmh'] == 56.25
    assert _sd(_payload(speed_mm_h=18_750))['speed_kmh'] == 0.01875
    assert _sd(_payload(speed_mm_h=0))['speed_kmh'] == 0
    assert _sd(_payload(0x53, 95, speed_mm_h=37_500_000), message_type=0x53)['speed_kmh'] == 37.5


def test_negative_speed_is_none():
    for value in (-1, -45_000):
        assert _sd(_payload(speed_mm_h=value))['speed_kmh'] is None


def test_range_is_km_from_hundredths():
    assert _sd(_payload(range_raw=12345))['range_km'] == 123.45
    assert _sd(_payload(range_raw=0))['range_km'] == 0
    assert _sd(_payload(0x52, 81, range_raw=9000))['range_km'] == 90.0


def test_range_0xffff_is_none():
    assert _sd(_payload(range_raw=0xFFFF))['range_km'] is None


# ---- names ----

def test_renamed_keys_are_present_and_the_old_keys_are_gone():
    for message_type, length in ((0x51, 64), (0x51, 68), (0x52, 81), (0x52, 85), (0x53, 95), (0x53, 99)):
        sd = _sd(_payload(message_type, length), _fw(21, first_x100=T0), message_type=message_type)
        for key in ('temp_flags', 'motor_temp_c', 'inverter_temp_c'):
            assert key in sd, (hex(message_type), length, key)
        for key in ('validity_flags_copy', 'drive_temperature_1_c', 'drive_temperature_2_c',
                    'speed_raw', 'range_estimate_raw', 'temperature_1_celsius', 'temperature_2_celsius',
                    'temperature_3_celsius', 'temperature_4_celsius'):
            assert key not in sd, (hex(message_type), length, key)
        has_tail = message_type != 0x51
        assert ('speed_kmh' in sd) is has_tail and ('range_km' in sd) is has_tail
    out = Gen2.vehicle_state_telemetry_tier(0x52, _payload(), firmware=_fw(21, first_x100=T0), timestamp=T0)
    for text in ('Tamb:23.50C', 'Tmotor:41C', 'Tinverter:33C', 'Tpack:22/21C', 'Speed:56.25km/h', 'Range:123.45km'):
        assert text in out['conditions'], text


# ---- the per-file pass ----

def _firmware_update_entry(timestamp, previous, new):
    buf = bytearray(58)
    struct.pack_into('<IBB', buf, 0, 123000, 5, 1)
    struct.pack_into('<HH', buf, 6, previous, new)
    text = b'Mar  3 2021 10:11:12'
    buf[10:10 + len(text)] = text
    buf[52:58] = b'bankb\x00'
    entry = dict(Gen2.gen3_firmware_build_info(buf))
    entry.update(message_type='0x4E', original_timestamp=timestamp)
    return entry


def _telemetry_entry(timestamp, **kw):
    entry = dict(Gen2.vehicle_state_telemetry_tier(0x52, _payload(**kw)))
    entry.update(message_type='0x52', original_timestamp=timestamp)
    return entry


def test_redecode_applies_the_header_revision_and_0x4e_updates_to_each_entry():
    old = _telemetry_entry(T0 - 7200, ambient=30)           # before the update: rev 19, plain C
    new = _telemetry_entry(T0 + 7200, ambient=31)           # after the update and after an x100 reading: x100
    warm = _telemetry_entry(T0 + 3600, ambient=1850)        # x100 by value
    update = _firmware_update_entry(T0, 19, 23)
    collected = [(0, e, i) for i, e in enumerate([old, update, new, warm])]
    firmware = Gen3TelemetryFirmware.redecode(collected, 23)
    assert firmware.rev_at(T0 - 1) == 19 and firmware.first_x100_timestamp == T0 + 3600
    assert old['structured_data']['ambient_temperature_c'] == 30.0
    assert warm['structured_data']['ambient_temperature_c'] == 18.5
    assert new['structured_data']['ambient_temperature_c'] == 0.31   # after an earlier x100 reading
    assert 'Tamb:30.00C' in old['conditions']


def test_temp_flags_renders_in_text_output_without_a_temperature_unit():
    from test_undecoded_and_corrupted_rendering import _entry, _run_txt
    text = _run_txt(_entry(0x52, T0, _payload(temp_flags=0x02)))
    assert 'Temp Flags: 2,' in text
    assert 'Temp Flags: 2°C' not in text
    assert 'Motor Temp C: 41°C' in text
