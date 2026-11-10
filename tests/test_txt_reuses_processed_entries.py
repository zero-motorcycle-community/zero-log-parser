"""
Tests that LogData.emit_zero_compatible_decoding uses the entries already
processed when the LogData was created instead of collecting and processing
every entry a second time, and that the text output is the same either way.

With a start or end time the entries are still collected again, because
_collect_and_process_entries keeps undated entries inside a time range and
_filter_processed_entries drops them.

Same conventions as the other files in this directory: plain test_*
functions, stdlib only, synthetic log files written to a temporary
directory, no dataset files checked in.
"""

import os
import struct
import sys
import tempfile
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from zero_log_parser import LogData, LogFile


def _write_minimal_rev0_file(path, entries_bytes):
    """A minimal REV0 legacy-format MBB file (same layout as the helper in
    test_undecoded_and_corrupted_rendering.py)."""
    header = bytearray(0x600)
    header[0:4] = b'MBB\x00'
    header[0x26:0x2a] = b'\xa1\xa1\xa1\xa1'
    header[0x2a:0x2a + 20] = b'Jan  1 2020 00:00:00'
    entries_start = 0x610
    entries_end = entries_start + len(entries_bytes)
    header += b'\xa2\xa2\xa2\xa2'
    header += struct.pack('<III', entries_end, entries_start, len(entries_bytes))
    header += b'\x00' * (entries_start - len(header))
    with open(path, 'wb') as f:
        f.write(bytes(header) + bytes(entries_bytes))


def _entry(message_type, timestamp, payload):
    body = bytes([message_type]) + struct.pack('<I', timestamp) + payload
    return bytearray([0xb2, len(body) + 2]) + bytearray(body)


def _entries(first_timestamp, count):
    out = bytearray()
    for i in range(count):
        payload = bytearray(46)
        struct.pack_into('<I', payload, 0, 1000 * (i + 1))
        payload[5] = 0x02
        payload[35:39] = b'RUN\x00'
        out += _entry(0x4b, first_timestamp + 60 * i, payload)
    return out


class _CountCollect:
    """Counts calls to LogData._collect_and_process_entries."""

    def __enter__(self):
        self.calls = 0
        self.original = LogData._collect_and_process_entries
        counter = self

        def counting(self, *args, **kwargs):
            counter.calls += 1
            return counter.original(self, *args, **kwargs)

        LogData._collect_and_process_entries = counting
        return self

    def __exit__(self, *exc):
        LogData._collect_and_process_entries = self.original
        return False


def _log_data(d, name, entries_bytes):
    path = os.path.join(d, name)
    _write_minimal_rev0_file(path, entries_bytes)
    return LogData(LogFile(path), timezone_offset=0)


def _txt(ld, d, **kwargs):
    out = os.path.join(d, 'out.txt')
    ld.emit_zero_compatible_decoding(out, **kwargs)
    with open(out, encoding='utf-8-sig') as f:
        return f.read()


def test_txt_without_time_range_collects_entries_once():
    with tempfile.TemporaryDirectory() as d, _CountCollect() as counter:
        ld = _log_data(d, 'a.bin', _entries(1_600_000_000, 5))
        assert counter.calls == 1  # when the LogData is created
        text = _txt(ld, d)
        assert counter.calls == 1
    assert 'Printing 5 of 5 log entries..' in text


def test_txt_output_is_the_same_as_collecting_again():
    with tempfile.TemporaryDirectory() as d:
        ld = _log_data(d, 'a.bin', _entries(1_600_000_000, 5))
        cached = _txt(ld, d)
        ld._processing_complete = False  # takes the collect-again path
        collected = _txt(ld, d)
    assert cached == collected


def test_txt_with_time_range_collects_entries_again():
    with tempfile.TemporaryDirectory() as d, _CountCollect() as counter:
        ld = _log_data(d, 'a.bin', _entries(1_600_000_000, 5))
        _txt(ld, d, start_time=datetime(2000, 1, 1, tzinfo=timezone.utc))
        assert counter.calls == 2


def test_txt_of_merged_logs_prints_the_merged_entries():
    # Merging replaces .entries with a placeholder and keeps the merged
    # entries in the processed-entry cache, so text output has to use the cache.
    with tempfile.TemporaryDirectory() as d:
        first = _log_data(d, 'a.bin', _entries(1_600_000_000, 3))
        second = _log_data(d, 'b.bin', _entries(1_700_000_000, 4))
        merged = first + second
        assert len(merged._get_processed_entries()) == 7
        text = _txt(merged, d)
    assert 'Printing 7 of 7 log entries..' in text
