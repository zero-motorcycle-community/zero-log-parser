"""
Tests for the FST/Gen3 BMS page-aware walker
(Gen2.is_paged_bms_format / Gen2.collect_paged_bms_entries /
Gen2._marker_corrupted_entry), per analysis/fst_page_aware_walker.md: the
00 f0 ff 00 page marker this format carries at every 128-byte page after
the first destroys 4 bytes of whatever real entry data was there. Two
recoverable shapes:

- type destroyed: a real entry (intact 0xb2 and length) whose type byte
  and part of its timestamp were overwritten (unescaped payload's first 4
  bytes equal the marker). Previously silently misread as type 0x00
  ("Board Status").
- header destroyed: the marker overwrote the entry's own 0xb2 (and usually
  its length and type too), so the normal resync walk finds no 0xb2 there
  and silently skips the whole span. Recovered when the marker sits within
  the first 3 bytes of an otherwise-unexplained gap of 8+ bytes and real
  (non-fill) data follows it.

Same conventions as the other files in this directory: plain test_*
functions, stdlib only, synthetic in-process buffers, no dataset files
checked in.
"""

import logging
import os
import struct
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from zero_log_parser import Gen2, LogFile, LogData

MARK = b'\x00\xf0\xff\x00'


def _entry(message_type, timestamp, payload):
    """One raw, framed entry: 0xb2, length byte, type, 4-byte timestamp,
    payload - the same shape used throughout this repo's other decoder
    tests. Payload bytes are chosen by callers to avoid 0xb2 and 0xfe (the
    header and escape bytes) unless a test specifically wants to include
    them."""
    body = bytes([message_type]) + struct.pack('<I', timestamp) + bytes(payload)
    return bytearray([0xb2, len(body) + 2]) + bytearray(body)


def _fill(n, start=1):
    """n filler bytes, 1..100, repeating - never 0xb2 or 0xfe."""
    return bytes(((start + i) % 100) + 1 for i in range(n))


def test_is_paged_bms_format_requires_marker_at_both_pages():
    buf = bytearray(384)
    assert not Gen2.is_paged_bms_format(bytes(buf))
    buf[128:132] = MARK
    assert not Gen2.is_paged_bms_format(bytes(buf))  # only one page marked
    buf[256:260] = MARK
    assert Gen2.is_paged_bms_format(bytes(buf))


def test_is_paged_bms_format_false_for_short_buffers():
    assert not Gen2.is_paged_bms_format(MARK * 60)  # under 3 pages


def _type_destroyed_entry(tail):
    """A framed entry whose type byte and first 3 timestamp bytes (the
    unescaped payload's own bytes 0-3) are the marker - the 4th timestamp
    byte and everything in tail survive intact."""
    body = MARK + bytes([0x00]) + bytes(tail)  # MARK covers type+ts[0:3]; ts[3]=0x00
    return bytearray([0xb2, len(body) + 2]) + bytearray(body)


def test_type_destroyed_entry_is_recovered_and_labelled():
    buf = _type_destroyed_entry(_fill(20))
    logger = logging.getLogger('test_fst_page_aware_walker')
    collected = Gen2.collect_paged_bms_entries(bytes(buf), logger)
    assert len(collected) == 1
    _ts, entry, _num = collected[0]
    assert entry['event'] == 'Corrupted Entry (type destroyed)'
    assert entry['message_type'] == 'CORRUPTED'
    assert entry['structured_data']['bytes_corrupted'] is True
    assert entry['structured_data']['corrupted_byte_count'] == 4
    assert bytes.fromhex(entry['structured_data']['raw_hex']) == b'\x00' + _fill(20)
    assert entry['time'] == 'Unknown'


def test_normal_entry_without_the_marker_is_unaffected():
    payload = _fill(10)
    buf = bytearray(_entry(0x01, 1_700_000_000, payload))
    logger = logging.getLogger('test_fst_page_aware_walker')
    collected = Gen2.collect_paged_bms_entries(bytes(buf), logger)
    assert len(collected) == 1
    _ts, entry, _num = collected[0]
    assert entry['event'] == 'BMS Reset'
    assert entry['message_type'] == '0x1'


def test_header_destroyed_gap_is_recovered_between_two_real_entries():
    # entry0, then a destroyed-header span (marker near the gap's start,
    # followed by real recoverable data), then entry1 with an intact 0xb2.
    entry0 = _entry(0x01, 1_700_000_000, _fill(5))
    recoverable = MARK + b'Sending first contactor close command\x00'
    entry1 = _entry(0x01, 1_700_000_100, _fill(5, start=50))
    buf = bytearray(entry0) + bytearray(recoverable) + bytearray(entry1)
    logger = logging.getLogger('test_fst_page_aware_walker')
    collected = Gen2.collect_paged_bms_entries(bytes(buf), logger)
    assert len(collected) == 3
    events = [e['event'] for _ts, e, _n in collected]
    assert events == ['BMS Reset', 'Corrupted Entry (header destroyed)', 'BMS Reset']
    gap_entry = collected[1][1]
    assert gap_entry['structured_data']['corrupted_byte_count'] == 4
    assert bytes.fromhex(gap_entry['structured_data']['raw_hex']) == recoverable[4:]


def test_header_destroyed_gap_requires_marker_near_the_start():
    # The marker sits well past the first 3 bytes of the gap - not the
    # "entry header destroyed" shape, left as an unexplained gap.
    entry0 = _entry(0x01, 1_700_000_000, _fill(5))
    gap = _fill(10, start=9) + MARK + b'text after\x00'
    entry1 = _entry(0x01, 1_700_000_100, _fill(5, start=50))
    buf = bytearray(entry0) + bytearray(gap) + bytearray(entry1)
    logger = logging.getLogger('test_fst_page_aware_walker')
    collected = Gen2.collect_paged_bms_entries(bytes(buf), logger)
    events = [e['event'] for _ts, e, _n in collected]
    assert 'Corrupted Entry (header destroyed)' not in events
    assert events == ['BMS Reset', 'BMS Reset']


def test_header_destroyed_gap_requires_real_data_after_the_marker():
    # Marker followed only by fill (0xff) - an unwritten page, not a
    # destroyed header with recoverable content.
    entry0 = _entry(0x01, 1_700_000_000, _fill(5))
    gap = MARK + b'\xff' * 10
    entry1 = _entry(0x01, 1_700_000_100, _fill(5, start=50))
    buf = bytearray(entry0) + bytearray(gap) + bytearray(entry1)
    logger = logging.getLogger('test_fst_page_aware_walker')
    collected = Gen2.collect_paged_bms_entries(bytes(buf), logger)
    events = [e['event'] for _ts, e, _n in collected]
    assert 'Corrupted Entry (header destroyed)' not in events


def test_header_destroyed_gap_requires_at_least_eight_bytes():
    entry0 = _entry(0x01, 1_700_000_000, _fill(5))
    gap = MARK + b'ab'  # 6 bytes total, under the 8-byte floor
    entry1 = _entry(0x01, 1_700_000_100, _fill(5, start=50))
    buf = bytearray(entry0) + bytearray(gap) + bytearray(entry1)
    logger = logging.getLogger('test_fst_page_aware_walker')
    collected = Gen2.collect_paged_bms_entries(bytes(buf), logger)
    events = [e['event'] for _ts, e, _n in collected]
    assert 'Corrupted Entry (header destroyed)' not in events


def test_zero_length_entry_does_not_crash_and_is_still_counted():
    # A real zero-length-byte entry (0xb2 followed by a 0x00 length byte),
    # matching Gen2.parse_entry's own tolerance for this case (degrades to
    # a mostly-empty "Board Status" rather than crashing or vanishing).
    buf = bytearray([0xb2, 0x00]) + bytearray(_entry(0x01, 1_700_000_000, _fill(5)))
    logger = logging.getLogger('test_fst_page_aware_walker')
    collected = Gen2.collect_paged_bms_entries(bytes(buf), logger)
    events = [e['event'] for _ts, e, _n in collected]
    assert events == ['Board Status', 'BMS Reset']


def test_entries_stay_in_byte_order_for_interpolation():
    # collected_entries must be built in byte order (not "every recovered
    # gap first, then every real entry"), since
    # Gen2.interpolate_missing_timestamps fills a missing timestamp from
    # list-adjacent neighbors, not from entry_num.
    entry0 = _entry(0x01, 1_700_000_000, _fill(5))
    recoverable = MARK + b'recovered text here\x00'
    entry1 = _entry(0x01, 1_700_000_100, _fill(5, start=50))
    buf = bytearray(entry0) + bytearray(recoverable) + bytearray(entry1)
    logger = logging.getLogger('test_fst_page_aware_walker')
    collected = Gen2.collect_paged_bms_entries(bytes(buf), logger)
    nums = [n for _ts, _e, n in collected]
    assert nums == sorted(nums)


def _write_paged_bms_file(path, body):
    """A minimal file this format's own detection recognises: 'BMS\\0'
    magic, byte 4 not one of the recognised classic codes (irrelevant here
    since is_paged_bms_format is checked before byte 4 is read at all, but
    kept realistic), no a2a2a2a2 header, and the marker at the start of
    the second and third 128-byte pages."""
    buf = bytearray(body)
    if len(buf) < 384:
        buf += b'\xff' * (384 - len(buf))
    buf[128:132] = MARK
    buf[256:260] = MARK
    with open(path, 'wb') as f:
        f.write(bytes(buf))


def test_end_to_end_through_logdata():
    entry0 = _entry(0x01, 1_700_000_000, _fill(5))
    entry1 = _type_destroyed_entry(_fill(10, start=30))
    body = bytes(entry0) + bytes(entry1)
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, 'test_BMS0.bin')
        _write_paged_bms_file(path, body)
        ld = LogData(LogFile(path), timezone_offset=0)
    events = [e.event for e in ld._processed_entries]
    assert 'BMS Reset' in events
    assert 'Corrupted Entry (type destroyed)' in events


def test_files_without_both_page_markers_use_the_normal_walker():
    # A file that looks like it might be this format (BMS magic, REV3
    # dispatch) but lacks the second page marker must not be routed into
    # collect_paged_bms_entries at all.
    entry0 = _entry(0x01, 1_700_000_000, _fill(5))
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, 'test_BMS0.bin')
        buf = bytearray(bytes(entry0) + b'\xff' * 400)
        with open(path, 'wb') as f:
            f.write(bytes(buf))
        ld = LogData(LogFile(path), timezone_offset=0)
    assert not any('Corrupted Entry' in e.event for e in ld._processed_entries)
