"""
Tests for the 0xfe 0x00 pair in an entry payload.

BinaryTools.unescape_block decodes an escape pair as 0xfe xor (next - 1), so
fe 01 is 0xfe and fe 4d is 0xb2. A fe 00 pair has no decoded value and raises
UnescapeError (a ValueError). The REV0/REV1/REV3 entry loop in LogData used to
stop at that error and drop every later entry in the file. For MBB, REV3 and
Unknown Type logs it now skips the one damaged entry and continues with the next
one. BMS logs still stop at the damaged entry.

Plain test_* functions, stdlib only, synthetic in-process buffers.
"""

import dataclasses
import os
import struct
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from zero_log_parser import BinaryTools, LogData, LogFile, UnescapeError


def _escape(body):
    out = bytearray()
    for b in body:
        if b == 0xfe:
            out += b'\xfe\x01'
        elif b == 0xb2:
            out += b'\xfe\x4d'
        else:
            out.append(b)
    return out


def _entry(message_type, timestamp, payload):
    body = _escape(bytes([message_type]) + struct.pack('<I', timestamp) + bytes(payload))
    return bytearray([0xb2, len(body) + 2]) + body


def _damaged_entry(message_type, timestamp, payload):
    """A framed entry with a fe 00 pair inserted after its type byte."""
    body = bytes([message_type]) + b'\xfe\x00' + _escape(struct.pack('<I', timestamp) + bytes(payload))
    return bytearray([0xb2, len(body) + 2]) + bytearray(body)


def _write_minimal_rev0_file(path, entries_bytes, magic=b'MBB\x00'):
    header = bytearray(0x600)
    header[0:4] = magic
    header[0x26:0x2a] = b'\xa1\xa1\xa1\xa1'
    header[0x2a:0x2a + 20] = b'Jan  1 2020 00:00:00'
    entries_start = 0x610
    entries_end = entries_start + len(entries_bytes)
    header += b'\xa2\xa2\xa2\xa2'
    header += struct.pack('<III', entries_end, entries_start, len(entries_bytes))
    header += b'\x00' * (entries_start - len(header))
    with open(path, 'wb') as f:
        f.write(bytes(header) + bytes(entries_bytes))


def _decode(entries_bytes, magic=b'MBB\x00'):
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, 'test.bin')
        _write_minimal_rev0_file(path, entries_bytes, magic)
        lf = LogFile(path)
        assert lf.log_type == (LogFile.log_type_bms if magic.startswith(b'BMS') else LogFile.log_type_mbb)
        ld = LogData(lf, timezone_offset=0)
        return ld._processed_entries


def _content(entries):
    """Every field of each entry except its entry number."""
    out = []
    for e in entries:
        d = dataclasses.asdict(e)
        d.pop('entry_number')
        out.append(d)
    return out


# Entry payloads. The second carries bytes that need escaping (0xb2 and 0xfe),
# so the entries after the damaged one also go through the unescaper.
GOOD = [
    (0x01, 1_700_000_000, bytes(5)),
    (0x01, 1_700_000_100, bytes([0xb2, 0xfe, 0x01, 0x02, 0x03])),
    (0x01, 1_700_000_200, bytes(5)),
]


def test_unescape_valid_pairs_unchanged():
    assert bytes(BinaryTools.unescape_block(bytearray(b'\x01\xfe\x01\x02'))) == b'\x01\xfe\x02'
    assert bytes(BinaryTools.unescape_block(bytearray(b'\x01\xfe\x4d\x02'))) == b'\x01\xb2\x02'
    # A trailing 0xfe with nothing after it is left as it is.
    assert bytes(BinaryTools.unescape_block(bytearray(b'\x01\x02\xfe'))) == b'\x01\x02\xfe'


def test_unescape_fe00_raises_unescape_error():
    try:
        BinaryTools.unescape_block(bytearray(b'\x01\xfe\x00\x02'))
    except UnescapeError:
        pass
    else:
        raise AssertionError('fe 00 did not raise')
    # Still a ValueError, so callers that catch ValueError or Exception are unaffected.
    assert issubclass(UnescapeError, ValueError)


def test_walk_continues_past_damaged_entry():
    buf = bytearray()
    buf += _entry(*GOOD[0])
    buf += _damaged_entry(0x01, 1_700_000_050, bytes(5))
    buf += _entry(*GOOD[1])
    buf += _entry(*GOOD[2])

    processed = _decode(buf)
    stamps = sorted(int(e.original_timestamp) for e in processed if e.original_timestamp is not None)
    # The two entries after the damaged one are present; the damaged one is not.
    for _, ts, _ in GOOD:
        assert ts in stamps, (ts, stamps)
    assert 1_700_000_050 not in stamps


def test_entries_after_damaged_entry_decode_as_without_it():
    clean = bytearray()
    for g in GOOD:
        clean += _entry(*g)
    damaged = bytearray()
    damaged += _entry(*GOOD[0])
    damaged += _damaged_entry(0x01, 1_700_000_050, bytes(5))
    damaged += _entry(*GOOD[1])
    damaged += _entry(*GOOD[2])

    assert _content(_decode(damaged)) == _content(_decode(clean))


def test_several_damaged_entries_each_skipped():
    buf = bytearray()
    buf += _entry(*GOOD[0])
    buf += _damaged_entry(0x01, 1_700_000_050, bytes(5))
    buf += _damaged_entry(0x01, 1_700_000_060, bytes(5))
    buf += _entry(*GOOD[1])
    buf += _damaged_entry(0x01, 1_700_000_150, bytes(5))
    buf += _entry(*GOOD[2])

    clean = bytearray()
    for g in GOOD:
        clean += _entry(*g)
    assert _content(_decode(buf)) == _content(_decode(clean))


def test_damaged_last_entry_ends_walk_cleanly():
    buf = bytearray()
    buf += _entry(*GOOD[0])
    buf += _damaged_entry(0x01, 1_700_000_050, bytes(5))

    clean = _entry(*GOOD[0])
    assert _content(_decode(buf)) == _content(_decode(clean))


def test_bms_log_still_stops_at_damaged_entry():
    buf = bytearray()
    buf += _entry(*GOOD[0])
    buf += _damaged_entry(0x01, 1_700_000_050, bytes(5))
    buf += _entry(*GOOD[1])
    buf += _entry(*GOOD[2])

    processed = _decode(buf, magic=b'BMS\x00')
    stamps = [int(e.original_timestamp) for e in processed if e.original_timestamp is not None]
    assert stamps == [GOOD[0][1]], stamps
    assert _content(processed) == _content(_decode(_entry(*GOOD[0]), magic=b'BMS\x00'))
