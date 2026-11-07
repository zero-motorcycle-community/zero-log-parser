"""
Tests for FST/Gen3 log type detection from the header entry's device name
(Gen2.fst_log_type), the FST/Gen3 BMS header fields
(Gen2.fst_bms_header_fields) and the VIN rule (LogData.settle_vin):
the header's VIN, else a file name VIN that passes the check digit, else
Unknown; a file name VIN is labeled in TXT and JSON.

Plain test_* functions, stdlib only, synthetic bytes and invented values.
"""

import json
import logging
import os
import struct
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from zero_log_parser import Gen2, LogFile, LogData, REV3, is_valid_vin_checksum, parse_log

logging.disable(logging.CRITICAL)

T0 = 1_700_000_000
VIN_A = '538TESTA4NAB00001'   # invented, valid check digit
VIN_B = '538TESTB5NAB00002'   # invented, valid check digit
VIN_BAD = '538TESTB7NAB00002'  # invented, check digit wrong


def _escape(body):
    """Byte-stuff an entry body the way the parser's unescape_block reverses."""
    out = bytearray()
    for b in body:
        if b in (0xb2, 0xfe):
            out += bytes([0xfe, (0xfe ^ b) + 1])
        else:
            out.append(b)
    return bytes(out)


def _frame(unescaped):
    """unescaped is the whole entry from 0xb2 on; returns it framed with the
    length byte set from the escaped body."""
    body = _escape(unescaped[2:])
    return bytes([0xb2, len(body) + 2]) + body


def _text(s, size):
    raw = s.encode('ascii')
    return raw + b'\x00' * (size - len(raw))


def _mbb_header(vin=VIN_A, usec=250000):
    h = bytearray([0xb2, 0, 0xfb]) + struct.pack('<II', T0, usec) + bytes([3, 0])
    h += _text('MBB', 10)                       # 0x0d device name
    h += struct.pack('<H', 20230)               # 0x17 mfg_version
    h += _text('TESTMODEL', 16)                 # 0x19 model
    h += _text(vin, 19)                         # 0x29 VIN
    h += _text('TESTSERIAL01', 32)              # 0x3c serial
    h += _text('40-00000', 9)                   # 0x5c board part
    h += struct.pack('<HHH', 2, 41, 7)          # 0x65 board id, 0x67 fw rev, 0x69 build
    h += _text('abc1234', 10)                   # 0x6b commit hash
    assert len(h) == 117
    return bytes(h)


def _bms_header(device=b'BMS\x00\x00\x00\x00\x00\x00\x00', usec=250000):
    h = bytearray([0xb2, 0, 0xfb]) + struct.pack('<II', T0, usec) + bytes([3, 0])
    h += device                                 # 0x0d device name, 10 bytes
    h += _text('FST TEST', 16)                  # 0x17 module model
    h += bytes([2])                             # 0x27 cells in parallel
    h += _text('40-00000-01', 16)               # 0x28 board part
    h += _text('ZZ0000TEST01', 16)              # 0x38 serial
    h += _text('BCBM9999', 16)                  # 0x48 BCBM id
    h += _text('75-00001', 16)                  # 0x58 firmware part
    h += struct.pack('<HH', 7, 321)             # 0x68 fw rev, 0x6a build
    h += _text('abc1234', 16)                   # 0x6c commit hash
    h += bytes([1])                             # 0x7c bank
    assert len(h) == 125
    return bytes(h)


def _entry(message_type, text, ts=T0 + 10):
    body = bytes([message_type]) + struct.pack('<I', ts) + struct.pack('<I', 1000) + bytes([1, 1]) + text
    return bytes([0xb2, len(body) + 2]) + body


def _parse(data, name):
    """LogData, the TXT output and the JSON output for data saved as name."""
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, name)
        with open(path, 'wb') as f:
            f.write(data + b'\xff' * 64)
        ld = LogData(LogFile(path), timezone_offset=0)
        out = os.path.join(d, 'out.txt')
        parse_log(path, out, output_format='txt', logger=logging.getLogger('t'))
        txt = open(out, encoding='utf-8-sig').read()
        parse_log(path, out, output_format='json', logger=logging.getLogger('t'))
        js = json.load(open(out.replace('.txt', '.json')))
    return ld, txt, js


def _gen3_mbb_file(header):
    return _frame(header) + _entry(0xfd, b'synthetic text one\x00') + _entry(0xfd, b'synthetic text two\x00')


def _gen3_bms_file(header):
    return _frame(header) + _entry(0x60, b'\x01\x02\x03\x04') + _entry(0xfd, b'synthetic bms text\x00')


def _title(txt):
    return txt.splitlines()[0]


def _header_lines(txt):
    lines = txt.splitlines()
    out = {}
    for l in lines[2:lines.index('', 2)]:
        label = 'VIN (from filename)' if l.startswith('VIN (from filename) ') else l[:18].strip()
        out[label] = l[max(18, len(label)):].strip()
    return out


def test_invented_vins():
    assert is_valid_vin_checksum(VIN_A) and is_valid_vin_checksum(VIN_B)
    assert not is_valid_vin_checksum(VIN_BAD)


# Log type

def test_mbb_normal_header():
    ld, txt, js = _parse(_gen3_mbb_file(_mbb_header()), 'synthetic.bin')
    assert ld.log_file.log_type == 'MBB' and ld.log_version == REV3
    assert _title(txt) == 'Zero MBB log' and js['metadata']['log_type'] == 'MBB'


def test_mbb_escape_moves_device_name_in_raw_bytes():
    # a 0xb2 in the sub-second field is escaped, so the raw device name sits at 0x0e
    header = _mbb_header(usec=0x0000b200)
    data = _gen3_mbb_file(header)
    assert data[0x0e:0x11] == b'MBB' and data[0x0d:0x10] != b'MBB'
    ld, txt, js = _parse(data, 'synthetic.bin')
    assert ld.log_file.log_type == 'MBB'
    assert _title(txt) == 'Zero MBB log' and js['metadata']['log_type'] == 'MBB'
    assert ld.header_info['VIN'] == VIN_A


def test_bms_normal_header_and_fields():
    ld, txt, js = _parse(_gen3_bms_file(_bms_header()), 'synthetic.bin')
    assert ld.log_file.log_type == 'BMS'
    assert _title(txt) == 'Zero BMS log' and js['metadata']['log_type'] == 'BMS'
    hi = ld.header_info
    assert (hi['Module model'], hi['BMS serial number'], hi['BCBM id'], hi['Firmware part'],
            hi['Firmware rev.'], hi['Firmware build']) == ('FST TEST', 'ZZ0000TEST01', 'BCBM9999', '75-00001', 7, 321)
    li = js['log_info']
    assert (li['module_model'], li['bcbm_id'], li['firmware_part'], li['firmware_rev'], li['firmware_build']) \
        == ('FST TEST', 'BCBM9999', '75-00001', 7, 321)


# Pack serial: none on Gen3 BMS, unchanged on legacy BMS

def test_gen3_bms_has_no_pack_serial():
    ld, txt, js = _parse(_gen3_bms_file(_bms_header()), 'synthetic.bin')
    assert 'Pack serial number' not in ld.header_info
    lines = _header_lines(txt)
    assert 'Pack serial number' not in lines and lines['BCBM id'] == 'BCBM9999'
    assert js['log_info']['pack_serial_number'] is None and js['log_info']['bcbm_id'] == 'BCBM9999'


def test_gen3_bms_shifted_header_has_no_pack_serial():
    # one byte lost before the name: the old search landed on the board part here
    header = _bms_header()
    header = header[:0x0d] + header[0x0e:]
    ld, txt, js = _parse(_gen3_bms_file(header), 'synthetic.bin')
    assert ld.log_file.log_type == 'BMS'
    assert 'Pack serial number' not in ld.header_info
    assert '40-00000-01' not in txt
    assert js['log_info']['pack_serial_number'] is None


def test_legacy_ring_buffer_bms_keeps_pack_serial():
    # starts with an ordinary 0xb2 entry, not 0xfb, so not an FST/Gen3 file
    data = _entry(0x60, b'\x01\x02\x03\x04') + b'\x00' * 8 + b'ZZ0000TEST02' + b'\x00' * 4 + b'00tb0007\x00'
    data += b'\x00' * 64 + _entry(0x60, b'\x01\x02\x03\x04')
    ld, txt, js = _parse(data, 'synthetic_BMS.bin')
    assert ld.log_file.log_type == 'BMS'
    assert ld.header_info['BMS serial number'] == 'ZZ0000TEST02'
    assert ld.header_info['Pack serial number'] == '00tb0007'
    assert _header_lines(txt)['Pack serial number'] == '00tb0007'
    assert js['log_info']['pack_serial_number'] == '00tb0007'


def test_bms_124_byte_header_lost_b():
    # the 'B' of 'BMS' is lost: 'MS' at 0x0d and every later field one byte early
    full = _bms_header()
    header = full[:0x0d] + full[0x0e:]
    assert len(header) == 124 and header[0x0d:0x0f] == b'MS'
    ld, txt, js = _parse(_gen3_bms_file(header), 'synthetic.bin')
    assert ld.log_file.log_type == 'BMS' and _title(txt) == 'Zero BMS log' and js['metadata']['log_type'] == 'BMS'
    assert ld.header_info['Firmware part'] == '75-00001' and ld.header_info['BCBM id'] == 'BCBM9999'
    assert ld.header_info['Module model'] == 'FST TEST' and ld.header_info['Firmware build'] == 321


def test_bms_124_byte_header_lost_byte_before_name():
    full = _bms_header()
    header = full[:0x0c] + full[0x0d:]
    assert len(header) == 124 and header[0x0c:0x0f] == b'BMS'
    ld, _txt, js = _parse(_gen3_bms_file(header), 'synthetic.bin')
    assert ld.log_file.log_type == 'BMS' and js['metadata']['log_type'] == 'BMS'
    assert ld.header_info['BMS serial number'] == 'ZZ0000TEST01'


def test_bms_126_byte_header_trailing_nul():
    header = _bms_header() + b'\x00'
    ld, _txt, js = _parse(_gen3_bms_file(header), 'synthetic.bin')
    assert ld.log_file.log_type == 'BMS' and js['metadata']['log_type'] == 'BMS'
    assert ld.header_info['Firmware rev.'] == 7 and ld.header_info['Firmware build'] == 321


def test_bms_bbms_header():
    # an extra 'B' and one NUL pad dropped: later fields stay in place
    header = _bms_header(device=b'BBMS\x00\x00\x00\x00\x00\x00')
    ld, txt, js = _parse(_gen3_bms_file(header), 'synthetic.bin')
    assert ld.log_file.log_type == 'BMS' and _title(txt) == 'Zero BMS log' and js['metadata']['log_type'] == 'BMS'
    assert ld.header_info['Module model'] == 'FST TEST' and ld.header_info['Firmware part'] == '75-00001'


def test_bms_fields_withheld_without_the_firmware_part_anchor():
    full = bytearray(_bms_header())
    full[0x58:0x60] = b'\xff' * 8                # firmware part damaged
    assert Gen2.fst_bms_header_fields(bytes(full)) == []


def test_bms_damaged_text_field_is_withheld():
    full = bytearray(_bms_header())
    full[0x48:0x58] = b'\x01' * 16               # BCBM id unreadable
    keys = [k for k, _ in Gen2.fst_bms_header_fields(bytes(full))]
    assert 'BCBM id' not in keys and 'Firmware part' in keys and 'Module model' in keys


def test_unreadable_device_name_uses_bms_only_entry_types():
    header = _bms_header(device=b'\x01' * 10)
    data = _frame(header) + _entry(0x60, b'\x01\x02') + _entry(0x61, b'\x01\x02')
    assert Gen2.fst_device_name(Gen2.fst_header_entry(data)) is None
    assert Gen2.fst_log_type(data) == 'BMS'


def test_unreadable_device_name_with_mixed_evidence_is_undecided():
    header = _bms_header(device=b'\x01' * 10)
    data = _frame(header) + _entry(0x60, b'\x01\x02') + _entry(0x47, b'\x01\x02')
    assert Gen2.fst_log_type(data) is None


def test_legacy_file_unchanged():
    # classic REV0 MBB layout: 'MBB' magic, VIN at 0x240, entries from 0x610
    body = bytes([0x01]) + struct.pack('<I', T0) + b'\x10\x20\x30\x40\x50\x60'
    entry = bytes([0xb2, len(body) + 2]) + body
    header = bytearray(0x600)
    header[0:4] = b'MBB\x00'
    header[0x26:0x2a] = b'\xa1\xa1\xa1\xa1'
    header[0x2a:0x2a + 20] = b'Jan  1 2020 00:00:00'
    header[0x240:0x240 + 17] = VIN_A.encode()
    start = 0x610
    header += b'\xa2\xa2\xa2\xa2' + struct.pack('<III', start + len(entry) * 2, start, 2)
    header += b'\x00' * (start - len(header))
    ld, txt, js = _parse(bytes(header) + entry + entry, 'synthetic_%s_MBB.bin' % VIN_B)
    assert ld.log_version != REV3 and ld.log_file.log_type == 'MBB'
    assert _title(txt) == 'Zero MBB log' and js['metadata']['log_type'] == 'MBB'
    assert _header_lines(txt)['VIN'] == VIN_A and js['log_info']['vin_source'] == 'header'
    assert 'module_model' not in js['log_info']


# VIN rule

def test_header_vin_wins_over_filename():
    ld, txt, js = _parse(_gen3_mbb_file(_mbb_header(vin=VIN_A)), 'x_%s_MBB.bin' % VIN_B)
    assert ld.header_info['VIN'] == VIN_A and ld.vin_source == 'header'
    assert _header_lines(txt)['VIN'] == VIN_A
    assert js['log_info']['vin'] == VIN_A and js['log_info']['vin_source'] == 'header'


def test_valid_filename_vin_used_and_labeled():
    ld, txt, js = _parse(_gen3_bms_file(_bms_header()), '20240101_%s_BMS1.bin' % VIN_B)
    assert ld.header_info['VIN'] == VIN_B and ld.vin_source == 'filename'
    assert _header_lines(txt)['VIN (from filename)'] == VIN_B
    assert js['log_info']['vin'] == VIN_B and js['log_info']['vin_source'] == 'filename'


def test_valid_lowercase_filename_vin_is_upper_cased():
    ld, _txt, js = _parse(_gen3_bms_file(_bms_header()), '%s_bms.bin' % VIN_B.lower())
    assert js['log_info']['vin'] == VIN_B and js['log_info']['vin_source'] == 'filename'


def test_invalid_filename_vin_gives_unknown():
    # MBB header whose VIN fails the check digit, file name VIN fails too
    ld, txt, js = _parse(_gen3_mbb_file(_mbb_header(vin=VIN_BAD)), 'x_%s_MBB.bin' % VIN_BAD)
    assert ld.header_info['VIN'] == 'Unknown' and ld.vin_source is None
    assert _header_lines(txt)['VIN'] == 'Unknown'
    assert js['log_info']['vin'] == 'Unknown' and js['log_info']['vin_source'] is None


def test_invalid_filename_vin_on_bms_adds_no_vin_line():
    ld, txt, js = _parse(_gen3_bms_file(_bms_header()), 'x_%s_BMS1.bin' % VIN_BAD)
    assert 'VIN' not in ld.header_info and not any(k.startswith('VIN') for k in _header_lines(txt))
    assert js['log_info']['vin'] == 'Unknown' and js['log_info']['vin_source'] is None
