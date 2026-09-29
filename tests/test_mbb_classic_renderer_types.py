"""
Regression tests for the classic (REV0/REV1) MBB entry types named by the
MBB firmware's own event-log renderer (analysis/mbb_firmware_strings.md):

- 0x3d Sevcon Failed To Fully Precharge (previously misread as "Battery
  module N contactor closed" from byte 0)
- 0x1c BMS Disable - Low Bat, 0x1e BMS Disable - High Temp, 0x1f BMS
  Disable - Low Temp, 0x20 Batt Temp (Okay / High Stage N / Low), 0x26 High
  Mot/Ctrl, 0x35 Exceeded Max Charge Amps (5- and 6-byte forms), all
  previously raw-hex fallbacks

Same conventions as tests/test_type_0x0f_decode.py: decoders are called
directly on the raw message bytes Gen2.parse_entry() would hand them.
Every byte sequence is a real payload from the file set, pulled with the
real entry walker; the source file and entry time are given for each.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from zero_log_parser import Gen2


def _hex(s):
    return bytearray(bytes.fromhex(s.replace(' ', '')))


def _decode(message_type, payload):
    return Gen2._entry_parsers()[message_type](payload)


# 0_538SDDZ61KCG10738_MBB_2019-11-29.bin, 2019-07-07 13:46:49.
SAMPLE_3D = _hex('d6 00 01 00')
# 538SD4Z25DCB02997_MBB_2019-08-07.bin (a 2013 model year bike), entry
# stamped 2019-03-29 13:17:46: the early-firmware 1-byte form, module 0,
# right after a "Module 00 FETs are now Closed" debug string.
SAMPLE_3D_CONTACTOR_CLOSED = _hex('00')

# 538SM5Z21ECA03455_MBB_2016-09-10.bin, 2016-09-08 03:10:22.
SAMPLE_1C = _hex('f7 57 01 00 00 01 30 02')

# 0_538SDDZ61KCG10738_MBB_2019-11-29.bin, 2019-07-08 00:12:17: a 0x1e and
# the 0x20 High Temp Stage 2 entry the walker yields right after it, same
# second, same pack temp and module.
SAMPLE_1E = _hex('32 00 06 43')
SAMPLE_20_STAGE2_PAIRED = _hex('02 32 00')
# 538SMMZ48NCA21916_MBB_2022-07-11.bin, 2022-06-25 23:28:40: -100 C, the
# firmware's invalid-thermistor value.
SAMPLE_1E_INVALID_THERMISTOR = _hex('9c 00 36 21')

# 538SD7Z22FCB05294_MBB_2018-04-12.bin, 2018-03-15 11:25:50: a 0x1f and the
# 0x20 Batt Low Temp entry right after it, same second.
SAMPLE_1F = _hex('fe 00 08 63')
SAMPLE_20_LOW_PAIRED = _hex('03 fe 00')

# 0_538SDDZ61KCG10738_MBB_2019-11-29.bin, 2019-07-08 01:23:10.
SAMPLE_20_OKAY = _hex('00 31 00')
# 5.3.182014NewBatterBikeLog.bin, 2018-03-28 16:04:32.
SAMPLE_20_STAGE1 = _hex('01 3d 00')
# 538SDDZ61JCG10074_MBB_2025-09-21.bin, 2025-04-05 05:29:57: state byte
# 0x2c, which the firmware renderer has no name for.
SAMPLE_20_UNNAMED_STATE = _hex('2c 20 00')

# 0_538SD5Z2XECB04302_MBB_2019-10-04.bin, 2019-09-16 07:37:18.
SAMPLE_26 = _hex('64 00 2b 00 18 2c')

# 538SD8Z20FCB04991_MBB_2018-02-07.bin, 2018-02-01 14:41:27 (5-byte form).
SAMPLE_35_SHORT = _hex('00 19 02 f2 ff')
# 538SDJZ61MCG16265_MBB_2022-08-05.bin, 2022-08-02 13:26:48 (6-byte form).
SAMPLE_35_LONG = _hex('01 e4 ff 02 d7 ff')


def test_dispatched_and_described():
    parsers = Gen2._entry_parsers()
    assert parsers[0x3d] == Gen2.contactor_closed_or_precharge_failed
    assert parsers[0x1c] == Gen2.bms_disable_low_bat
    assert parsers[0x20] == Gen2.batt_temp_status
    assert parsers[0x26] == Gen2.high_motor_controller_temp
    assert parsers[0x35] == Gen2.exceeded_max_charge_amps
    for t, name in [(0x3d, 'Sevcon Failed To Fully Precharge'), (0x1c, 'BMS Disable - Low Bat'),
                    (0x1e, 'BMS Disable - High Temp'), (0x1f, 'BMS Disable - Low Temp'),
                    (0x20, 'Batt Temp'), (0x26, 'High Mot/Ctrl'), (0x35, 'Exceeded Max Charge Amps')]:
        assert Gen2.get_message_type_description(t) == name


def test_0x3d_is_capacitor_voltage_not_module_number():
    e = _decode(0x3d, SAMPLE_3D)
    assert e['event'] == 'Sevcon Failed To Fully Precharge'
    assert e['structured_data'] == {'capacitor_voltage_volts': 65.75}
    assert 'module' not in e['event'].lower()
    assert 'contactor' not in e['event'].lower()


def test_0x3d_four_byte_form_log_level_is_warning_not_error():
    # determine_log_level() would call this ERROR on the word "Failed" in
    # the event name alone - a side effect of the 2026-09-24 rename, not a
    # deliberate severity choice. The decoder now overrides it explicitly.
    e = _decode(0x3d, SAMPLE_3D)
    assert e['log_level'] == 'WARNING'


def test_0x3d_one_byte_form_is_still_module_contactor_closed():
    e = _decode(0x3d, SAMPLE_3D_CONTACTOR_CLOSED)
    assert e['event'] == 'Battery module 00 contactor closed'
    assert e['structured_data']['module_number'] == 0


def test_0x3d_other_lengths_fall_back():
    e = _decode(0x3d, _hex('d6 00 01'))
    assert 'structured_data' not in e
    assert e['conditions'].startswith('Raw data:')


def test_0x1c_low_bat():
    e = _decode(0x1c, SAMPLE_1C)
    assert e['event'] == 'BMS Disable - Low Bat'
    d = e['structured_data']
    assert d['pack_sum_voltage_volts'] == 88.055
    assert d['capacity_percent'] == 0
    assert d['module_number'] == 1
    assert d['status_flags'] == 0x0230
    assert d['status_hex'] == '0x0230'


def test_0x1e_high_temp_matches_paired_0x20():
    e = _decode(0x1e, SAMPLE_1E)
    assert e['event'] == 'BMS Disable - High Temp'
    d = e['structured_data']
    assert (d['pack_temp_celsius'], d['module_number'], d['status_hex']) == (50, 0, '0x4306')
    p = _decode(0x20, SAMPLE_20_STAGE2_PAIRED)
    assert p['event'] == 'Batt High Temp Stage 2'
    assert (p['structured_data']['pack_temp_celsius'], p['structured_data']['module_number']) == (50, 0)


def test_0x1e_invalid_thermistor_kept_as_is():
    d = _decode(0x1e, SAMPLE_1E_INVALID_THERMISTOR)['structured_data']
    assert d['pack_temp_celsius'] == -100


def test_0x1f_low_temp_signed_and_matches_paired_0x20():
    e = _decode(0x1f, SAMPLE_1F)
    assert e['event'] == 'BMS Disable - Low Temp'
    d = e['structured_data']
    assert (d['pack_temp_celsius'], d['module_number'], d['status_flags']) == (-2, 0, 0x6308)
    p = _decode(0x20, SAMPLE_20_LOW_PAIRED)
    assert p['event'] == 'Batt Low Temp'
    assert p['structured_data'] == {'state': 'low', 'pack_temp_celsius': -2, 'module_number': 0}


def test_0x20_okay_and_stage1():
    e = _decode(0x20, SAMPLE_20_OKAY)
    assert e['event'] == 'Batt Temp Okay'
    assert e['structured_data'] == {'state': 'okay', 'pack_temp_celsius': 49, 'module_number': 0}
    e = _decode(0x20, SAMPLE_20_STAGE1)
    assert e['event'] == 'Batt High Temp Stage 1'
    assert e['structured_data'] == {'state': 'high', 'stage': 1, 'pack_temp_celsius': 61, 'module_number': 0}


def test_0x20_unnamed_state_falls_back():
    e = _decode(0x20, SAMPLE_20_UNNAMED_STATE)
    assert 'structured_data' not in e
    assert e['conditions'].startswith('Raw data:')


def test_0x26_motor_controller_temps_and_unread_tail():
    e = _decode(0x26, SAMPLE_26)
    assert e['event'] == 'High Mot/Ctrl'
    d = e['structured_data']
    assert d['motor_temp_celsius'] == 100
    assert d['controller_temp_celsius'] == 43
    assert d['raw_hex'] == bytes(SAMPLE_26).hex()


def test_0x35_five_byte_form():
    e = _decode(0x35, SAMPLE_35_SHORT)
    assert e['event'] == 'Exceeded Max Charge Amps'
    assert e['structured_data'] == {'module_number': 0, 'exceeded_current_amps': 25,
                                    'exceeded_seconds': 2, 'current_at_log_amps': -14}


def test_0x35_six_byte_form():
    e = _decode(0x35, SAMPLE_35_LONG)
    assert e['structured_data'] == {'module_number': 1, 'exceeded_current_amps': -28,
                                    'exceeded_seconds': 2, 'current_at_log_amps': -41}


def test_0x35_other_lengths_fall_back():
    e = _decode(0x35, _hex('00 19 02 f2'))
    assert 'structured_data' not in e
