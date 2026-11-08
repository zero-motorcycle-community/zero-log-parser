"""
Tests for Gen3/FST 0xFD text entries.

On FST/Gen3 files (log_version REV3) a 0xFD payload starts with the 6-byte entry
prefix (u32 sub-second microseconds, u8 sequence counter, u8 marker) and the
NUL-terminated message follows it. debug_message() reads from byte 0, so on
those entries it showed the prefix bytes as one to three stray characters.
gen3_debug_message() reads the message from byte 6. Plain-text payloads, which
legacy-platform ring-buffer logs (also log_version REV3) send to it, are left to
debug_message() unchanged.

Plain test_* functions, stdlib only, synthetic in-process buffers.
"""

import logging
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from zero_log_parser import Gen2

LOGGER = logging.getLogger('test_gen3_debug_message_prefix')


def _prefix(subsecond_us=250000, sequence=7, marker=0xf9):
    return struct.pack('<IBB', subsecond_us, sequence, marker)


def _payload(text, subsecond_us=250000):
    return bytearray(_prefix(subsecond_us) + text.encode('ascii') + b'\x00')


def _block(message_type, timestamp, payload):
    """A framed entry: 0xb2, length, type, u32 timestamp, payload. The test
    payloads hold no 0xb2 or 0xfe bytes, so no escaping is needed."""
    body = bytes([message_type]) + struct.pack('<I', timestamp) + bytes(payload)
    assert b'\xb2' not in body and b'\xfe' not in body
    return bytearray([0xb2, len(body) + 2]) + bytearray(body)


def test_prefix_is_skipped():
    for text in ('Exiting Hibernate', 'State change from STOP to CHRG',
                 'Saving stats, hibernating for 86400 sec'):
        assert Gen2.gen3_debug_message(_payload(text))['event'] == text


def test_old_reader_shows_the_prefix_bytes():
    # What the FST path showed before: the message read from byte 0 stops at
    # the first zero byte of the prefix.
    event = Gen2.debug_message(_payload('Exiting Hibernate'))['event']
    assert 'Exiting Hibernate' not in event


def test_subsecond_zero_and_maximum_still_skip():
    for us in (0, 1000000):
        assert Gen2.gen3_debug_message(_payload('Key On', subsecond_us=us))['event'] == 'Key On'


def test_plain_text_payload_is_left_to_debug_message():
    # Legacy ring-buffer logs reach this decoder with no prefix. Four
    # printable bytes are always above the sub-second limit.
    payload = bytearray(b'Module 00 Contactor is now Closed\x00')
    assert Gen2.gen3_debug_message(payload) == Gen2.debug_message(payload)


def test_short_payload_is_left_to_debug_message():
    payload = bytearray(b'abc\x00')
    assert Gen2.gen3_debug_message(payload) == Gen2.debug_message(payload)


def test_content_handling_matches_debug_message():
    # The message after the prefix gets the same content-based handling as
    # legacy text (here the SOC line, which sets log level STATE).
    text = 'SOC:1,2,3,104000,5,6,7,8,9,10,11'
    assert Gen2.gen3_debug_message(_payload(text)) == Gen2.debug_message(bytearray(text.encode() + b'\x00'))


def test_dispatch_selects_the_gen3_reader_only_on_the_fst_path():
    assert Gen2._entry_parsers()[0xfd] == Gen2.debug_message
    assert Gen2._entry_parsers(rev3=True)[0xfd] == Gen2.gen3_debug_message


def test_parse_entry_reads_the_message_on_the_fst_path():
    block = _block(0xfd, 1_700_000_000, _payload('Exiting Hibernate'))
    _, fst, _ = Gen2.parse_entry(block, 0, 0, LOGGER, timezone_offset=0, rev3=True)
    _, legacy, _ = Gen2.parse_entry(block, 0, 0, LOGGER, timezone_offset=0)
    assert fst['event'] == 'Exiting Hibernate'
    assert legacy['event'] != 'Exiting Hibernate'
