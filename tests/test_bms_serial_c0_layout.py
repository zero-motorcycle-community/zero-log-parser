"""
Tests for the classic BMS layout selector in LogData.get_version_and_header():
c0 c0 c0 c0 at 0x300 selects the REV1 serial offsets (board 0x310, pack
0x331) whatever header byte 0x4 holds, and the REV0 branch shows 'Unknown'
instead of a value that is not serial-shaped.

Synthetic buffers only; every serial below is invented.
"""

import logging
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from zero_log_parser import LogFile, LogData, REV0, REV1


def _c0_buffer(byte4, board=b'SJ0000ZER0001', pack=b'00tb0001', total_len=0x40000):
    buf = bytearray(b'\xff' * total_len)
    buf[0:4] = b'BMS\x00'
    buf[0x4] = byte4
    buf[0x12:0x12 + 20] = b'Jan  1 2000 00:00:00'
    buf[0x300:0x310] = b'\xc0' * 16            # the layout tag block
    buf[0x310:0x310 + len(board) + 1] = board + b'\x00'
    buf[0x320:0x330] = b'\x00' * 16
    buf[0x330] = 0xeb                            # constant byte before the pack serial
    buf[0x331:0x331 + len(pack) + 1] = pack + b'\x00'
    return bytes(buf)


def _rev0_buffer(board=b'2000_bms_000000_00001', pack=b'00qd0003', total_len=0x40000):
    buf = bytearray(b'\xff' * total_len)
    buf[0:4] = b'BMS\x00'
    buf[0x4] = 0xb6
    buf[0x12:0x12 + 20] = b'Jan  1 2000 00:00:00'
    buf[0x300:0x300 + 21] = board[:21].ljust(21, b'\x00')
    buf[0x320:0x320 + 8] = pack[:8].ljust(8, b'\x00')
    return bytes(buf)


def _parse(data):
    f = tempfile.NamedTemporaryFile(prefix='bmsc0test', suffix='_BMS0.bin', delete=False)
    f.write(data)
    f.close()
    try:
        lf = LogFile(f.name)
        ld = object.__new__(LogData)
        ld.log_file = lf
        return ld.get_version_and_header(lf)
    finally:
        os.unlink(f.name)


def test_c0_header_with_byte4_d4_reads_rev1_offsets():
    log_version, header = _parse(_c0_buffer(0xd4))
    assert log_version == REV1
    assert header['BMS serial number'] == 'SJ0000ZER0001'
    assert header['Pack serial number'] == '00tb0001'


def test_c0_header_with_byte4_ca_reads_rev1_offsets():
    log_version, header = _parse(_c0_buffer(0xca, board=b'SJ0000ZER0002', pack=b'00sg0002'))
    assert log_version == REV1
    assert header['BMS serial number'] == 'SJ0000ZER0002'
    assert header['Pack serial number'] == '00sg0002'


def test_c0_header_no_longer_logs_unknown_log_format(caplog):
    with caplog.at_level(logging.WARNING):
        _parse(_c0_buffer(0xd4))
    assert 'Unknown Log Format' not in caplog.text


def test_rev0_header_keeps_board_serial_with_underscore():
    log_version, header = _parse(_rev0_buffer())
    assert log_version == REV0
    assert header['BMS serial number'] == '2000_bms_000000_00001'
    assert header['Pack serial number'] == '00qd0003'


def test_rev0_header_with_garbage_at_0x300_shows_unknown():
    # Log text overwriting the board field, and binary bytes over the pack field
    data = _rev0_buffer(board=b'ror Log : 83574 mV', pack=b'\x01\x9a\x07\x00\x00\x00\x00\x00')
    log_version, header = _parse(data)
    assert log_version == REV0
    assert header['BMS serial number'] == 'Unknown'
    assert header['Pack serial number'] == 'Unknown'


def test_rev0_blank_fields_show_unknown():
    log_version, header = _parse(_rev0_buffer(board=b'', pack=b''))
    assert header['BMS serial number'] == 'Unknown'
    assert header['Pack serial number'] == 'Unknown'


def test_non_c0_byte4_de_still_rev1():
    # The 7 real non-c0 0xde files keep the REV1 branch (and 'Unknown' for
    # a board field that is not serial-shaped).
    buf = bytearray(_c0_buffer(0xde))
    buf[0x300:0x310] = b'\x00' * 16
    log_version, _header = _parse(bytes(buf))
    assert log_version == REV1
