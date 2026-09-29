"""
Tests for the None-value rendering fix: a handful of decoders legitimately
emit a None value in structured_data when their own source data does not
apply to a specific entry (debug_message()'s SOC path when the message
carries no current reading, battery_status()'s precharge_percent outside
the one event it applies to). Before this fix, the text writer printed the
literal string "None" (or "NoneA"/"NoneV"/etc. once a unit suffix was
appended), and the unnest TSV/CSV writer printed the literal text "None"
in a numeric-value column instead of an empty cell.

Same conventions as the other files in this directory: plain test_*
functions, stdlib only, synthetic in-process files, no real log files
checked in.
"""

import os
import struct
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from zero_log_parser import LogFile, LogData


def _write_minimal_rev0_file(path, entries_bytes):
    """Same minimal REV0 builder test_undecoded_and_corrupted_rendering.py
    uses: MBB magic, an a1a1a1a1 fencepost, an a2a2a2a2 event-log header
    pointing at the given already-framed entry bytes."""
    header = bytearray(0x600)
    header[0:4] = b'MBB\x00'
    header[0x26:0x2a] = b'\xa1\xa1\xa1\xa1'
    header[0x2a:0x2a + 20] = b'Jan  1 2020 00:00:00'
    entries_start = 0x610
    entries_end = entries_start + len(entries_bytes)
    header += b'\xa2\xa2\xa2\xa2'
    header += struct.pack('<III', entries_end, entries_start, len(entries_bytes))
    header += b'\x00' * (entries_start - len(header))
    buf = bytes(header) + bytes(entries_bytes)
    with open(path, 'wb') as f:
        f.write(buf)


def _entry(message_type, timestamp, payload):
    body = bytes([message_type]) + struct.pack('<I', timestamp) + bytes(payload)
    return bytearray([0xb2, len(body) + 2]) + bytearray(body)


def _battery_status_payload(event):
    """battery_status() (type 0x33): precharge_percent is None whenever
    event != 1 (event 1 is "Closing Contractor")."""
    payload = bytearray(0x14)
    payload[0x0] = event
    payload[0x1] = 3  # module_num
    struct.pack_into('<I', payload, 0x2, 350_000)   # mod_volt (mV)
    struct.pack_into('<I', payload, 0x6, 400_000)   # sys_max
    struct.pack_into('<I', payload, 0xa, 300_000)   # sys_min
    struct.pack_into('<I', payload, 0xe, 340_000)   # capacitor_volt
    struct.pack_into('<h', payload, 0x12, 50)       # battery_current (int16)
    payload += b'SN123\x00'
    return payload


def _debug_soc_no_current_payload():
    """debug_message() (type 0xfd): exactly 11 comma-separated SOC values
    (indices 0-10) means values[11] does not exist, so current_ma and
    current_amps are both None."""
    message = 'SOC:1,2,3,4,5,6,7,8,9,10,11'
    x = message.encode('ascii') + b'\x00'
    return x


def _load(path):
    return LogData(LogFile(path), timezone_offset=0)


def _run_txt(entries_bytes):
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, 'test.bin')
        _write_minimal_rev0_file(path, entries_bytes)
        ld = _load(path)
        out = os.path.join(d, 'test.txt')
        ld.emit_zero_compatible_decoding(out)
        return open(out, encoding='utf-8-sig').read()


def _run_unnest_tsv(entries_bytes):
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, 'test.bin')
        _write_minimal_rev0_file(path, entries_bytes)
        ld = _load(path)
        out = os.path.join(d, 'test.txt')
        ld.emit_tabular_decoding(out, out_format='tsv', unnest=True)
        tsv_path = out.replace('.txt', '.tsv')
        return open(tsv_path, encoding='utf-8').read()


def test_none_field_is_omitted_from_text_output_not_printed_as_none():
    text = _run_txt(_entry(0x33, 1_700_000_000, _battery_status_payload(event=0x00)))
    assert 'None' not in text
    assert 'Precharge Percent' not in text
    # The rest of the entry still renders normally.
    assert 'Module Number: 3' in text
    assert 'Event Type: Opening Contractor' in text


def test_none_field_with_unit_suffix_key_does_not_leak_as_nonepercent():
    # precharge_percent's own key name would trigger the '%' suffix branch
    # in format_structured_value if it were ever reached for a None value.
    text = _run_txt(_entry(0x33, 1_700_000_000, _battery_status_payload(event=0x02)))  # Registered
    assert 'None%' not in text
    assert 'None' not in text


def test_precharge_percent_still_renders_when_applicable():
    text = _run_txt(_entry(0x33, 1_700_000_000, _battery_status_payload(event=0x01)))  # Closing Contractor
    assert 'Precharge Percent:' in text
    assert 'None' not in text


def test_debug_soc_current_fields_omitted_from_text_when_absent():
    text = _run_txt(_entry(0xfd, 1_700_000_000, _debug_soc_no_current_payload()))
    assert 'None' not in text
    assert 'Current Ma' not in text
    assert 'Current Amps' not in text
    assert 'Soc Raw 1: 1' in text


def test_none_field_renders_as_empty_cell_in_unnest_tsv_not_the_word_none():
    tsv = _run_unnest_tsv(_entry(0x33, 1_700_000_000, _battery_status_payload(event=0x00)))
    lines = [l for l in tsv.splitlines() if l]
    precharge_rows = [l for l in lines if '\tprecharge_percent\t' in l or l.endswith('\tprecharge_percent')]
    assert precharge_rows, f'no precharge_percent row found in:\n{tsv}'
    for row in precharge_rows:
        fields = row.split('\t')
        value_field = fields[5] if len(fields) > 5 else ''
        assert value_field == '', f'expected an empty cell, got {value_field!r} in row {row!r}'
        assert 'None' not in row


def test_debug_soc_current_ma_renders_as_empty_cell_in_unnest_tsv():
    tsv = _run_unnest_tsv(_entry(0xfd, 1_700_000_000, _debug_soc_no_current_payload()))
    lines = [l for l in tsv.splitlines() if l]
    current_rows = [l for l in lines if '\tcurrent_ma\t' in l or l.endswith('\tcurrent_ma')]
    assert current_rows, f'no current_ma row found in:\n{tsv}'
    for row in current_rows:
        fields = row.split('\t')
        value_field = fields[5] if len(fields) > 5 else ''
        assert value_field == ''
        assert 'None' not in row


def _bms_discharge_level_payload(mode_byte):
    """bms_discharge_level() (type 0x03): mode is None whenever mode_byte
    is not one of 0x01/0x02/0x03 (a dict.get() with no default, found only
    by scanning parser output for real logs, not by reading the
    source)."""
    payload = bytearray(0x16)
    struct.pack_into('<H', payload, 0x00, 3900)   # voltage_low_cell (mV)
    struct.pack_into('<H', payload, 0x02, 4000)   # voltage_high_cell (mV)
    payload[0x04] = 25   # pack_temp_celsius
    payload[0x05] = 26   # bms_temp_celsius
    struct.pack_into('<I', payload, 0x06, 5_000_000)   # amp_hours * 1e6
    payload[0x0a] = 80   # state_of_charge_percent
    struct.pack_into('<I', payload, 0x0b, 108_000)  # pack_voltage (mV)
    payload[0x0f] = mode_byte
    struct.pack_into('<i', payload, 0x10, 2_000_000)  # current_amps * 1e6
    struct.pack_into('<H', payload, 0x14, 3950)   # voltage_unloaded_cell (mV)
    return payload


def test_bms_discharge_level_unknown_mode_omitted_from_text_not_none():
    text = _run_txt(_entry(0x03, 1_700_000_000, _bms_discharge_level_payload(mode_byte=0x00)))
    assert 'None' not in text
    assert 'Mode:' not in text  # not bare 'Mode': the synthetic header's own unrelated bytes can coincidentally contain that substring
    assert 'State Of Charge Percent: 80' in text


def test_bms_discharge_level_known_mode_still_renders():
    text = _run_txt(_entry(0x03, 1_700_000_000, _bms_discharge_level_payload(mode_byte=0x01)))
    assert 'Mode: Bike On' in text
    assert 'None' not in text


def test_non_none_numeric_zero_still_renders_in_unnest_tsv():
    # A real 0 value must not be confused with the None-is-empty-cell case.
    payload = _battery_status_payload(event=0x01)
    struct.pack_into('<h', payload, 0x12, 0)  # battery_current_amps = 0
    tsv = _run_unnest_tsv(_entry(0x33, 1_700_000_000, payload))
    lines = [l for l in tsv.splitlines() if l]
    current_rows = [l for l in lines if '\tbattery_current_amps\t' in l]
    assert current_rows
    assert any(row.split('\t')[5] == '0' for row in current_rows)
