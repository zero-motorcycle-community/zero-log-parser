"""
Regression tests for entry type 0x0F ("BMS Long Term Storage Stats") in
Gen2.bms_storage_stats(), previously undecoded (fell through to
unhandled_entry_format() as "Unknown Type 15").

Same conventions as tests/test_type_0x32_decode.py: the decoder is tested
directly on the raw message bytes Gen2.parse_entry() would hand it (the
bytes after the 0xb2 header, length byte, message-type byte and 4-byte
timestamp), so no LogFile/LogData scaffolding is needed.

Every byte sequence below is a real sample from the file set, pulled with
the real entry walker (see analysis/type_0xF_decode.md for the scan and the
full-population validation). Each 10-byte record is paired with the type
0x3 discharge-level record that immediately follows it in the same file,
since the layout was confirmed by those two records agreeing on low cell,
high cell and BMS temperature.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from zero_log_parser import Gen2


def _hex(s):
    return bytearray(bytes.fromhex(s.replace(' ', '')))


# 538SD5Z27ECB04404_BMS0_2021-02-27.bin, 2020-10-04 05:54:00. Preceding
# LTSM string reads "Num bal res active: 10".
SAMPLE_BAL_ACTIVE = _hex('02 01 0a 0e 10 0f 10 01 00 2b')
PAIRED_0X3_BAL_ACTIVE = _hex('0e100f10192b0000000064b2c0010003000000000e100000')

# Same file, 2020-10-04 17:54:00. "Num bal res active: 0".
SAMPLE_BAL_IDLE = _hex('01 1b 00 07 10 09 10 02 00 2b')
PAIRED_0X3_BAL_IDLE = _hex('07100910192b00000000648cc00100030000000007100000')

# 538XXDZ48MCC16670_BmsD0_2022-01-15.bin: a deeply discharged cell, a
# balance above 255 mV, so byte 8 (the balance's high byte) is non-zero.
SAMPLE_WIDE_BALANCE = _hex('02 1c 00 e9 03 75 0a 8c 06 16')
PAIRED_0X3_WIDE_BALANCE = _hex('e903750a1416c0da6b0300e37400000300000000e9030000')

# 538SMMZ41MCA16118_BMS0_2025-12-15.bin, 2025-07-18 05:52:50: a real 0x0F
# record cut short (a type 0x0 reset follows one second later). Not 10
# bytes, so it must fall back to the raw-hex report.
SAMPLE_TRUNCATED = _hex('01 02 00')


def test_dispatched():
    assert Gen2._entry_parsers()[0x0f] == Gen2.bms_storage_stats
    assert Gen2.get_message_type_description(0x0f) == 'BMS Long Term Storage Stats'


def test_fields_balance_active():
    e = Gen2.bms_storage_stats(SAMPLE_BAL_ACTIVE)
    d = e['structured_data']
    assert e['event'] == 'BMS Long Term Storage Stats'
    assert d['balance_resistors_active'] == 10
    assert d['voltage_low_cell_volts'] == 4.110
    assert d['voltage_high_cell_volts'] == 4.111
    assert d['voltage_balance_mv'] == 1
    assert d['bms_temp_celsius'] == 43
    assert d['raw_hex'] == bytes(SAMPLE_BAL_ACTIVE).hex()


def test_fields_balance_idle():
    d = Gen2.bms_storage_stats(SAMPLE_BAL_IDLE)['structured_data']
    assert d['balance_resistors_active'] == 0
    assert d['voltage_balance_mv'] == 2


def test_wide_balance_uses_both_bytes():
    d = Gen2.bms_storage_stats(SAMPLE_WIDE_BALANCE)['structured_data']
    assert d['voltage_low_cell_volts'] == 1.001
    assert d['voltage_high_cell_volts'] == 2.677
    assert d['voltage_balance_mv'] == 2677 - 1001


def test_agrees_with_paired_discharge_level():
    for sample, paired in [(SAMPLE_BAL_ACTIVE, PAIRED_0X3_BAL_ACTIVE),
                           (SAMPLE_BAL_IDLE, PAIRED_0X3_BAL_IDLE),
                           (SAMPLE_WIDE_BALANCE, PAIRED_0X3_WIDE_BALANCE)]:
        d = Gen2.bms_storage_stats(sample)['structured_data']
        level = Gen2.bms_discharge_level(paired)['structured_data']
        assert d['voltage_low_cell_volts'] == level['voltage_low_cell_volts']
        assert d['voltage_high_cell_volts'] == level['voltage_high_cell_volts']
        assert d['voltage_balance_mv'] == level['voltage_balance_mv']
        assert d['bms_temp_celsius'] == level['bms_temp_celsius']


def test_bytes_0_and_1_not_labelled():
    d = Gen2.bms_storage_stats(SAMPLE_BAL_ACTIVE)['structured_data']
    assert set(d) == {'balance_resistors_active', 'voltage_low_cell_volts',
                      'voltage_high_cell_volts', 'voltage_balance_mv',
                      'bms_temp_celsius', 'raw_hex'}


def test_wrong_length_falls_back():
    e = Gen2.bms_storage_stats(SAMPLE_TRUNCATED)
    assert 'structured_data' not in e
    assert e == Gen2.unhandled_entry_format(0x0f, SAMPLE_TRUNCATED)


def test_raw_hex_renders_as_undecoded_tag():
    d = Gen2.bms_storage_stats(SAMPLE_BAL_ACTIVE)['structured_data']
    assert (Gen2.undecoded_hex_display(bytes.fromhex(d['raw_hex']))
            == '{undecoded hex: 02 01 0a 0e 10 0f 10 01 00 2b}')
