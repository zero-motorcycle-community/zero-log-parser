"""
Tests for the REV1 (header byte 0x4 == 0xde) classic BMS 'BMS serial
number' recovery in LogData.get_version_and_header(), per
analysis/bms_serial_rev1.md: the board-serial string sits at a fixed offset
0x310, one 16-byte binary block later than REV0's own 0x300, not at 0x300
itself and not left unset (the previous code had a # TODO and only ever set
'Pack serial number', at 0x331, unchanged here).

Same conventions as tests/test_rev3_detection.py, which this file borrows
its minimal-buffer/_parse() helpers' shape from directly (see that file's
own docstring for why no dataset fixtures are used): plain test_* functions,
stdlib only, synthetic in-process buffers, no dataset files checked in.
"""

import os
import tempfile

import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from zero_log_parser import LogFile, LogData, REV1


def build_rev1_bms_buffer(board_serial=b'SJ2318ZER0300', pack_serial=b'19tb0121',
                           total_len=0x40000):
    """A minimal REV1-shaped classic BMS buffer: 'BMS\\0' magic at offset 0,
    header byte 0x4 == 0xde (REV1), the board-serial string at 0x310 and the
    pack-serial string at the already-decoded offset 0x331, both
    NUL-terminated - the exact layout analysis/bms_serial_rev1.md confirmed
    against 802 of 808 real classic REV1 files."""
    buf = bytearray(b'\xff' * total_len)
    buf[0:4] = b'BMS\x00'
    buf[0x4] = 0xde
    buf[0x12:0x12 + 20] = b'Jan  1 2020 00:00:00'
    buf[0x310:0x310 + len(board_serial) + 1] = board_serial + b'\x00'
    buf[0x331:0x331 + len(pack_serial) + 1] = pack_serial + b'\x00'
    return bytes(buf)


def _write_temp(data, suffix='_BMS0.bin'):
    f = tempfile.NamedTemporaryFile(prefix='bmsrev1test', suffix=suffix, delete=False)
    f.write(data)
    f.close()
    return f.name


def _parse(path):
    lf = LogFile(path)
    ld = object.__new__(LogData)
    ld.log_file = lf
    return ld.get_version_and_header(lf)


def test_board_serial_recovered_at_0x310_sj_shape():
    path = _write_temp(build_rev1_bms_buffer(b'SJ2318ZER0300', b'19tb0121'))
    try:
        log_version, header = _parse(path)
    finally:
        os.unlink(path)
    assert log_version == REV1
    assert header['BMS serial number'] == 'SJ2318ZER0300'
    assert header['Pack serial number'] == '19tb0121'


def test_board_serial_recovered_rkt_shape():
    # The other known board-serial shape (already established for REV0 and
    # the ring-buffer format), confirmed present in this population too.
    path = _write_temp(build_rev1_bms_buffer(b'RKT-20160273', b'20md0135'))
    try:
        _log_version, header = _parse(path)
    finally:
        os.unlink(path)
    assert header['BMS serial number'] == 'RKT-20160273'


def test_implausible_bytes_degrade_to_unknown_not_emitted_as_a_fake_serial():
    # The real, unexplained failure mode found in 6 of 808 files: dense,
    # non-text binary bytes sitting at 0x310 instead of a serial string.
    buf = bytearray(build_rev1_bms_buffer())
    buf[0x310:0x310 + 21] = bytes([0xea, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x6d,
                                    0xc0, 0x27, 0x09, 0x00, 0x00, 0x00, 0x00, 0x00,
                                    0x25, 0x00, 0xcc, 0xbf, 0x19])
    path = _write_temp(bytes(buf))
    try:
        _log_version, header = _parse(path)
    finally:
        os.unlink(path)
    assert header['BMS serial number'] == 'Unknown'


def test_non_serial_shaped_text_degrades_to_unknown():
    # A real failure mode found at full population scale: one file's own
    # error-log section starts early enough to overwrite this field with a
    # printable but non-serial-shaped log-text fragment ("ed: 9570 mV").
    path = _write_temp(build_rev1_bms_buffer(b'ed: 9570 mV', b'19tb0121'))
    try:
        _log_version, header = _parse(path)
    finally:
        os.unlink(path)
    assert header['BMS serial number'] == 'Unknown'


def test_too_short_a_string_degrades_to_unknown():
    path = _write_temp(build_rev1_bms_buffer(b'AB', b'19tb0121'))
    try:
        _log_version, header = _parse(path)
    finally:
        os.unlink(path)
    assert header['BMS serial number'] == 'Unknown'


def test_pack_serial_field_is_unchanged():
    # This task only touches the board-serial field; pack serial at 0x331
    # keeps its existing, already-correct behavior.
    path = _write_temp(build_rev1_bms_buffer(b'RKT-20160273', b'20hm1481'))
    try:
        _log_version, header = _parse(path)
    finally:
        os.unlink(path)
    assert header['Pack serial number'] == '20hm1481'


def test_rev0_and_rev2_bms_files_are_unaffected():
    # REV0 (byte4 == 0xb6) still reads its own, unrelated offset (0x300);
    # this branch's change is scoped to REV1 (0xde) only.
    buf = bytearray(b'\xff' * 0x40000)
    buf[0:4] = b'BMS\x00'
    buf[0x4] = 0xb6
    buf[0x12:0x12 + 20] = b'Jan  1 2020 00:00:00'
    buf[0x300:0x300 + 14] = b'SJ5016ZE12345\x00'
    buf[0x320:0x320 + 9] = b'17tb0905\x00'
    path = _write_temp(bytes(buf))
    try:
        _log_version, header = _parse(path)
    finally:
        os.unlink(path)
    assert header['BMS serial number'] == 'SJ5016ZE12345'
    assert header['Pack serial number'] == '17tb0905'
