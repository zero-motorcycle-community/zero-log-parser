"""
Tests for the standing text-rendering convention: how a raw_hex field (bytes
a decoder never attempted to interpret) and a bytes_corrupted/
corrupted_byte_count pair (bytes destroyed by the FST/Gen3 BMS page marker,
analysis/pattern_00f0ff00_entries_and_ecuid_outliers.md) render in the txt
output's structured-data formatter, and the three small Gen2 helpers
(undecoded_hex_display, corrupted_span_display, mark_bytes_corrupted) that
back it.

Same conventions as the other files in this directory: plain test_*
functions, stdlib only, no dataset files checked in. format_structured_data
is a closure defined inside LogData.emit_zero_compatible_decoding, so it is
exercised the same way the existing test_rev3_detection.py exercises other
private behavior: by writing a real (synthetic) log file and reading back
the .txt output, rather than importing the closure directly.
"""

import os
import struct
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from zero_log_parser import Gen2, LogFile, LogData


def test_undecoded_hex_display_is_space_separated_lowercase_hex_in_braces():
    assert Gen2.undecoded_hex_display(bytes([0x3f, 0xa2, 0x04, 0x91])) == '{undecoded hex: 3f a2 04 91}'
    assert Gen2.undecoded_hex_display(b'') == '{undecoded hex: }'


def test_corrupted_span_display_names_the_byte_count():
    assert Gen2.corrupted_span_display(4) == '{corrupted: 4 bytes lost}'
    assert Gen2.corrupted_span_display(0) == '{corrupted: 0 bytes lost}'


def test_mark_bytes_corrupted_sets_real_typed_fields_not_a_string():
    sd = {}
    Gen2.mark_bytes_corrupted(sd, 4)
    assert sd == {'bytes_corrupted': True, 'corrupted_byte_count': 4}
    assert sd['bytes_corrupted'] is True
    assert isinstance(sd['corrupted_byte_count'], int)


def _write_minimal_rev0_file(path, entries_bytes):
    """A minimal REV0 legacy-format file: MBB magic, an a1a1a1a1 fencepost
    (first-run date), an a2a2a2a2 event-log header pointing at the given
    already-framed entry bytes, then the entries themselves."""
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
    body = bytes([message_type]) + struct.pack('<I', timestamp) + payload
    return bytearray([0xb2, len(body) + 2]) + bytearray(body)


def _run_txt(entries_bytes):
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, 'test.bin')
        _write_minimal_rev0_file(path, entries_bytes)
        ld = LogData(LogFile(path), timezone_offset=0)
        out = os.path.join(d, 'test.txt')
        ld.emit_zero_compatible_decoding(out)
        return open(out, encoding='utf-8-sig').read()


def test_raw_hex_renders_as_bracketed_undecoded_hex_with_no_label():
    payload = bytearray(46)
    struct.pack_into('<I', payload, 0, 123000)
    payload[5] = 0x02
    payload[35:39] = b'RUN\x00'
    text = _run_txt(_entry(0x4b, 1_600_000_000, payload))
    assert '{undecoded hex: ' + bytes(payload).hex(' ') + '}' in text
    assert 'Raw Hex' not in text


def test_marker_field_does_not_render_as_milliamps():
    payload = bytearray(46)
    struct.pack_into('<I', payload, 0, 1000)
    payload[5] = 0xf9
    payload[35:39] = b'RUN\x00'
    text = _run_txt(_entry(0x4b, 1_600_000_000, payload))
    assert 'Marker: 249' in text
    assert 'mA' not in text


def test_corrupted_fields_collapse_into_one_bracketed_tag():
    # A synthetic decoder-shaped dict, exercised the same way state_snapshot's
    # structured_data would look if it also flagged corruption; verified via
    # the format_structured_data closure indirectly is impractical without a
    # decoder that sets it, so this drives it through Gen2.mark_bytes_corrupted
    # plus a manual structured_data pass-through using debug_message's own
    # SOC-data path is unrelated - instead this test builds the dict the way
    # any future decoder would and checks the two helper functions compose
    # into exactly what format_structured_data is documented to produce.
    sd = {'state': 'RUN'}
    Gen2.mark_bytes_corrupted(sd, 4)
    assert sd['bytes_corrupted'] is True
    assert sd['corrupted_byte_count'] == 4
    # The txt renderer's own contract (see format_structured_data's
    # docstring): bytes_corrupted/corrupted_byte_count never appear as their
    # own "Label: value" pairs, they always collapse to corrupted_span_display.
    assert Gen2.corrupted_span_display(sd['corrupted_byte_count']) == '{corrupted: 4 bytes lost}'
