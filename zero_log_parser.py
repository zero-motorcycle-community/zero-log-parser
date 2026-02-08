#!/usr/bin/env python3

"""
Little decoder utility to parse Zero Motorcycle main bike board (MBB) and
battery management system (BMS) logs. These may be extracted from the bike
using the Zero mobile app. Once paired over bluetooth, select 'Support' >
'Email bike logs' and send the logs to yourself rather than / in addition to
zero support.

Usage:

   $ python zero_log_parser.py <*.bin file> [-o output_file]

Architecture:
This parser uses a hybrid approach combining Gen2/Gen3 binary parsing with
structured data extraction. Gen2 parsers have been optimized to generate
structured data directly from binary data, eliminating the "string formatting
→ regex re-parsing" overhead that existed previously.

Gen2 Parser Optimization - Completed (19 methods):
- Phase 1: disarmed_status(), bms_contactor_state(), bms_soc_adj_voltage()
- Phase 2: battery_status(), bms_discharge_cut(), bms_contactor_drive()
- Phase 3: bms_curr_sens_zero(), sevcon_status(), bms_isolation_fault(), power_state()
- Phase 4: charger_status(), bms_reflash(), key_state()
- Phase 5: battery_discharge_current_limited(), low_chassis_isolation(), sevcon_power_state(), battery_contactor_closed()
- Additional: vehicle_state_telemetry(), sensor_data()

All optimized parsers use direct binary extraction via BinaryTools.unpack() and
generate structured JSON data with human-readable conditions for all output formats.
This eliminates the previous "string formatting → regex re-parsing" overhead while
maintaining full backward compatibility. Redundant patterns removed from improve_message_parsing.

"""

import codecs
import json
import logging
import os
import re
import string
import struct
from collections import OrderedDict, namedtuple
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from math import trunc
from time import gmtime, localtime, strftime
from typing import Dict, List, Union, Optional

# Parser version - try to import from package, fallback to hardcoded
try:
    from src.zero_log_parser import __version__ as PARSER_VERSION
except ImportError:
    PARSER_VERSION = "2.2.0"  # Fallback version

# Localized time format - use system locale preference
ZERO_TIME_FORMAT = '%Y-%m-%d %H:%M:%S'  # ISO format is more universal
# The output from the MBB (via serial port) lists time as GMT-7
MBB_TIMESTAMP_GMT_OFFSET = -7 * 60 * 60


class MismatchingVinError(Exception):
    """Raised when attempting to merge LogData objects with different VINs"""
    def __init__(self, vin1, vin2):
        self.vin1 = vin1
        self.vin2 = vin2
        super().__init__(f"Cannot merge logs with different VINs: '{vin1}' != '{vin2}'")


try:
    from src.zero_log_parser.utils import get_timezone_offset
except ImportError:
    try:
        from zero_log_parser.utils import get_timezone_offset
    except ImportError:
        pass


@dataclass
class ProcessedLogEntry:
    """Standardized log entry structure for all output formats"""
    entry_number: int
    timestamp: str
    sort_timestamp: float
    log_level: str
    event: str
    conditions: str
    uninterpreted: str = ""
    structured_data: Optional[dict] = None
    has_structured_data: bool = False
    message_type: str = "unknown"
    original_timestamp: Optional[str] = None  # Raw timestamp before interpolation

    def __eq__(self, other):
        """
        Compare ProcessedLogEntry objects based on content, not sequence.

        Compares: original_timestamp (or timestamp), event, message_type,
        and structured_data if available, otherwise conditions.
        Excludes: entry_number, log_level, sort_timestamp, uninterpreted, has_structured_data
        """
        if not isinstance(other, ProcessedLogEntry):
            return False

        # Use original_timestamp if available, fallback to timestamp
        self_ts = self.original_timestamp or self.timestamp
        other_ts = other.original_timestamp or other.timestamp

        # Use structured_data if available, otherwise use conditions
        self_content = self.structured_data if self.structured_data else self.conditions
        other_content = other.structured_data if other.structured_data else other.conditions

        return (
            self_ts == other_ts and
            self.event == other.event and
            self.message_type == other.message_type and
            self_content == other_content
        )

    def __hash__(self):
        """
        Generate hash based on content for set operations and deduplication.

        Uses same fields as __eq__: original_timestamp (or timestamp), event,
        message_type, and structured_data or conditions.
        """
        # Use original_timestamp if available, fallback to timestamp
        timestamp_val = self.original_timestamp or self.timestamp

        # Handle structured_data vs conditions with robust hashing for complex data
        if self.structured_data:
            # Convert to JSON string for consistent hashing of complex data structures
            import json
            content_val = json.dumps(self.structured_data, sort_keys=True, separators=(',', ':'))
        else:
            content_val = self.conditions

        return hash((
            timestamp_val,
            self.event,
            self.message_type,
            content_val
        ))


def improve_message_parsing(event_text: str, conditions_text: str = None, verbosity_level: int = 1, logger=None) -> tuple:
    """
    Improve message parsing by removing redundant prefixes and converting structured data to JSON.

    Args:
        event_text: The event text to process
        conditions_text: Optional conditions text
        verbosity_level: Verbosity level (0=quiet, 1=normal, 2=verbose, 3+=very verbose)
        logger: Logger instance for verbose output

    Returns tuple: (improved_event, improved_conditions, json_data, has_json_data, was_modified, modification_type)

    Note: This method tracks modifications to help identify which Gen2 binary parsers
    need to incorporate advanced message parsing directly for optimization.
    """
    if not event_text:
        return event_text, conditions_text, None, False, False, None

    improved_event = event_text
    improved_conditions = conditions_text
    json_data = None
    was_modified = False
    modification_type = None

    # Remove redundant DEBUG: prefix since we have log_level
    original_event = improved_event
    if improved_event.startswith('DEBUG: '):
        improved_event = improved_event[7:]
        was_modified = True
        modification_type = "prefix_removal"
    elif improved_event.startswith('INFO: '):
        improved_event = improved_event[6:]
        was_modified = True
        modification_type = "prefix_removal"
    elif improved_event.startswith('ERROR: '):
        improved_event = improved_event[7:]
        was_modified = True
        modification_type = "prefix_removal"
    elif improved_event.startswith('WARNING: '):
        improved_event = improved_event[9:]
        was_modified = True
        modification_type = "prefix_removal"

    # Handle edge cases only - most patterns have been moved to optimized Gen2 parsers
    try:
        # Handle abbreviated hex patterns from newer log formats (2025+)
        if re.match(r'^0x[0-9a-f]+(\s+0x[0-9a-f]+)*$', improved_event or '', re.IGNORECASE):
            # Parse hex pattern like "0x28 0x02" or "0x01"
            hex_parts = improved_event.split()
            if len(hex_parts) >= 1:
                try:
                    main_type = int(hex_parts[0], 16)
                    # Handle specific abbreviated patterns for newer formats
                    if main_type == 0x28:  # Battery CAN Link Up
                        if len(hex_parts) >= 2:
                            module_num = int(hex_parts[1], 16)
                            improved_event = f"Module {module_num:02d} CAN Link Up"
                        else:
                            improved_event = "Battery CAN Link Up"
                        improved_conditions = None
                        was_modified = True
                        modification_type = "hex_pattern_expansion"
                        if verbosity_level >= 2 and logger:
                            logger.debug(f"Hex pattern expanded: {original_event} → {improved_event} (needs Gen2 optimization)")
                    elif main_type == 0x29:  # Battery CAN Link Down
                        if len(hex_parts) >= 2:
                            module_num = int(hex_parts[1], 16)
                            improved_event = f"Module {module_num:02d} CAN Link Down"
                        else:
                            improved_event = "Battery CAN Link Down"
                        improved_conditions = None
                        was_modified = True
                        modification_type = "hex_pattern_expansion"
                        if verbosity_level >= 2 and logger:
                            logger.debug(f"Hex pattern expanded: {original_event} → {improved_event} (needs Gen2 optimization)")
                    else:
                        # Mark other unknown hex patterns
                        improved_event = f"Unknown (Type 0x{main_type:02x})"
                        if len(hex_parts) > 1:
                            data_parts = [f"0x{int(part, 16):02x}" for part in hex_parts[1:]]
                            improved_conditions = f"Data: {' '.join(data_parts)}"
                        else:
                            improved_conditions = "No additional data"
                        was_modified = True
                        modification_type = "hex_pattern_expansion"
                        if verbosity_level >= 2 and logger:
                            logger.debug(f"Hex pattern expanded: {original_event} → {improved_event} (needs Gen2 optimization)")
                except ValueError:
                    # If hex conversion fails, mark as malformed
                    improved_event = f"Unknown - {improved_event}"
                    improved_conditions = "Malformed hex pattern"
                    was_modified = True
                    modification_type = "malformed_hex_handling"
                    if verbosity_level >= 2 and logger:
                        logger.debug(f"Malformed hex handled: {original_event} → {improved_event} (needs Gen2 optimization)")

        # Handle single character entries (likely corrupted)
        elif improved_event and len(improved_event) == 1 and improved_event.isalpha():
            improved_event = f"Unknown - Single character: {improved_event}"
            improved_conditions = "Possibly corrupted entry"
            was_modified = True
            modification_type = "corrupted_entry_handling"
            if verbosity_level >= 2 and logger:
                logger.debug(f"Corrupted entry handled: {original_event} → {improved_event} (needs Gen2 optimization)")

    except (ValueError, AttributeError, IndexError):
        # If parsing fails, keep original format
        pass

    # Determine if this entry contains JSON data
    has_json_data = json_data is not None

    return improved_event, improved_conditions, json_data, has_json_data, was_modified, modification_type


def determine_log_level(message: str) -> str:
    """Determine log level based on message content patterns"""
    if not message:
        return 'UNKNOWN'

    message_upper = message.upper()

    # Explicit level indicators (check for redundant prefixes)
    if message.startswith('DEBUG:'):
        return 'DEBUG'
    elif message.startswith('INFO:'):
        return 'INFO'
    elif message.startswith('ERROR:') or message.startswith('FAULT:'):
        return 'ERROR'
    elif message.startswith('WARNING:') or message.startswith('WARN:'):
        return 'WARNING'

    # Error patterns
    if any(pattern in message_upper for pattern in [
        'ERROR', 'FAULT', 'FAILED', 'FAILURE', 'CRITICAL', 'ALARM',
        'ABORT', 'EXCEPTION', 'TIMEOUT'
    ]):
        return 'ERROR'

    # Warning patterns
    if any(pattern in message_upper for pattern in [
        'WARNING', 'WARN', 'CAUTION', 'OVERTEMP', 'UNDERVOLT', 'OVERVOLT'
    ]):
        return 'WARNING'

    # State change patterns (important operational states)
    if any(pattern in message_upper for pattern in [
        'RIDING', 'DISARMED', 'CHARGING', 'ARMED', 'STANDBY',
        'POWER ON', 'POWER OFF', 'SLEEP', 'WAKE', 'BOOT',
        'STARTUP', 'SHUTDOWN', 'CONNECTED', 'DISCONNECTED'
    ]):
        return 'STATE'

    # System/informational patterns
    if any(pattern in message_upper for pattern in [
        'MODULE', 'SEVCON', 'CONTACTOR', 'TEMPERATURE', 'VOLTAGE',
        'CURRENT', 'BATTERY', 'MOTOR', 'CONFIG', 'SETTING'
    ]):
        return 'INFO'

    # Debug patterns (verbose/detailed info)
    if any(pattern in message_upper for pattern in [
        'DEBUG', 'TRACE', 'VERBOSE', 'DETAIL'
    ]):
        return 'DEBUG'

    # Default to INFO for unmatched messages
    return 'INFO'


# noinspection PyMissingOrEmptyDocstring
class BinaryTools:
    """
    Utility class for dealing with serialised data from the Zero's
    """

    TYPES = {
        'int8': 'b',
        'uint8': 'B',
        'int16': 'h',
        'uint16': 'H',
        'int32': 'l',
        'uint32': 'L',
        'int64': 'q',
        'uint64': 'Q',
        'float': 'f',
        'double': 'd',
        'char': 's',
        'bool': '?'
    }

    TYPE_CONVERSIONS = {
        'int8': int,
        'uint8': int,
        'int16': int,
        'uint16': int,
        'int32': int,
        'uint32': int,
        'int64': int,
        'uint64': int,
        'float': float,
        'double': float,
        'char': None,  # chr
        'bool': bool
    }

    @classmethod
    def unpack(cls,
               type_name: str,
               buff: bytearray,
               address: int,
               count=1, offset=0) -> Union[bytearray, int, float, bool]:
        # noinspection PyAugmentAssignment
        buff = buff + bytearray(32)
        type_key = type_name.lower()
        type_char = cls.TYPES[type_key]
        type_convert = cls.TYPE_CONVERSIONS[type_key]
        type_format = '<{}{}'.format(count, type_char)
        unpacked = struct.unpack_from(type_format, buff, address + offset)[0]
        if type_convert:
            # if count > 1:
            #     return [type_convert(each) for each in unpacked]
            # else:
            return type_convert(unpacked)
        else:
            return unpacked

    @staticmethod
    def unescape_block(data):
        start_offset = 0

        escape_offset = data.find(b'\xfe')

        while escape_offset != -1:
            escape_offset += start_offset
            if escape_offset + 1 < len(data):
                data[escape_offset] = data[escape_offset] ^ data[escape_offset + 1] - 1
                data = data[0:escape_offset + 1] + data[escape_offset + 2:]
            start_offset = escape_offset + 1
            escape_offset = data[start_offset:].find(b'\xfe')

        return data

    @staticmethod
    def decode_str(log_text_segment: bytearray, encoding='utf-8') -> str:
        """Decodes UTF-8 strings from a test segment, ignoring any errors"""
        return log_text_segment.decode(encoding=encoding, errors='ignore')

    @classmethod
    def unpack_str(cls, log_text_segment: bytearray, address, count=1, offset=0,
                   encoding='utf-8') -> str:
        """Unpacks and decodes UTF-8 strings from a test segment, ignoring any errors"""
        unpacked = cls.unpack('char', log_text_segment, address, count, offset)
        return cls.decode_str(unpacked.partition(b'\0')[0], encoding=encoding)

    @staticmethod
    def is_printable(bytes_or_str: str) -> bool:
        return all(c in string.printable for c in bytes_or_str)

    @classmethod
    def bms_discharge_level_binary(cls, x):
        # Enhanced BMS discharge decoder with state names and better formatting
        state_names = {
            0x01: 'Bike On',
            0x02: 'Charge',
            0x03: 'Idle'
        }
        return {
            'event': 'BMS Discharge Level',
            'conditions':
                '{AH:03.0f}Ah, SOC:{SOC:3d}%, I:{I:+4.0f}A, State:{STATE}, '
                'LowCell:{L}mV, HighCell:{H}mV, Balance:{B:+4d}mV, UnloadedCell:{l}mV, '
                'PackTemp:{PT:3d}°C, BMSTemp:{BT:3d}°C, PackV:{PV:6.1f}V'.format(
                    AH=trunc(BinaryTools.unpack('uint32', x, 0x06) / 1000000.0),
                    SOC=BinaryTools.unpack('uint8', x, 0x0a),
                    I=trunc(BinaryTools.unpack('int32', x, 0x10) / 1000000.0),
                    STATE=state_names.get(BinaryTools.unpack('uint8', x, 0x0f), f'Unknown({BinaryTools.unpack("uint8", x, 0x0f)})'),
                    L=BinaryTools.unpack('uint16', x, 0x0),
                    H=BinaryTools.unpack('uint16', x, 0x02),
                    B=BinaryTools.unpack('uint16', x, 0x02) - BinaryTools.unpack('uint16', x, 0x0),
                    l=BinaryTools.unpack('uint16', x, 0x14),
                    PT=BinaryTools.unpack('uint8', x, 0x04),
                    BT=BinaryTools.unpack('uint8', x, 0x05),
                    PV=BinaryTools.unpack('uint32', x, 0x0b) / 1000.0)
        }

    @classmethod
    def bms_unknown_type_5(cls, x):
        """BMS Unknown Type 5 - raw hex display"""
        hex_data = ' '.join(f'{b:02x}' for b in x[:min(16, len(x))])
        return {
            'event': 'BMS Unknown Type 5',
            'conditions': f'Unknown: {hex_data}'
        }

    @classmethod
    def bms_unknown_type_14(cls, x):
        """BMS Unknown Type 14 - raw hex display"""
        hex_data = ' '.join(f'{b:02x}' for b in x[:min(16, len(x))])
        return {
            'event': 'BMS Unknown Type 14',
            'conditions': f'Unknown: {hex_data}'
        }

    @classmethod
    def mbb_unknown_type_28(cls, x):
        """MBB Unknown Type 28 - raw hex display"""
        hex_data = ' '.join(f'{b:02x}' for b in x[:min(16, len(x))])
        return {
            'event': 'MBB Unknown Type 28',
            'conditions': f'Unknown: {hex_data}'
        }

    @classmethod
    def mbb_unknown_type_38(cls, x):
        """MBB Unknown Type 38 - raw hex display"""
        hex_data = ' '.join(f'{b:02x}' for b in x[:min(16, len(x))])
        return {
            'event': 'MBB Unknown Type 38',
            'conditions': f'Unknown: {hex_data}'
        }

    @classmethod
    def mbb_bt_rx_buffer_overflow(cls, x):
        """MBB BT RX Buffer Overflow"""
        hex_data = ' '.join(f'{b:02x}' for b in x[:min(16, len(x))])
        return {
            'event': 'MBB BT RX Buffer Overflow',
            'conditions': f'Data: {hex_data}'
        }


vin_length = 17
vin_guaranteed_prefix = '538'


def is_vin(vin: str):
    """Whether the string matches a Zero VIN."""
    return (BinaryTools.is_printable(vin)
            and len(vin) == vin_length
            and vin.startswith(vin_guaranteed_prefix))


# noinspection PyMissingOrEmptyDocstring
class LogFile:
    """
    Wrapper for our raw log file
    """

    def __init__(self, file_path: str, logger=None):
        self.file_path = file_path
        self._data = bytearray()
        self.reload()
        self.log_type = self.get_log_type()

    def reload(self):
        with open(self.file_path, 'rb') as f:
            self._data = bytearray(f.read())

    def index_of_sequence(self, sequence, start=None):
        try:
            return self._data.index(sequence, start)
        except ValueError:
            return None

    def indexes_of_sequence(self, sequence, start=None):
        result = []
        last_index = self.index_of_sequence(sequence, start)
        while last_index is not None:
            result.append(last_index)
            last_index = self.index_of_sequence(sequence, last_index + 1)
        return result

    def unpack(self, type_name, address, count=1, offset=0):
        return BinaryTools.unpack(type_name, self._data, address + offset,
                                  count=count)

    def decode_str(self, address, count=1, offset=0, encoding='utf-8'):
        return BinaryTools.decode_str(BinaryTools.unpack('char', self._data, address + offset,
                                                         count=count), encoding=encoding)

    def unpack_str(self, address, count=1, offset=0, encoding='utf-8') -> str:
        """Unpacks and decodes UTF-8 strings from a test segment, ignoring any errors"""
        unpacked = self.unpack('char', address, count, offset)
        return BinaryTools.decode_str(unpacked.partition(b'\0')[0], encoding=encoding)

    def is_printable(self, address, count=1, offset=0) -> bool:
        unpacked = self.unpack('char', address, count, offset).decode('utf-8', 'ignore')
        return BinaryTools.is_printable(unpacked) and len(unpacked) == count

    def extract(self, start_address, length, offset=0):
        return self._data[start_address + offset:
                          start_address + length + offset]

    def raw(self):
        return bytearray(self._data)

    log_type_mbb = 'MBB'
    log_type_bms = 'BMS'
    log_type_unknown = 'Unknown Type'

    def get_log_type(self):
        log_type = None
        if self.is_printable(0x000, count=3):
            log_type = self.unpack_str(0x000, count=3)
        elif self.is_printable(0x00d, count=3):
            log_type = self.unpack_str(0x00d, count=3)
        elif self.log_type_mbb in self.file_path.upper():
            log_type = self.log_type_mbb
        elif self.log_type_bms in self.file_path.upper():
            log_type = self.log_type_bms
        if log_type not in [self.log_type_mbb, self.log_type_bms]:
            log_type = self.log_type_unknown
        return log_type

    def is_mbb(self):
        return self.log_type == self.log_type_mbb

    def is_bms(self):
        return self.log_type == self.log_type_bms

    def is_unknown(self):
        return self.log_type == self.log_type_unknown

    def get_filename_vin(self):
        basename = os.path.basename(self.file_path)
        if basename and len(basename) > vin_length and vin_guaranteed_prefix in basename:
            vin_index = basename.index(vin_guaranteed_prefix)
            return basename[vin_index:vin_index + vin_length]


def convert_mv_to_v(milli_volts: int) -> float:
    return milli_volts / 1000.0


def convert_ratio_to_percent(numerator: Union[int, float], denominator: Union[int, float]) -> float:
    return numerator * 100 / denominator if denominator != 0 else 0


def convert_bit_to_on_off(bit: int) -> str:
    return 'On' if bit else 'Off'


def hex_of_value(value):
    if isinstance(value, str):
        return value
    if isinstance(value, float):
        return value.hex()
    if isinstance(value, bytearray):
        return value.hex()
    if isinstance(value, bytes):
        return value.hex()
    return str(value)


def display_bytes_hex(x: Union[List[int], bytearray, bytes, str]):
    byte_values = bytearray(x, 'utf8') if isinstance(x, str) else x
    return ' '.join(['0x{:02x}'.format(c) for c in byte_values])


EMPTY_CSV_VALUE = ''
CSV_DELIMITER = ';'


def print_value_tabular(value, omit_units=False):
    """Stringify the value for CSV/TSV; treat None as empty text."""
    if value is None:
        return EMPTY_CSV_VALUE
    if isinstance(value, str) and not BinaryTools.is_printable(value):
        return display_bytes_hex(value)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, bytearray):
        return display_bytes_hex(value)
    if isinstance(value, float):
        return '{0:.2f}'.format(value)
    if omit_units and value is str:
        matches = re.match(r"^([0-9.]+)\s*([A-Za-z]+)$", value)
        if matches:
            return matches.group(1)

    # Clean up string values to be CSV-safe
    result = str(value)
    if isinstance(value, str):
        # Replace problematic characters that break CSV parsing
        result = result.replace('\t', ' ').replace('\n', ' ').replace('\r', ' ')
        result = result.replace(';', ':')  # Replace CSV delimiter
        # Remove non-printable characters except spaces
        result = ''.join(c for c in result if c.isprintable() or c == ' ')
        result = result.strip()

    return result


class Gen2:
    @classmethod
    def timestamp_from_event(cls, unescaped_block, use_local_time=True, timezone_offset=None):
        timestamp = BinaryTools.unpack('uint32', unescaped_block, 0x01)
        if timestamp > 0xfff:
            # Apply timezone offset and use GMT to avoid double timezone conversion
            adjusted_timestamp = timestamp + (timezone_offset or 0)
            timestamp_corrected = gmtime(adjusted_timestamp)
            return strftime(ZERO_TIME_FORMAT, timestamp_corrected)
        else:
            return str(timestamp)

    @classmethod
    def interpolate_missing_timestamps(cls, entries_with_metadata, logger=None):
        """
        Improve missing timestamp detection by interpolating from neighboring entries.

        Args:
            entries_with_metadata: List of tuples (sort_timestamp, entry_payload, entry_num)
            logger: Optional logger for debugging

        Returns:
            List of tuples with improved timestamps
        """
        if not entries_with_metadata:
            return entries_with_metadata

        improved_entries = []

        # First pass: identify entries with valid and invalid timestamps
        for i, (sort_timestamp, entry_payload, entry_num) in enumerate(entries_with_metadata):
            time_str = entry_payload.get('time', '0')
            has_valid_timestamp = not time_str.isdigit() and sort_timestamp > 0

            improved_entries.append({
                'index': i,
                'sort_timestamp': sort_timestamp,
                'entry_payload': entry_payload,
                'entry_num': entry_num,
                'has_valid_timestamp': has_valid_timestamp,
                'original_time_str': time_str
            })

        # Second pass: interpolate missing timestamps
        for i, entry in enumerate(improved_entries):
            if not entry['has_valid_timestamp']:
                # Find nearest valid timestamps before and after
                before_entry = None
                after_entry = None

                # Look backwards for valid timestamp
                for j in range(i - 1, -1, -1):
                    if improved_entries[j]['has_valid_timestamp']:
                        before_entry = improved_entries[j]
                        break

                # Look forwards for valid timestamp
                for j in range(i + 1, len(improved_entries)):
                    if improved_entries[j]['has_valid_timestamp']:
                        after_entry = improved_entries[j]
                        break

                # Interpolate timestamp if we have neighbors
                interpolated_timestamp = None
                interpolated_time_str = None

                if before_entry and after_entry:
                    # Interpolate between two valid timestamps
                    before_ts = before_entry['sort_timestamp']
                    after_ts = after_entry['sort_timestamp']
                    before_entry_num = before_entry['entry_num']
                    after_entry_num = after_entry['entry_num']
                    current_entry_num = entry['entry_num']

                    # Calculate position ratio based on entry numbers
                    if after_entry_num != before_entry_num:
                        ratio = (current_entry_num - before_entry_num) / (after_entry_num - before_entry_num)
                        interpolated_timestamp = before_ts + ratio * (after_ts - before_ts)
                    else:
                        interpolated_timestamp = before_ts

                elif before_entry:
                    # Extrapolate from previous entry (assume 1 second interval)
                    entry_gap = entry['entry_num'] - before_entry['entry_num']
                    interpolated_timestamp = before_entry['sort_timestamp'] + entry_gap

                elif after_entry:
                    # Extrapolate from next entry (assume 1 second interval)
                    entry_gap = after_entry['entry_num'] - entry['entry_num']
                    interpolated_timestamp = after_entry['sort_timestamp'] - entry_gap

                # Apply interpolated timestamp if we calculated one
                if interpolated_timestamp and interpolated_timestamp > 0:
                    try:
                        from datetime import datetime
                        interpolated_dt = datetime.fromtimestamp(interpolated_timestamp)
                        interpolated_time_str = interpolated_dt.strftime(ZERO_TIME_FORMAT)

                        # Update the entry
                        entry['sort_timestamp'] = interpolated_timestamp
                        entry['entry_payload']['time'] = interpolated_time_str
                        entry['has_valid_timestamp'] = True

                        if logger:
                            logger.debug('Interpolated timestamp for entry %d: %s (was: %s)',
                                       entry['entry_num'] + 1, interpolated_time_str, entry['original_time_str'])
                    except:
                        # If interpolation fails, keep original
                        pass

        # Return in the original format
        return [(entry['sort_timestamp'], entry['entry_payload'], entry['entry_num'])
                for entry in improved_entries]

    @classmethod
    def bms_discharge_level(cls, x):
        bike_states = {
            0x01: 'Bike On',
            0x02: 'Charge',
            0x03: 'Idle'
        }

        # Extract raw data once from binary
        structured_data = {
            'amp_hours': trunc(BinaryTools.unpack('uint32', x, 0x06) / 1000000.0),
            'state_of_charge_percent': BinaryTools.unpack('uint8', x, 0x0a),
            'current_amps': trunc(BinaryTools.unpack('int32', x, 0x10) / 1000000.0),
            'voltage_low_cell_volts': BinaryTools.unpack('uint16', x, 0x0) / 1000.0,
            'voltage_unloaded_cell_volts': BinaryTools.unpack('uint16', x, 0x14) / 1000.0,
            'voltage_high_cell_volts': BinaryTools.unpack('uint16', x, 0x02) / 1000.0,
            'voltage_balance_mv': BinaryTools.unpack('uint16', x, 0x02) - BinaryTools.unpack('uint16', x, 0x0),
            'pack_temp_celsius': BinaryTools.unpack('uint8', x, 0x04),
            'bms_temp_celsius': BinaryTools.unpack('uint8', x, 0x05),
            'pack_voltage_volts': BinaryTools.unpack('uint32', x, 0x0b) / 1000.0,
            'pack_voltage_mv': BinaryTools.unpack('uint32', x, 0x0b),  # For backward compatibility
            'mode': bike_states.get(BinaryTools.unpack('uint8', x, 0x0f))
        }

        # Generate legacy conditions string for backward compatibility
        legacy_conditions = (
            '{AH:03.0f} AH, SOC:{SOC:3d}%, I:{I:3.0f}A, L:{L:4.2f}V, l:{l:4.2f}V, H:{H:4.2f}V, B:{B:03d}mV, '
            'PT:{PT:03d}C, BT:{BT:03d}C, PV:{PV:6.1f}V, M:{M}').format(
                AH=structured_data['amp_hours'],
                SOC=structured_data['state_of_charge_percent'],
                I=structured_data['current_amps'],
                L=structured_data['voltage_low_cell_volts'],
                l=structured_data['voltage_unloaded_cell_volts'],
                H=structured_data['voltage_high_cell_volts'],
                B=structured_data['voltage_balance_mv'],
                PT=structured_data['pack_temp_celsius'],
                BT=structured_data['bms_temp_celsius'],
                PV=structured_data['pack_voltage_volts'],
                M=structured_data['mode']
            )

        return {
            'event': 'Discharge level',
            'structured_data': structured_data,  # NEW: Direct structured data
            'conditions': legacy_conditions  # LEGACY: For backward compatibility
        }

    @classmethod
    def bms_charge_event_fields(cls, x):
        return {
            'AH': trunc(BinaryTools.unpack('uint32', x, 0x06) / 1000000.0),
            'B': BinaryTools.unpack('uint16', x, 0x02) - BinaryTools.unpack('uint16', x, 0x0),
            'L': BinaryTools.unpack('uint16', x, 0x00) / 1000.0,  # Convert to volts
            'H': BinaryTools.unpack('uint16', x, 0x02) / 1000.0,  # Convert to volts
            'PT': BinaryTools.unpack('uint8', x, 0x04),
            'BT': BinaryTools.unpack('uint8', x, 0x05),
            'SOC': BinaryTools.unpack('uint8', x, 0x0a),
            'PV': BinaryTools.unpack('uint32', x, 0x0b) / 1000.0  # Convert to volts
        }

    @classmethod
    def bms_charge_full(cls, x):
        # Extract raw binary data once into structured format
        fields = cls.bms_charge_event_fields(x)

        # Build structured data dictionary with descriptive names
        structured_data = {
            'amp_hours': fields['AH'],
            'state_of_charge_percent': fields['SOC'],
            'voltage_low_cell_volts': fields['L'],
            'voltage_high_cell_volts': fields['H'],
            'voltage_balance_mv': fields['B'],
            'pack_temp_celsius': fields['PT'],
            'bms_temp_celsius': fields['BT'],
            'pack_voltage_volts': fields['PV'],
            'event_type': 'charge_complete'
        }

        # Generate legacy conditions string for backward compatibility
        legacy_conditions = (
            '{AH:03.0f} AH, SOC: {SOC}%,         L:{L:4.2f}V,         H:{H:4.2f}V, B:{B:03d}mV, '
            'PT:{PT:03d}C, BT:{BT:03d}C, PV:{PV:6.1f}V').format_map(fields)

        return {
            'event': 'Charged To Full',
            'structured_data': structured_data,  # NEW: Direct structured data
            'conditions': legacy_conditions  # LEGACY: For backward compatibility
        }

    @classmethod
    def bms_discharge_low(cls, x):
        # Extract raw binary data once into structured format
        fields = cls.bms_charge_event_fields(x)

        # Build structured data dictionary with descriptive names
        structured_data = {
            'amp_hours': fields['AH'],
            'state_of_charge_percent': fields['SOC'],
            'voltage_low_cell_volts': fields['L'],
            'voltage_high_cell_volts': fields['H'],
            'voltage_balance_mv': fields['B'],
            'pack_temp_celsius': fields['PT'],
            'bms_temp_celsius': fields['BT'],
            'pack_voltage_volts': fields['PV'],
            'event_type': 'discharge_low'
        }

        # Generate legacy conditions string for backward compatibility
        legacy_conditions = (
            '{AH:03.0f} AH, SOC:{SOC:3d}%,         L:{L:4.2f}V,         H:{H:4.2f}V, B:{B:03d}mV, '
            'PT:{PT:03d}C, BT:{BT:03d}C, PV:{PV:6.1f}V').format_map(fields)

        return {
            'event': 'Discharged To Low',
            'structured_data': structured_data,  # NEW: Direct structured data
            'conditions': legacy_conditions  # LEGACY: For backward compatibility
        }

    @classmethod
    def bms_system_state(cls, x):
        return {
            'event': 'System Turned ' + convert_bit_to_on_off(BinaryTools.unpack('bool', x, 0x0))
        }

    @classmethod
    def bms_soc_adj_voltage(cls, x):
        # Extract binary data once
        old_uah = BinaryTools.unpack('uint32', x, 0x00)
        old_soc = BinaryTools.unpack('uint8', x, 0x04)
        new_uah = BinaryTools.unpack('uint32', x, 0x05)
        new_soc = BinaryTools.unpack('uint8', x, 0x09)
        low_cell_mv = BinaryTools.unpack('uint16', x, 0x0a)

        # Build structured data
        structured_data = {
            'old_capacity_microamp_hours': old_uah,
            'old_state_of_charge_percent': old_soc,
            'new_capacity_microamp_hours': new_uah,
            'new_state_of_charge_percent': new_soc,
            'low_cell_voltage_millivolts': low_cell_mv,
            'capacity_change_microamp_hours': new_uah - old_uah,
            'soc_change_percent': new_soc - old_soc
        }

        # Generate legacy conditions string for backward compatibility
        legacy_conditions = ('old:   {old}uAH (soc:{old_soc}%), '
                           'new:   {new}uAH (soc:{new_soc}%), '
                           'low cell: {low} mV').format(
                              old=old_uah, old_soc=old_soc,
                              new=new_uah, new_soc=new_soc,
                              low=low_cell_mv)

        return {
            'event': 'SOC adjusted for voltage',
            'structured_data': structured_data,
            'conditions': legacy_conditions
        }

    @classmethod
    def bms_curr_sens_zero(cls, x):
        # Extract binary data once
        old_mv = BinaryTools.unpack('uint16', x, 0x00)
        new_mv = BinaryTools.unpack('uint16', x, 0x02)
        corrfact = BinaryTools.unpack('uint8', x, 0x04)

        # Build structured data
        structured_data = {
            'old_value_millivolts': old_mv,
            'new_value_millivolts': new_mv,
            'correction_factor': corrfact,
            'old_value_volts': old_mv / 1000.0,
            'new_value_volts': new_mv / 1000.0,
            'adjustment_millivolts': new_mv - old_mv
        }

        # Generate legacy conditions string for backward compatibility
        legacy_conditions = 'old: {old}mV, new: {new}mV, corrfact: {corrfact}'.format(
            old=old_mv, new=new_mv, corrfact=corrfact
        )

        return {
            'event': 'Current Sensor Zeroed',
            'structured_data': structured_data,
            'conditions': legacy_conditions
        }

    @classmethod
    def bms_state(cls, x):
        entering_hibernate = BinaryTools.unpack('bool', x, 0x0)
        return {
            'event': ('Entering' if entering_hibernate else 'Exiting') + ' Hibernate'
        }

    @classmethod
    def bms_isolation_fault(cls, x):
        # Extract binary data once
        resistance_ohms = BinaryTools.unpack('uint32', x, 0x00)
        cell_number = BinaryTools.unpack('uint8', x, 0x04)

        # Build structured data
        structured_data = {
            'resistance_ohms': resistance_ohms,
            'cell_number': cell_number,
            'resistance_kiloohms': resistance_ohms / 1000.0,
            'resistance_megaohms': resistance_ohms / 1000000.0,
            'is_low_resistance': resistance_ohms < 1000000,  # Less than 1 megaohm is concerning
            'fault_severity': 'critical' if resistance_ohms < 100000 else 'warning' if resistance_ohms < 1000000 else 'info'
        }

        # Generate legacy conditions string for backward compatibility
        legacy_conditions = '{ohms} ohms to cell {cell}'.format(
            ohms=resistance_ohms, cell=cell_number
        )

        return {
            'event': 'Chassis Isolation Fault',
            'structured_data': structured_data,
            'conditions': legacy_conditions
        }

    @classmethod
    def bms_reflash(cls, x):
        # Extract binary data once
        revision = BinaryTools.unpack('uint8', x, 0x00)
        build_str = BinaryTools.unpack_str(x, 0x01, 20)

        # Build structured data
        structured_data = {
            'revision': revision,
            'build_string': build_str,
            'revision_hex': f'0x{revision:02X}',
            'is_printable_build': all(c.isprintable() for c in build_str),
            'build_length': len(build_str.rstrip('\x00'))
        }

        # Generate legacy conditions string for backward compatibility
        legacy_conditions = 'Revision {rev}, Built {build}'.format(
            rev=revision, build=build_str
        )

        return {
            'event': 'BMS Reflash',
            'structured_data': structured_data,
            'conditions': legacy_conditions
        }

    @classmethod
    def bms_change_can_id(cls, x):
        return {
            'event': 'Changed CAN Node ID',
            'conditions': 'old: {old:02d}, new: {new:02d}'.format(
                old=BinaryTools.unpack('uint8', x, 0x00),
                new=BinaryTools.unpack('uint8', x, 0x01))
        }

    @classmethod
    def bms_contactor_state(cls, x):
        # Extract raw binary data once into structured format
        is_closed = BinaryTools.unpack('bool', x, 0x0)
        pack_voltage = BinaryTools.unpack('uint32', x, 0x01)
        switched_voltage = BinaryTools.unpack('uint32', x, 0x05)
        discharge_current = BinaryTools.unpack('int32', x, 0x09)

        # Convert to display units
        pack_voltage_v = pack_voltage / 1000.0
        switched_voltage_v = switched_voltage / 1000.0
        discharge_current_a = discharge_current / 1000.0
        precharge_percent = convert_ratio_to_percent(switched_voltage, pack_voltage)

        # Build structured data dictionary
        structured_data = {
            'contactor_state': 'closed' if is_closed else 'opened',
            'pack_voltage_volts': pack_voltage_v,
            'switched_voltage_volts': switched_voltage_v,
            'precharge_percent': precharge_percent,
            'discharge_current_amps': discharge_current_a,
            'pack_voltage_mv': pack_voltage,
            'switched_voltage_mv': switched_voltage,
            'discharge_current_ma': discharge_current
        }

        # Generate legacy conditions string for backward compatibility
        legacy_conditions = (
            'Pack V: {pv:6.1f}V, '
            'Switched V: {sv:6.1f}V, '
            'Prechg Pct: {pc:2.0f}%, '
            'Dischg Cur: {dc:4.0f}A').format(
                pv=pack_voltage_v,
                sv=switched_voltage_v,
                pc=precharge_percent,
                dc=discharge_current_a)

        return {
            'event': 'Contactor was ' + ('Closed' if is_closed else 'Opened'),
            'structured_data': structured_data,  # NEW: Direct structured data
            'conditions': legacy_conditions  # LEGACY: For backward compatibility
        }

    @classmethod
    def bms_discharge_cut(cls, x):
        # Extract binary data once
        cut_byte = BinaryTools.unpack('uint8', x, 0x00)
        cut_pct = round((cut_byte / 255) * 100)  # JS parser formula

        # Build structured data
        structured_data = {
            'cutback_percent': cut_pct,
            'cutback_raw_value': cut_byte,
            'cutback_ratio': cut_byte / 255.0
        }

        # Generate legacy conditions string for backward compatibility
        legacy_conditions = '{cut:2d}%'.format(cut=cut_pct)

        return {
            'event': 'Discharge Cutback',
            'structured_data': structured_data,
            'conditions': legacy_conditions
        }

    @classmethod
    def bms_contactor_drive(cls, x):
        # Extract binary data once
        pack_voltage_raw = BinaryTools.unpack('uint32', x, 0x01)
        switched_voltage_raw = BinaryTools.unpack('uint32', x, 0x05)
        duty_cycle = BinaryTools.unpack('uint8', x, 0x09)

        # Convert millivolts to volts for display
        pack_voltage_v = pack_voltage_raw / 1000.0
        switched_voltage_v = switched_voltage_raw / 1000.0

        # Build structured data
        structured_data = {
            'pack_voltage_volts': pack_voltage_v,
            'switched_voltage_volts': switched_voltage_v,
            'duty_cycle_percent': duty_cycle,
            'pack_voltage_millivolts': pack_voltage_raw,
            'switched_voltage_millivolts': switched_voltage_raw,
            'voltage_difference_volts': abs(pack_voltage_v - switched_voltage_v)
        }

        # Generate legacy conditions string for backward compatibility
        legacy_conditions = 'Pack V: {pv:6.1f}V, Switched V: {sv:6.1f}V, Duty Cycle: {dc}%'.format(
            pv=pack_voltage_v,
            sv=switched_voltage_v,
            dc=duty_cycle
        )

        return {
            'event': 'Contactor drive turned on',
            'structured_data': structured_data,
            'conditions': legacy_conditions
        }

    @classmethod
    def debug_message(cls, x):
        # Extract the debug message string
        message = BinaryTools.unpack_str(x, 0x0, count=len(x) - 1)

        log_level = None

        # Check if this is a SOC data message and optimize it directly
        if message.startswith('SOC:'):
            log_level = 'STATE'
            soc_data = message[4:]  # Remove 'SOC:' prefix
            if ',' in soc_data:
                values = [v.strip() for v in soc_data.split(',')]
                if len(values) >= 11:  # Extended format (11+ values)
                    # Parse numeric values safely
                    def safe_int(val):
                        try:
                            return int(val)
                        except ValueError:
                            return val

                    pack_voltage_mv = safe_int(values[3]) if len(values) > 3 else 0
                    voltage_max_mv = safe_int(values[8]) if len(values) > 8 else 0
                    voltage_min_1_mv = safe_int(values[9]) if len(values) > 9 else 0
                    voltage_min_2_mv = safe_int(values[10]) if len(values) > 10 else 0

                    # Build structured data dictionary directly
                    structured_data = {
                        'soc_raw_1': safe_int(values[0]) if len(values) > 0 else 0,
                        'soc_raw_2': safe_int(values[1]) if len(values) > 1 else 0,
                        'soc_raw_3': safe_int(values[2]) if len(values) > 2 else 0,
                        'pack_voltage_volts': round(pack_voltage_mv / 1000.0, 3) if isinstance(pack_voltage_mv, int) else pack_voltage_mv,
                        'pack_voltage_mv': pack_voltage_mv,  # Deprecated, use pack_voltage_volts
                        'soc_percent_1': safe_int(values[4]) if len(values) > 4 else 0,
                        'soc_percent_2': safe_int(values[5]) if len(values) > 5 else 0,
                        'soc_percent_3': safe_int(values[6]) if len(values) > 6 else 0,
                        'balance_count': safe_int(values[7]) if len(values) > 7 else 0,
                        'voltage_max_volts': round(voltage_max_mv / 1000.0, 3) if isinstance(voltage_max_mv, int) else voltage_max_mv,
                        'voltage_min_1_volts': round(voltage_min_1_mv / 1000.0, 3) if isinstance(voltage_min_1_mv, int) else voltage_min_1_mv,
                        'voltage_min_2_volts': round(voltage_min_2_mv / 1000.0, 3) if isinstance(voltage_min_2_mv, int) else voltage_min_2_mv,
                        'voltage_max': voltage_max_mv,  # Deprecated, use voltage_max_volts
                        'voltage_min_1': voltage_min_1_mv,  # Deprecated, use voltage_min_1_volts
                        'voltage_min_2': voltage_min_2_mv,  # Deprecated, use voltage_min_2_volts
                        'current_ma': safe_int(values[11]) if len(values) > 11 else None,
                        'current_amps': round(safe_int(values[11]) / 1000.0, 3) if len(values) > 11 and isinstance(safe_int(values[11]), int) else None
                    }

                    # Generate legacy conditions string for backward compatibility
                    legacy_conditions = message  # Keep original debug message

                    return {
                        'event': 'SOC Data',
                        'log_level': log_level,
                        'structured_data': structured_data,  # NEW: Direct structured data
                        'conditions': legacy_conditions  # LEGACY: For backward compatibility
                    }
                elif len(values) == 8:  # Compact format (8 values)
                    # Handle compact format similarly
                    def safe_int(val):
                        try:
                            return int(val)
                        except ValueError:
                            return val

                    pack_voltage_mv = safe_int(values[3])
                    structured_data = {
                        'soc_raw_1': safe_int(values[0]),
                        'soc_raw_2': safe_int(values[1]),
                        'soc_raw_3': safe_int(values[2]),
                        'pack_voltage_mv': pack_voltage_mv,
                        'pack_voltage_volts': pack_voltage_mv / 1000.0 if isinstance(pack_voltage_mv, int) else pack_voltage_mv,
                        'soc_percent_1': safe_int(values[4]),
                        'soc_percent_2': safe_int(values[5]),
                        'soc_percent_3': safe_int(values[6]),
                        'balance_or_current': safe_int(values[7])  # Could be balance count or current
                    }

                    return {
                        'event': 'SOC Data',
                        'log_level': log_level,
                        'structured_data': structured_data,  # NEW: Direct structured data
                        'conditions': message  # LEGACY: For backward compatibility
                    }

        if message.startswith('DEBUG:'):
            message = message[7:].strip()
            log_level = 'DEBUG'
        elif message.startswith('INFO:'):
            message = message[6:].strip()
            log_level = 'INFO'
        elif message.startswith('ERROR:'):
            message = message[7:].strip()
            log_level = 'ERROR'
        elif message.startswith('WARNING:'):
            message = message[9:].strip()
            log_level = 'WARNING'

        # Check if this is a charger debug message and optimize it directly
        if message.startswith('Charger ') and 'SN:' in message and 'SW:' in message:
            import re

            # Pattern to match various charger message formats:
            # "Charger 6 Stopped SN:2329104 SW:209 236Vac  50Hz EVSE  8A"
            # "Charger 6 Charging SN:2329104 SW:209 237Vac 50Hz EVSE 16A"
            # "Charger 6 Charging SN:2329104 SW:209 237Vac  50Hz EVSE 16A" (extra spaces)
            charger_pattern = r'Charger\s+(\d+)\s+(\w+)\s+SN:(\d+)\s+SW:(\d+)\s+(\d+)Vac\s+(\d+)Hz\s+EVSE\s+(\d+)A'
            match = re.search(charger_pattern, message)

            if match:
                charger_num, status, serial_num, sw_version, voltage_ac, frequency, evse_current = match.groups()

                # Build structured data dictionary
                structured_data = {
                    'charger_number': int(charger_num),
                    'status': status,
                    'serial_number': serial_num,
                    'software_version': int(sw_version),
                    'voltage_ac': int(voltage_ac),
                    'frequency_hz': int(frequency),
                    'evse_current_amps': int(evse_current)
                }

                return {
                    'event': 'Charger Status',
                    'log_level': log_level,
                    'structured_data': structured_data,  # NEW: Direct structured data
                    'conditions': message  # LEGACY: For backward compatibility
                }
            else:
                # If pattern doesn't match exactly, still try to extract some basic charger info
                basic_charger_pattern = r'Charger (\d+) (\w+)'
                basic_match = re.search(basic_charger_pattern, message)
                if basic_match:
                    charger_num, status = basic_match.groups()
                    return {
                        'event': 'Charger Status',
                        'log_level': log_level,
                        'structured_data': {
                            'charger_number': int(charger_num),
                            'status': status,
                            'raw_message': message  # Include full message for unknown formats
                        },
                        'conditions': message
                    }

        # For other debug messages, return as normal
        return {
            'event': message,
            'log_level': log_level
        }

    @classmethod
    def board_status(cls, x):
        causes = {
            0x04: 'Software',
        }

        return {
            'event': 'BMS Reset',
            'conditions': causes.get(BinaryTools.unpack('uint8', x, 0x00),
                                     'Unknown')
        }

    @classmethod
    def key_state(cls, x):
        # Extract binary data once
        key_on = BinaryTools.unpack('bool', x, 0x0)
        key_state_text = convert_bit_to_on_off(key_on)

        # Build structured data
        structured_data = {
            'key_on': key_on,
            'key_state': key_state_text,
            'is_key_on': key_on,
            'is_key_off': not key_on
        }

        # Generate legacy event string for backward compatibility
        event_name = 'Key ' + key_state_text + (' ' if key_on else '')

        return {
            'event': event_name,
            'structured_data': structured_data
        }

    @classmethod
    def battery_can_link_up(cls, x):
        return {
            'event': 'Module {module:02} CAN Link Up'.format(
                module=BinaryTools.unpack('uint8', x, 0x0)
            )
        }

    @classmethod
    def battery_can_link_down(cls, x):
        return {
            'event': 'Module {module:02} CAN Link Down'.format(
                module=BinaryTools.unpack('uint8', x, 0x0)
            )
        }

    @classmethod
    def sevcon_can_link_up(cls, _):
        return {
            'event': 'Sevcon CAN Link Up'
        }

    @classmethod
    def sevcon_can_link_down(cls, x):
        return {
            'event': 'Sevcon CAN Link Down'
        }

    @classmethod
    def run_status(cls, x):
        mod_translate = {
            0x00: '00',
            0x01: '10',
            0x02: '01',
            0x03: '11',
        }

        # Extract raw binary data once into structured format
        pack_temp_hi = BinaryTools.unpack('uint8', x, 0x0)
        pack_temp_low = BinaryTools.unpack('uint8', x, 0x1)
        soc = BinaryTools.unpack('uint16', x, 0x2)
        pack_voltage = convert_mv_to_v(BinaryTools.unpack('uint32', x, 0x4))
        motor_temp = BinaryTools.unpack('int16', x, 0x8)
        controller_temp = BinaryTools.unpack('int16', x, 0xa)
        rpm = BinaryTools.unpack('uint16', x, 0xc)
        battery_current = BinaryTools.unpack('int16', x, 0x10)
        mods_raw = BinaryTools.unpack('uint8', x, 0x12)
        mods = mod_translate.get(mods_raw, 'Unknown')
        motor_current = BinaryTools.unpack('int16', x, 0x13)
        ambient_temp = BinaryTools.unpack('int16', x, 0x15)
        odometer = BinaryTools.unpack('uint32', x, 0x17)

        # Build structured data dictionary
        structured_data = {
            'pack_temp_high_celsius': pack_temp_hi,
            'pack_temp_low_celsius': pack_temp_low,
            'state_of_charge_percent': soc,
            'pack_voltage_volts': pack_voltage,
            'motor_temp_celsius': motor_temp,
            'controller_temp_celsius': controller_temp,
            'motor_rpm': rpm,
            'battery_current_amps': battery_current,
            'motor_current_amps': motor_current,
            'ambient_temp_celsius': ambient_temp,
            'odometer_km': odometer,
            'mods': mods,
            'mods_raw': mods_raw
        }

        # Generate legacy conditions string for backward compatibility
        legacy_conditions = (
            'PackTemp: h {pack_temp_hi}C, l {pack_temp_low}C, '
            'PackSOC:{soc:3d}%, '
            'Vpack:{pack_voltage:7.3f}V, '
            'MotAmps:{motor_current:4d}, BattAmps:{battery_current:4d}, '
            'Mods: {mods}, '
            'MotTemp:{motor_temp:4d}C, CtrlTemp:{controller_temp:4d}C, '
            'AmbTemp:{ambient_temp:4d}C, '
            'MotRPM:{rpm:4d}, '
            'Odo:{odometer:5d}km').format(
                pack_temp_hi=pack_temp_hi,
                pack_temp_low=pack_temp_low,
                soc=soc,
                pack_voltage=pack_voltage,
                motor_temp=motor_temp,
                controller_temp=controller_temp,
                rpm=rpm,
                battery_current=battery_current,
                mods=mods,
                motor_current=motor_current,
                ambient_temp=ambient_temp,
                odometer=odometer)

        return {
            'event': 'Riding',
            'structured_data': structured_data,  # NEW: Direct structured data
            'conditions': legacy_conditions  # LEGACY: For backward compatibility
        }

    @classmethod
    def charging_status(cls, x):
        # Extract raw binary data once into structured format
        pack_temp_hi = BinaryTools.unpack('uint8', x, 0x00)
        pack_temp_low = BinaryTools.unpack('uint8', x, 0x01)
        soc = BinaryTools.unpack('uint16', x, 0x02)
        pack_voltage = convert_mv_to_v(BinaryTools.unpack('uint32', x, 0x4))
        battery_current = BinaryTools.unpack('int8', x, 0x08)
        mods = BinaryTools.unpack('uint8', x, 0x0c)
        ambient_temp = BinaryTools.unpack('int8', x, 0x0d)

        # Build structured data dictionary
        structured_data = {
            'pack_temp_high_celsius': pack_temp_hi,
            'pack_temp_low_celsius': pack_temp_low,
            'ambient_temp_celsius': ambient_temp,
            'state_of_charge_percent': soc,
            'pack_voltage_volts': pack_voltage,
            'battery_current_amps': battery_current,
            'mods': mods,
            'mbb_charge_enabled': True,  # Always true based on message format
            'bms_charge_enabled': False  # Always false based on message format
        }

        # Generate legacy conditions string for backward compatibility
        legacy_conditions = (
            'PackTemp: h {pack_temp_hi}C, l {pack_temp_low}C, AmbTemp: {ambient_temp}C, '
            'PackSOC:{soc:3d}%, Vpack:{pack_voltage:7.3f}V, BattAmps: {battery_current:3d}, '
            'Mods: {mods:02b}, MbbChgEn: Yes, BmsChgEn: No').format(
                pack_temp_hi=pack_temp_hi,
                pack_temp_low=pack_temp_low,
                ambient_temp=ambient_temp,
                soc=soc,
                pack_voltage=pack_voltage,
                battery_current=battery_current,
                mods=mods)

        return {
            'event': 'Charging',
            'structured_data': structured_data,  # NEW: Direct structured data
            'conditions': legacy_conditions  # LEGACY: For backward compatibility
        }

    @classmethod
    def sevcon_status(cls, x):
        cause = {
            0x4681: 'Preop',
            0x4884: 'Sequence Fault',
            0x4981: 'Throttle Fault',
        }

        # Extract binary data once
        error_code = BinaryTools.unpack('uint16', x, 0x00)
        sevcon_code = BinaryTools.unpack('uint16', x, 0x02)
        error_reg = BinaryTools.unpack('uint8', x, 0x04)
        additional_data = x[5:] if len(x) > 5 else []

        # Build structured data
        structured_data = {
            'error_code': error_code,
            'error_code_hex': f'0x{error_code:04X}',
            'sevcon_error_code': sevcon_code,
            'sevcon_error_code_hex': f'0x{sevcon_code:04X}',
            'error_register': error_reg,
            'error_register_hex': f'0x{error_reg:02X}',
            'additional_data_hex': [f'{c:02X}' for c in additional_data],
            'additional_data_raw': list(additional_data),
            'cause': cause.get(sevcon_code, 'Unknown'),
            'is_known_error': sevcon_code in cause
        }

        # Generate legacy conditions string for backward compatibility
        legacy_conditions = ('Error Code: 0x{code:04X}, Error Reg: 0x{reg:02X}, '
                           'Sevcon Error Code: 0x{sevcon_code:04X}, Data: {data}, {cause}').format(
                              code=error_code,
                              reg=error_reg,
                              sevcon_code=sevcon_code,
                              data=' '.join([f'{c:02X}' for c in additional_data]),
                              cause=cause.get(sevcon_code, 'Unknown')
                          )

        return {
            'event': 'SEVCON CAN EMCY Frame',
            'structured_data': structured_data,
            'conditions': legacy_conditions
        }

    @classmethod
    def charger_status(cls, x):
        states = {
            0x00: 'Disconnected',
            0x01: 'Connected',
        }

        name = {
            0x00: 'Calex 720W',
            0x01: 'Calex 1200W',
            0x02: 'External Chg 0',
            0x03: 'External Chg 1',
        }

        # Extract binary data once
        charger_state_code = BinaryTools.unpack('uint8', x, 0x1)
        charger_id = BinaryTools.unpack('uint8', x, 0x0)
        state_name = states.get(charger_state_code, 'Unknown')
        charger_name = name.get(charger_id, 'Unknown')

        # Build structured data
        structured_data = {
            'charger_id': charger_id,
            'charger_name': charger_name,
            'state_code': charger_state_code,
            'state': state_name,
            'is_connected': charger_state_code == 0x01,
            'is_calex_720w': charger_id == 0x00,
            'is_calex_1200w': charger_id == 0x01,
            'is_external_charger': charger_id in [0x02, 0x03],
            'is_known_charger': charger_id in name
        }

        # Generate legacy event string for backward compatibility
        event_name = '{name} Charger {charger_id} {state:13s}'.format(
            charger_id=charger_id,
            state=state_name,
            name=charger_name
        )

        return {
            'event': event_name,
            'structured_data': structured_data
        }

    @classmethod
    def battery_status(cls, x):
        opening_contactor = 'Opening Contractor'
        closing_contactor = 'Closing Contractor'
        registered = 'Registered'
        events = {
            0x00: opening_contactor,
            0x01: closing_contactor,
            0x02: registered,
        }

        # Extract binary data once
        event = BinaryTools.unpack('uint8', x, 0x0)
        module_num = BinaryTools.unpack('uint8', x, 0x1)
        mod_volt = convert_mv_to_v(BinaryTools.unpack('uint32', x, 0x2))
        sys_max = convert_mv_to_v(BinaryTools.unpack('uint32', x, 0x6))
        sys_min = convert_mv_to_v(BinaryTools.unpack('uint32', x, 0xa))
        capacitor_volt = convert_mv_to_v(BinaryTools.unpack('uint32', x, 0x0e))
        battery_current = BinaryTools.unpack('int16', x, 0x12)
        serial_no = BinaryTools.unpack_str(x, 0x14, count=len(x[0x14:]))

        # Ensure the serial is printable
        printable_serial_no = ''.join(c for c in serial_no
                                      if c not in string.printable)
        if not printable_serial_no:
            printable_serial_no = hex_of_value(serial_no)

        event_name = events.get(event, 'Unknown (0x{:02x})'.format(event))

        # Build structured data
        structured_data = {
            'module_number': module_num,
            'event_type': event_name,
            'event_code': event,
            'module_voltage_volts': mod_volt,
            'system_max_voltage_volts': sys_max,
            'system_min_voltage_volts': sys_min,
            'voltage_difference_volts': sys_max - sys_min,
            'capacitor_voltage_volts': capacitor_volt,
            'battery_current_amps': battery_current,
            'serial_number': printable_serial_no,
            'precharge_percent': convert_ratio_to_percent(capacitor_volt, mod_volt) if event == 1 else None
        }

        # Generate legacy conditions string for backward compatibility
        if event_name == opening_contactor:
            conditions_msg = 'vmod: {modvolt:7.3f}V, batt curr: {batcurr:3.0f}A'.format(
                modvolt=mod_volt,
                batcurr=battery_current
            )
        elif event_name == closing_contactor:
            conditions_msg = ('vmod: {modvolt:7.3f}V, maxsys: {sysmax:7.3f}V, '
                              'minsys: {sysmin:7.3f}V, diff: {diff:0.03f}V, vcap: {vcap:6.3f}V, '
                              'prechg: {prechg:2.0f}%').format(
                modvolt=mod_volt,
                sysmax=sys_max,
                sysmin=sys_min,
                vcap=capacitor_volt,
                batcurr=battery_current,
                serial=printable_serial_no,
                diff=sys_max - sys_min,
                prechg=convert_ratio_to_percent(capacitor_volt, mod_volt))
        elif event_name == registered:
            conditions_msg = 'serial: {serial},  vmod: {modvolt:3.3f}V'.format(
                serial=printable_serial_no,
                modvolt=mod_volt
            )
        else:
            conditions_msg = ''

        return {
            'event': 'Module {module:02} {event}'.format(
                module=module_num,
                event=event_name
            ),
            'structured_data': structured_data,
            'conditions': conditions_msg
        }

    @classmethod
    def power_state(cls, x):
        sources = {
            0x01: 'Key Switch',
            0x02: 'Ext Charger 0',
            0x03: 'Ext Charger 1',
            0x04: 'Onboard Charger',
        }

        # Extract binary data once
        power_on_cause_code = BinaryTools.unpack('uint8', x, 0x1)
        power_on = BinaryTools.unpack('bool', x, 0x0)
        power_state_text = convert_bit_to_on_off(power_on)
        source_name = sources.get(power_on_cause_code, 'Unknown')

        # Build structured data
        structured_data = {
            'power_on': power_on,
            'power_state': power_state_text,
            'power_source_code': power_on_cause_code,
            'power_source': source_name,
            'is_key_switch': power_on_cause_code == 0x01,
            'is_external_charger': power_on_cause_code in [0x02, 0x03],
            'is_onboard_charger': power_on_cause_code == 0x04,
            'is_known_source': power_on_cause_code in sources
        }

        # Generate legacy conditions string for backward compatibility
        legacy_conditions = source_name

        return {
            'event': 'Power ' + power_state_text,
            'structured_data': structured_data,
            'conditions': legacy_conditions
        }

    @classmethod
    def sevcon_power_state(cls, x):
        # Extract binary data once
        power_on = BinaryTools.unpack('bool', x, 0x0)
        power_state_text = convert_bit_to_on_off(power_on)

        # Build structured data
        structured_data = {
            'sevcon_power_on': power_on,
            'power_state': power_state_text,
            'is_powered': power_on,
            'controller_type': 'sevcon'
        }

        return {
            'event': 'Sevcon Turned ' + power_state_text,
            'structured_data': structured_data,
            'conditions': None  # No legacy conditions needed for this simple event
        }

    @classmethod
    def show_bluetooth_state(cls, x):
        return {
            'event': 'BT RX buffer reset'
        }

    @classmethod
    def battery_discharge_current_limited(cls, x):
        # Extract binary data once
        limit = BinaryTools.unpack('uint16', x, 0x00)
        min_cell_mv = BinaryTools.unpack('uint16', x, 0x02)
        temp = BinaryTools.unpack('uint8', x, 0x04)
        max_amp = BinaryTools.unpack('uint16', x, 0x05)

        # Build structured data
        structured_data = {
            'limit_current_amps': limit,
            'max_current_amps': max_amp,
            'limit_percentage': convert_ratio_to_percent(limit, max_amp),
            'min_cell_voltage_mv': min_cell_mv,
            'min_cell_voltage_volts': round(min_cell_mv / 1000.0, 3),
            'max_pack_temp_celsius': temp,
            'is_current_limited': True,
            'current_reduction_amps': max_amp - limit
        }

        # Generate legacy conditions string for backward compatibility
        legacy_conditions = '{limit} A ({percent:.2f}%), MinCell: {min_cell}mV, MaxPackTemp: {temp}C'.format(
            limit=limit,
            min_cell=min_cell_mv,
            temp=temp,
            max_amp=max_amp,
            percent=convert_ratio_to_percent(limit, max_amp)
        )

        return {
            'event': 'Batt Dischg Cur Limited',
            'structured_data': structured_data,
            'conditions': legacy_conditions
        }

    @classmethod
    def low_chassis_isolation(cls, x):
        # Extract binary data once
        resistance_kohms = BinaryTools.unpack('uint32', x, 0x00)
        cell_number = BinaryTools.unpack('uint8', x, 0x04)

        # Build structured data
        structured_data = {
            'resistance_kohms': resistance_kohms,
            'resistance_ohms': resistance_kohms * 1000,
            'resistance_mohms': resistance_kohms * 1000000,
            'affected_cell_number': cell_number,
            'isolation_fault': True,
            'is_critical': resistance_kohms < 500  # Typical critical threshold
        }

        # Generate legacy conditions string for backward compatibility
        legacy_conditions = '{kohms} KOhms to cell {cell}'.format(
            kohms=resistance_kohms,
            cell=cell_number
        )

        return {
            'event': 'Low Chassis Isolation',
            'structured_data': structured_data,
            'conditions': legacy_conditions
        }

    @classmethod
    def precharge_decay_too_steep(cls, x):
        return {
            'event': 'Precharge Decay Too Steep. Restarting Sevcon.'
        }

    @classmethod
    def disarmed_status(cls, x):
        # Extract raw binary data once into structured format
        pack_temp_hi = BinaryTools.unpack('uint8', x, 0x0)
        pack_temp_low = BinaryTools.unpack('uint8', x, 0x1)
        soc = BinaryTools.unpack('uint16', x, 0x2)
        pack_voltage = convert_mv_to_v(BinaryTools.unpack('uint32', x, 0x4))
        motor_temp = BinaryTools.unpack('int16', x, 0x8)
        controller_temp = BinaryTools.unpack('int16', x, 0xa)
        rpm = BinaryTools.unpack('uint16', x, 0xc)
        battery_current = BinaryTools.unpack('uint8', x, 0x10)
        mods = BinaryTools.unpack('uint8', x, 0x12)
        motor_current = BinaryTools.unpack('int8', x, 0x13)
        ambient_temp = BinaryTools.unpack('int16', x, 0x15)
        odometer = BinaryTools.unpack('uint32', x, 0x17)

        # Build structured data dictionary
        structured_data = {
            'pack_temp_high_celsius': pack_temp_hi,
            'pack_temp_low_celsius': pack_temp_low,
            'state_of_charge_percent': soc,
            'pack_voltage_volts': pack_voltage,
            'motor_temp_celsius': motor_temp,
            'controller_temp_celsius': controller_temp,
            'motor_rpm': rpm,
            'battery_current_amps': battery_current,
            'motor_current_amps': motor_current,
            'ambient_temp_celsius': ambient_temp,
            'odometer_km': odometer,
            'modules_status': mods,
            'vehicle_state': 'disarmed'
        }

        # Generate legacy conditions string for backward compatibility
        legacy_conditions = (
            'PackTemp: h {pack_temp_hi}C, l {pack_temp_low}C, '
            'PackSOC:{soc:3d}%, '
            'Vpack:{pack_voltage:03.3f}V, '
            'MotAmps:{motor_current:4d}, BattAmps:{battery_current:4d}, '
            'Mods: {mods:02b}, '
            'MotTemp:{motor_temp:4d}C, CtrlTemp:{controller_temp:4d}C, '
            'AmbTemp:{ambient_temp:4d}C, '
            'MotRPM:{rpm:4d}, '
            'Odo:{odometer:5d}km').format(
                pack_temp_hi=pack_temp_hi,
                pack_temp_low=pack_temp_low,
                soc=soc,
                pack_voltage=pack_voltage,
                motor_temp=motor_temp,
                controller_temp=controller_temp,
                rpm=rpm,
                battery_current=battery_current,
                mods=mods,
                motor_current=motor_current,
                ambient_temp=ambient_temp,
                odometer=odometer)

        return {
            'event': 'Disarmed',
            'structured_data': structured_data,  # NEW: Direct structured data
            'conditions': legacy_conditions  # LEGACY: For backward compatibility
        }

    @classmethod
    def battery_contactor_closed(cls, x):
        # Extract binary data once
        module_number = BinaryTools.unpack('uint8', x, 0x0)

        # Build structured data
        structured_data = {
            'module_number': module_number,
            'contactor_state': 'closed',
            'contactor_closed': True,
            'event_type': 'contactor_closure',
            'module_id': f'module_{module_number:02d}'
        }

        return {
            'event': 'Battery module {module:02} contactor closed'.format(module=module_number),
            'structured_data': structured_data,
            'conditions': None  # No legacy conditions needed for this simple event
        }

    @classmethod
    def vehicle_state_telemetry(cls, x):
        """Parse Type 81 (0x51) - Vehicle State Telemetry (68 bytes)

        Optimized Gen2 parser - generates structured data directly from binary.
        Returns ProcessedLogEntry with both human-readable conditions and structured JSON data.
        """
        if len(x) < 68:
            return cls.unhandled_entry_format(0x51, x)

        # Extract vehicle state string (bytes 36-39)
        state_bytes = x[36:40]
        state = state_bytes.rstrip(b'\x00').decode('ascii', errors='ignore')

        # Decode key telemetry values using BinaryTools.unpack()
        odometer_m = BinaryTools.unpack('uint32', x, 0)      # Distance in meters
        soc_raw = BinaryTools.unpack('uint32', x, 4)         # State of charge raw
        ambient_temp_raw = BinaryTools.unpack('uint32', x, 8) # Ambient temperature raw

        # Temperature values are at bytes 48-63 as single bytes
        temp1 = BinaryTools.unpack('uint8', x, 48) if len(x) > 48 else 0  # Temperature 1 (°C)
        temp2 = BinaryTools.unpack('uint8', x, 49) if len(x) > 49 else 0  # Temperature 2 (°C)
        temp3 = BinaryTools.unpack('uint8', x, 50) if len(x) > 50 else 0  # Temperature 3 (°C)
        temp4 = BinaryTools.unpack('uint8', x, 51) if len(x) > 51 else 0  # Temperature 4 (°C)

        # Convert and calculate derived values
        odometer_km = odometer_m // 1000  # Convert meters to km
        soc_percent = max(0, min(100, int((soc_raw - 200) / 6.0)))  # Estimate SOC percentage
        ambient_temp_celsius = int(ambient_temp_raw / 1000) if ambient_temp_raw > 1000 else ambient_temp_raw

        # Create structured data
        structured_data = {
            'vehicle_state': state,
            'odometer_meters': odometer_m,
            'odometer_km': odometer_km,
            'soc_raw': soc_raw,
            'soc_percent': soc_percent,
            'ambient_temperature_raw': ambient_temp_raw,
            'ambient_temperature_celsius': ambient_temp_celsius,
            'temperature_1_celsius': temp1,
            'temperature_2_celsius': temp2,
            'temperature_3_celsius': temp3,
            'temperature_4_celsius': temp4
        }

        # Generate human-readable conditions with units
        conditions = (
            f"State: {state}, "
            f"PackSOC: {soc_percent}%, "
            f"Odo: {odometer_km}km, "
            f"AmbTemp: {ambient_temp_celsius}°C, "
            f"Temp1: {temp1}°C, Temp2: {temp2}°C, "
            f"Temp3: {temp3}°C, Temp4: {temp4}°C"
        )

        # Determine event name based on state - riding states show as "Riding" for plotting compatibility
        if state in ['RUN', 'IB', 'WSU', 'UN']:  # Active states that should show as "Riding"
            event_name = 'Riding'
        else:
            event_name = f'Vehicle State ({state})'

        return {
            'event': event_name,
            'structured_data': structured_data,  # NEW: Direct structured data
            'conditions': conditions  # Human-readable conditions with units
        }

    @classmethod
    def sensor_data(cls, x):
        """Parse Type 84 (0x54) - Sensor Data (22 bytes)

        Optimized Gen2 parser - generates structured data directly from binary.
        Returns structured sensor telemetry with both human-readable conditions and structured JSON data.
        """
        if len(x) < 22:
            return cls.unhandled_entry_format(0x54, x)

        # Extract binary data using BinaryTools.unpack()
        odometer_m = BinaryTools.unpack('uint32', x, 0)      # Distance in meters
        sensor1 = BinaryTools.unpack('uint32', x, 4)         # Sensor value 1
        sensor2 = BinaryTools.unpack('uint32', x, 8)         # Sensor value 2
        sensor3 = BinaryTools.unpack('uint32', x, 12)        # Sensor value 3
        sensor4 = BinaryTools.unpack('uint32', x, 16)        # Sensor value 4
        status = BinaryTools.unpack('uint16', x, 20)         # Status flags

        # Convert derived values
        odometer_km = odometer_m // 1000  # Convert meters to kilometers

        # Create structured data
        structured_data = {
            'odometer_meters': odometer_m,
            'odometer_km': odometer_km,
            'sensor_1_value': sensor1,
            'sensor_2_value': sensor2,
            'sensor_3_value': sensor3,
            'sensor_4_value': sensor4,
            'status_flags': status,
            'status_hex': f'0x{status:04x}'
        }

        # Generate human-readable conditions with units
        conditions = (
            f"Odometer: {odometer_km}km, "
            f"Sensor1: {sensor1}, Sensor2: {sensor2}, "
            f"Sensor3: {sensor3}, Sensor4: {sensor4}, "
            f"Status: 0x{status:04x}"
        )

        return {
            'event': 'Sensor Data',
            'structured_data': structured_data,  # NEW: Direct structured data
            'conditions': conditions  # Human-readable conditions with units
        }

    @classmethod
    def type_from_block(cls, unescaped_block):
        return BinaryTools.unpack('uint8', unescaped_block, 0x00)

    @classmethod
    def get_message_type_description(cls, message_type):
        """Get descriptive name for log entry message types based on log_structure.md"""
        descriptions = {
            0x00: "Board Status",
            0x01: "Board Status",
            0x02: "High Throttle Disable",
            0x03: "BMS Discharge Level",
            0x04: "BMS Charge Full",
            0x05: "BMS Unknown Type 5",
            0x06: "BMS Discharge Low",
            0x08: "BMS System State",
            0x09: "Key State",
            0x0b: "BMS SOC Adjusted for Voltage",
            0x0d: "BMS Current Sensor Zeroed",
            0x0e: "BMS Unknown Type 14",
            0x10: "BMS Hibernate State",
            0x11: "BMS Chassis Isolation Fault",
            0x12: "BMS Reflash",
            0x13: "BMS CAN Node ID Changed",
            0x15: "BMS Contactor State",
            0x16: "BMS Discharge Cutback",
            0x18: "BMS Contactor Drive",
            0x1c: "MBB Unknown Type 28",
            0x1e: "MBB Unknown Type 30",
            0x1f: "MBB Unknown Type 31",
            0x20: "MBB Unknown Type 32",
            0x26: "MBB Unknown Type 38",
            0x28: "Battery CAN Link Up",
            0x29: "Battery CAN Link Down",
            0x2a: "Sevcon CAN Link Up",
            0x2b: "Sevcon CAN Link Down",
            0x2c: "Riding Status",
            0x2d: "Charging Status",
            0x2f: "Sevcon Status",
            0x30: "Charger Status",
            0x31: "MBB BMS Isolation Fault",
            0x33: "Battery Module Status",
            0x34: "Power State",
            0x35: "MBB Unknown Type 53",
            0x36: "Sevcon Power State",
            0x37: "MBB BT RX Buffer Overflow",
            0x38: "Bluetooth State",
            0x39: "Battery Discharge Current Limited",
            0x3a: "Low Chassis Isolation",
            0x3b: "Precharge Decay Too Steep",
            0x3c: "Disarmed Status",
            0x3d: "Battery Module Contactor Closed",
            0x3e: "Cell Voltages",
            0x51: "Vehicle State Telemetry",  # Type 81 (0x51)
            0x52: "Unknown Type 82",          # Type 82 (0x52) - appears in new format
            0x54: "Sensor Data",              # Type 84 (0x54)
            0xfb: "System Information",       # Type 251 (0xfb)
            0xfd: "Debug String"
        }
        return descriptions.get(message_type, f"Unknown Type {message_type}")

    @classmethod
    def unhandled_entry_format(cls, message_type, x):
        description = cls.get_message_type_description(message_type)

        # For completely unknown/unhandled types, show raw data
        if not x or len(x) == 0:
            return {
                'event': description,
                'conditions': 'No additional data'
            }

        # Try to provide meaningful interpretation for common patterns
        conditions = f'Raw data: {display_bytes_hex(x)}'

        # Check for specific patterns that might be meaningful
        if len(x) == 1:
            byte_val = BinaryTools.unpack('uint8', x, 0x00)
            conditions += f' (decimal: {byte_val})'
        elif len(x) == 2:
            word_val = BinaryTools.unpack('uint16', x, 0x00)
            conditions += f' (decimal: {word_val}, 0x{word_val:04X})'
            # Check for specific known values
            if word_val == 0x550A:
                conditions += ' [Possible reference: 0x550A]'
            elif word_val == 0x553A:
                conditions += ' [Possible reference: 0x553A]'
        elif len(x) == 4:
            dword_val = BinaryTools.unpack('uint32', x, 0x00)
            conditions += f' (decimal: {dword_val}, 0x{dword_val:08X})'

        # Try to detect if it might be ASCII text
        try:
            ascii_text = x.decode('ascii').rstrip('\x00')
            if ascii_text.isprintable() and len(ascii_text) > 1:
                conditions += f' [ASCII: "{ascii_text}"]'
        except:
            pass

        return {
            'event': description,
            'conditions': conditions
        }

    @classmethod
    def parse_entry(cls, log_data, address, unhandled, logger, timezone_offset=None, verbosity_level=1):
        """
        Parse an individual entry from a LogFile into a human readable form
        """
        try:
            header = log_data[address]
        # IndexError: bytearray index out of range
        except IndexError:
            logger.warn("IndexError log_data[%r]: forcing header_bad", address)
            header = 0
        # correct header offset as needed to prevent errors
        header_bad = header != 0xb2
        while header_bad:
            address += 1
            try:
                header = log_data[address]
            except IndexError:
                # IndexError: bytearray index out of range
                logger.warn("IndexError log_data[%r]: forcing header_bad", address)
                header = 0
                header_bad = True
                break
            header_bad = header != 0xb2
        try:
            length = log_data[address + 1]
        # IndexError: bytearray index out of range
        except IndexError:
            length = 0

        unescaped_block = BinaryTools.unescape_block(log_data[address + 0x2:address + length])

        message_type = cls.type_from_block(unescaped_block)
        message = unescaped_block[0x05:]

        parsers = {
            # Unknown entry types to be added when defined: type, length, source, example
            0x01: cls.board_status,
            # 0x02: unknown, 2, 6350_MBB_2016-04-12, 0x02 0x2e 0x11 ???
            0x03: cls.bms_discharge_level,
            0x04: cls.bms_charge_full,
            0x05: BinaryTools.bms_unknown_type_5,
            0x06: cls.bms_discharge_low,
            0x08: cls.bms_system_state,
            0x09: cls.key_state,
            0x0b: cls.bms_soc_adj_voltage,
            0x0d: cls.bms_curr_sens_zero,
            0x0e: BinaryTools.bms_unknown_type_14,
            0x10: cls.bms_state,
            0x11: cls.bms_isolation_fault,
            0x12: cls.bms_reflash,
            0x13: cls.bms_change_can_id,
            0x15: cls.bms_contactor_state,
            0x16: cls.bms_discharge_cut,
            0x18: cls.bms_contactor_drive,
            0x1c: BinaryTools.mbb_unknown_type_28,
            # 0x1e: unknown, 4, 6472_MBB_2016-12-12, 0x1e 0x32 0x00 0x06 0x23 ???
            # 0x1f: unknown, 4, 5078_MBB_2017-01-20, 0x1f 0x00 0x00 0x08 0x43 ???
            # 0x20: unknown, 3, 6472_MBB_2016-12-12, 0x20 0x02 0x32 0x00 ???
            0x26: BinaryTools.mbb_unknown_type_38,
            0x28: cls.battery_can_link_up,
            0x29: cls.battery_can_link_down,
            0x2a: cls.sevcon_can_link_up,
            0x2b: cls.sevcon_can_link_down,
            0x2c: cls.run_status,
            0x2d: cls.charging_status,
            0x2f: cls.sevcon_status,
            0x30: cls.charger_status,
            # 0x31: unknown, 1, 6350_MBB_2016-04-12, 0x31 0x00 ???
            0x33: cls.battery_status,
            0x34: cls.power_state,
            # 0x35: unknown, 5, 6472_MBB_2016-12-12, 0x35 0x00 0x46 0x01 0xcb 0xff ???
            0x36: cls.sevcon_power_state,
            0x37: BinaryTools.mbb_bt_rx_buffer_overflow,
            # 0x37: unknown, 0, 3558_MBB_2016-12-25, 0x37  ???
            0x38: cls.show_bluetooth_state,
            0x39: cls.battery_discharge_current_limited,
            0x3a: cls.low_chassis_isolation,
            0x3b: cls.precharge_decay_too_steep,
            0x3c: cls.disarmed_status,
            0x3d: cls.battery_contactor_closed,
            0x51: cls.vehicle_state_telemetry,  # Type 81
            0x54: cls.sensor_data,              # Type 84
            0xfd: cls.debug_message
        }
        entry_parser = parsers.get(message_type)
        try:
            if entry_parser:
                entry = entry_parser(message)
            else:
                entry = cls.unhandled_entry_format(message_type, message)
        except Exception as e:
            entry = cls.unhandled_entry_format(message_type, message)
            entry['event'] = 'Exception caught: ' + entry['event']
            unhandled += 1

        entry['time'] = cls.timestamp_from_event(unescaped_block, timezone_offset=timezone_offset)
        # Store original raw timestamp for comparison purposes
        entry['original_timestamp'] = BinaryTools.unpack('uint32', unescaped_block, 0x01)

        # Check if Gen2 parser already provided structured data (optimized path)
        has_structured_data = 'structured_data' in entry
        if not has_structured_data:
            # Apply improved message parsing for unoptimized entries
            improved_event, improved_conditions, json_data, has_json_data, was_modified, modification_type = improve_message_parsing(
                entry.get('event', ''), entry.get('conditions', ''), verbosity_level=verbosity_level, logger=logger)
            entry['event'] = improved_event  # Store the improved event
            entry['conditions'] = improved_conditions  # Store the improved conditions

        if not entry.get('log_level'):
            entry['log_level'] = determine_log_level(entry.get('event', ''))

        # Always store the numeric message type ID
        entry['message_type'] = f"0x{message_type:X}"

        return length, entry, unhandled


class Gen3:
    entry_data_fencepost = b'\x00\xb2'
    Entry = namedtuple('Gen3EntryType', ['event', 'time', 'conditions', 'uninterpreted', 'log_level', 'message_type'])
    min_timestamp = datetime.strptime('2019-01-01', '%Y-%M-%d')
    max_timestamp = datetime.now() + timedelta(days=365)

    @classmethod
    def timestamp_is_valid(cls, event_timestamp: datetime):
        return cls.min_timestamp < event_timestamp < cls.max_timestamp

    @classmethod
    def payload_to_entry(cls, entry_payload: bytearray, hex_on_error=False, logger=None, verbosity_level=1) -> Entry:
        timestamp_bytes = list(entry_payload[0:4])
        timestamp_int = int.from_bytes(timestamp_bytes, byteorder='big', signed=False)
        event_timestamp = datetime.fromtimestamp(timestamp_int)
        if not cls.timestamp_is_valid(event_timestamp) and logger:
            logger.warning('Timestamp out of normal range: {}'.format(
                event_timestamp.strftime(ZERO_TIME_FORMAT)))
        # event_counter = BinaryTools.unpack('int16', entry_payload, 4)
        payload_string = BinaryTools.unpack_str(entry_payload, 7, len(entry_payload) - 7).strip()
        event_message = payload_string
        event_conditions = ''
        conditions = OrderedDict()
        conditions_str = ''
        data_payload = None
        try:
            data_fencepost = entry_payload.index(cls.entry_data_fencepost)
            data_payload = entry_payload[data_fencepost + 2:]
        except ValueError:
            pass
        # Clean up the payload string first to remove problematic characters for CSV
        if payload_string:
            payload_string = payload_string.replace('\t', ' ').replace('\n', ' ').replace('\r', ' ')
            payload_string = ''.join(c for c in payload_string if c.isprintable() or c.isspace()).strip()

        if len(payload_string) < 2:
            if hex_on_error:
                event_message = f"Unknown - {display_bytes_hex(entry_payload)}"
            else:
                event_message = "Unknown"
                # conditions_str = 'Payload: ' + display_bytes_hex(entry_payload)
        elif '. ' in payload_string:
            sentences = payload_string.split(sep='. ')
            event_conditions = sentences[-1]
            event_message = '. '.join(sentences[:-1]) if len(sentences) > 2 else sentences[0]
        elif payload_string.startswith('I_('):
            match = re.match(r'(I_)\((.*)\)(.*)', payload_string)
            if match:
                event_message = 'Current'
                key_prefix = match.group(1)
                event_conditions = match.group(2)
                value_suffix = match.group(3)
                kv_parts = event_conditions.split(', ')
                for list_part in kv_parts:
                    if ': ' in list_part:
                        [k, v] = list_part.split(': ', maxsplit=1)
                        conditions[key_prefix + k] = v + value_suffix
                event_conditions = ''
        elif ': ' in payload_string:
            [event_message, event_conditions] = payload_string.split(': ', maxsplit=1)
        elif ' = ' in payload_string:
            [event_message, event_conditions] = payload_string.split(' = ', maxsplit=1)
        elif ' from ' in payload_string and ' to ' in payload_string:
            [event_message, event_conditions] = payload_string.split(' from ', maxsplit=1)
            match = re.match(r'(.*) to (.*)', event_conditions)
            if match:
                conditions['from'] = match.group(1)
                conditions['to'] = match.group(2)
                event_conditions = ''
        else:
            match = re.match(r'([^()]+) \(([^()]+)\)', payload_string)
            if match:
                event_message = match.group(1)
                event_conditions = match.group(2)
        if event_conditions.startswith('V_('):
            match = re.match(r'(V_)\((.*)\)(.*)', event_conditions)
            if match:
                key_prefix = match.group(1)
                event_conditions = match.group(2)
                value_suffix = match.group(3).rstrip(',')
                kv_parts = event_conditions.split(', ')
                for list_part in kv_parts:
                    if ': ' in list_part:
                        [k, v] = list_part.split(': ', maxsplit=1)
                        conditions[key_prefix + k] = v + value_suffix
        elif 'Old: ' in event_conditions and 'New: ' in event_conditions:
            matches = re.search('Old: (0x[0-9a-fA-F]+) New: (0x[0-9a-fA-F]+)', event_conditions)
            if matches:
                old = matches.group(1)
                old_int = int(old, 16)
                old_bits = '{0:b}'.format(old_int)
                new = matches.group(2)
                new_int = int(new, 16)
                new_bits = '{0:b}'.format(new_int)
                if len(new_bits) != len(old_bits):
                    max_len = max(len(new_bits), len(old_bits))
                    bitwise_format = '{0:0' + str(max_len) + 'b}'
                    new_bits = bitwise_format.format(new_int)
                    old_bits = bitwise_format.format(old_int)
                conditions['old'] = old_bits
                conditions['new'] = new_bits
        elif ', ' in event_conditions:
            list_parts = [x.strip() for x in event_conditions.split(', ')]
            for list_part in list_parts:
                if ': ' in list_part:
                    [k, v] = list_part.split(': ', maxsplit=1)
                else:
                    list_part_words = list_part.split(' ')
                    if len(list_part_words) == 2:
                        [k, v] = list_part_words
                    else:
                        k = list_part
                        v = ''
                conditions[k] = v
        if len(conditions) > 0:
            for k, v in conditions.items():
                if conditions_str:
                    conditions_str += ', '
                conditions_str += (k + ': ' + v) if k and v else k or v
        elif event_conditions:
            conditions_str = event_conditions
        # Apply improved message parsing and determine log level
        improved_event, improved_conditions, json_data, has_json_data, was_modified, modification_type = improve_message_parsing(event_message, conditions_str, verbosity_level=verbosity_level, logger=logger)
        log_level = determine_log_level(improved_event)
        # Gen3 entries don't have numeric message types, so use "gen3" as identifier
        message_type = "gen3"

        return cls.Entry(improved_event, event_timestamp, improved_conditions,
                         display_bytes_hex(data_payload) if data_payload else '', log_level, message_type)


REV0 = 0
REV1 = 1
REV2 = 2
REV3 = 3  # Ring buffer format (2024+ firmware)


class LogData(object):
    """
    :type log_version: int
    :type header_info: Dict[str, str]
    :type entries_count: Optional[int]
    :type entries: List[str]
    :type timezone_offset: int
    """

    def __init__(self, log_file: LogFile, timezone_offset=None, verbosity_level=1):
        self.log_file = log_file
        self.timezone_offset = timezone_offset
        self.verbosity_level = verbosity_level
        self.log_version, self.header_info = self.get_version_and_header(log_file)
        self.entries_count, self.entries = self.get_entries_and_counts(log_file)

        # Eager processing: process entries immediately and cache results
        self._processed_entries = None
        self._processing_complete = False
        self._process_entries_eagerly()

    def _process_entries_eagerly(self):
        """Process all entries immediately after LogData creation and cache results."""
        if self._processing_complete:
            return  # Early return for subsequent calls

        try:
            logger = logger_for_input(self.log_file.file_path)
            self._processed_entries = self._collect_and_process_entries(
                logger=logger,
                verbosity_level=self.verbosity_level
            )
            self._processing_complete = True
        except Exception as e:
            # Fall back to lazy processing if eager processing fails
            logger_for_input(self.log_file.file_path).warning(f"Eager processing failed: {e}, falling back to lazy processing")
            self._processed_entries = None
            self._processing_complete = False

    def _get_processed_entries(self, start_time=None, end_time=None):
        """Get cached processed entries with optional time filtering."""
        # Ensure processing is complete
        if not self._processing_complete:
            self._process_entries_eagerly()

        if self._processed_entries is None:
            # Fallback to lazy processing
            logger = logger_for_input(self.log_file.file_path)
            return self._collect_and_process_entries(logger, start_time, end_time, self.verbosity_level)

        # Apply time filtering if needed
        if start_time or end_time:
            return self._filter_processed_entries(self._processed_entries, start_time, end_time)

        return self._processed_entries

    def _filter_processed_entries(self, entries, start_time, end_time):
        """Filter processed entries by time range."""
        if not start_time and not end_time:
            return entries

        filtered_entries = []
        for entry in entries:
            # Use sort_timestamp for filtering if available
            entry_time = entry.sort_timestamp if entry.sort_timestamp and entry.sort_timestamp > 0 else None

            # Skip entries with invalid timestamps
            if entry_time is None:
                continue

            # Apply start time filter
            if start_time and entry_time < start_time:
                continue

            # Apply end time filter
            if end_time and entry_time > end_time:
                continue

            filtered_entries.append(entry)

        return filtered_entries

    def _parse_entry_timestamp(self, time_str: str) -> datetime:
        """Parse entry timestamp string into datetime object."""
        if time_str.isdigit():
            # Invalid/zero timestamp - return epoch
            return datetime.fromtimestamp(0)

        try:
            # Try new ISO format first
            try:
                return datetime.strptime(time_str, ZERO_TIME_FORMAT)
            except ValueError:
                # Fall back to old format for compatibility
                return datetime.strptime(time_str, '%m/%d/%Y %H:%M:%S')
        except ValueError:
            # If all parsing fails, return epoch
            return datetime.fromtimestamp(0)

    def _collect_and_process_entries(self, logger=None, start_time=None, end_time=None, verbosity_level=None):
        """
        Centralized method to collect, parse, filter, and sort all log entries.
        Returns a list of ProcessedLogEntry objects ready for output formatting.
        """
        if not logger:
            logger = logger_for_input(self.log_file.file_path)

        # Use instance verbosity level if not provided
        if verbosity_level is None:
            verbosity_level = getattr(self, 'verbosity_level', 1)

        processed_entries = []

        if self.log_version < REV2 or self.log_version == REV3:
            # Handle REV0/REV1/REV3 formats - collect and sort entries
            collected_entries = []
            read_pos = 0

            if hasattr(self, 'entries_count'):
                for entry_num in range(self.entries_count):
                    try:
                        (length, entry_payload, unhandled) = Gen2.parse_entry(self.entries, read_pos,
                                                                              0,  # unhandled counter
                                                                              timezone_offset=self.timezone_offset,
                                                                              logger=logger, verbosity_level=verbosity_level)

                        # Extract timestamp for sorting
                        time_str = entry_payload.get('time', '0')
                        if time_str.isdigit():
                            sort_timestamp = int(time_str)
                        else:
                            try:
                                try:
                                    parsed_time = datetime.strptime(time_str, ZERO_TIME_FORMAT)
                                except ValueError:
                                    parsed_time = datetime.strptime(time_str, '%m/%d/%Y %H:%M:%S')
                                if parsed_time.year > 2030:
                                    sort_timestamp = 0
                                else:
                                    sort_timestamp = parsed_time.timestamp()
                            except:
                                sort_timestamp = 0

                        collected_entries.append((sort_timestamp, entry_payload, entry_num))
                        read_pos += length
                    except Exception as e:
                        logger.warning(f'Error parsing entry {entry_num}: {e}')
                        break

                # Apply timestamp interpolation before sorting
                collected_entries = Gen2.interpolate_missing_timestamps(collected_entries, logger)

                # Apply time filtering if specified
                if start_time or end_time:
                    collected_entries = self._filter_collected_entries(collected_entries, start_time, end_time)
                    logger.info(f"Filtered to {len(collected_entries)} entries based on time range")

                # Sort by timestamp (newest first)
                collected_entries.sort(key=lambda x: x[0], reverse=True)

                # Process sorted entries into ProcessedLogEntry objects
                for line_num, (sort_timestamp, entry_payload, original_entry_num) in enumerate(collected_entries):
                    message = entry_payload.get('event', '')
                    conditions = entry_payload.get('conditions', '')
                    log_level = entry_payload.get('log_level', 'INFO')

                    # Check if message has already been processed by Gen2/Gen3 parsers
                    existing_message_type = entry_payload.get('message_type')
                    structured_data = entry_payload.get('structured_data')
                    has_json_data = structured_data is not None

                    # Message was already processed by Gen2/Gen3 parser - use as-is
                    improved_message = message
                    improved_conditions = conditions
                    message_type = existing_message_type

                    processed_entry = ProcessedLogEntry(
                        entry_number=original_entry_num + 1,
                        timestamp=entry_payload.get('time', ''),
                        sort_timestamp=sort_timestamp if sort_timestamp > 0 else None,
                        log_level=log_level,
                        event=improved_message,
                        conditions=improved_conditions if improved_conditions else "",
                        uninterpreted="",
                        structured_data=structured_data,
                        has_structured_data=has_json_data,
                        message_type=message_type,
                        original_timestamp=str(entry_payload.get('original_timestamp', ''))
                    )
                    processed_entries.append(processed_entry)

        else:
            # Handle REV2 (Gen3) format
            for line, entry_payload in enumerate(self.entries):
                try:
                    entry = Gen3.payload_to_entry(entry_payload, logger=logger, verbosity_level=verbosity_level)

                    # Gen3 parser already processed the message - use the results directly
                    improved_event = entry.event
                    improved_conditions = entry.conditions
                    log_level = entry.log_level
                    message_type = entry.message_type if entry.message_type else "unchanged"

                    # Parse structured data if available
                    structured_data = None
                    has_json_data = False
                    if improved_conditions and improved_conditions.startswith('{'):
                        try:
                            structured_data = json.loads(improved_conditions)
                            improved_conditions = None  # Remove redundant text version
                            has_json_data = True
                        except json.JSONDecodeError:
                            pass  # Keep as text if JSON parsing fails

                    processed_entry = ProcessedLogEntry(
                        entry_number=line + 1,
                        timestamp=entry.time.strftime(ZERO_TIME_FORMAT),
                        sort_timestamp=entry.time.timestamp(),
                        log_level=log_level,
                        event=improved_event,
                        conditions=improved_conditions if improved_conditions else "",
                        uninterpreted=entry.uninterpreted if entry.uninterpreted else "",
                        structured_data=structured_data,
                        has_structured_data=has_json_data,
                        message_type=message_type,
                        original_timestamp=str(int(entry.time.timestamp()))  # Use timestamp as original for Gen3
                    )
                    processed_entries.append(processed_entry)
                except Exception as e:
                    logger.warning(f'Error processing entry {line}: {e}')

        return processed_entries

    def _filter_collected_entries(self, collected_entries, start_time=None, end_time=None):
        """Filter collected entries by time range."""
        if not start_time and not end_time:
            return collected_entries

        filtered_entries = []
        for sort_timestamp, entry_payload, entry_num in collected_entries:
            # Parse entry timestamp
            time_str = entry_payload.get('time', '0')
            entry_time = self._parse_entry_timestamp(time_str)

            # Skip obviously invalid timestamps (year > 2030 or epoch)
            if entry_time.year > 2030 or entry_time.year == 1970:
                filtered_entries.append((sort_timestamp, entry_payload, entry_num))
                continue

            # Apply timezone to entry timestamp for comparison
            try:
                # Try to import timezone utilities
                try:
                    from src.zero_log_parser.utils import apply_timezone_to_datetime
                    entry_time_tz = apply_timezone_to_datetime(entry_time, None)  # Use system timezone
                except ImportError:
                    # Fallback - assume local timezone
                    entry_time_tz = entry_time.replace(tzinfo=timezone.utc).astimezone()
            except:
                # Last fallback - use naive datetime
                entry_time_tz = entry_time

            # Apply filters
            if start_time and entry_time_tz < start_time:
                continue
            if end_time and entry_time_tz > end_time:
                continue

            filtered_entries.append((sort_timestamp, entry_payload, entry_num))

        return filtered_entries

    def _filter_gen3_entries(self, collected_gen3_entries, start_time=None, end_time=None):
        """Filter Gen3 entries by time range."""
        if not start_time and not end_time:
            return collected_gen3_entries

        filtered_entries = []
        for timestamp, entry, line in collected_gen3_entries:
            # Convert timestamp to datetime object
            entry_time = datetime.fromtimestamp(timestamp)

            # Apply timezone for comparison
            try:
                # Try to import timezone utilities
                try:
                    from src.zero_log_parser.utils import apply_timezone_to_datetime
                    entry_time_tz = apply_timezone_to_datetime(entry_time, None)  # Use system timezone
                except ImportError:
                    # Fallback - assume local timezone
                    entry_time_tz = entry_time.replace(tzinfo=timezone.utc).astimezone()
            except:
                # Last fallback - use naive datetime
                entry_time_tz = entry_time

            # Apply filters
            if start_time and entry_time_tz < start_time:
                continue
            if end_time and entry_time_tz > end_time:
                continue

            filtered_entries.append((timestamp, entry, line))

        return filtered_entries

    def get_version_and_header(self, log: LogFile):
        logger = logger_for_input(self.log_file.file_path)
        sys_info = OrderedDict()
        log_version = REV0
        if len(sys_info) == 0 and (self.log_file.is_mbb() or self.log_file.is_unknown()):
            # Check for ring buffer format (2024+ firmware) - starts with log entries
            if log.raw()[0] == 0xb2 or (len(log.raw()) == 0x40000 and log.index_of_sequence(b'\xa1\xa1\xa1\xa1')):
                # Ring buffer format detected
                log_version = REV3  # New revision for ring buffer format
                filename_vin = self.log_file.get_filename_vin()
                sys_info['VIN'] = filename_vin if filename_vin else 'Unknown'

                # Look for serial number - it's located 0x302 bytes after first run date header
                serial_found = False
                first_run_idx = log.index_of_sequence(b'\xa1\xa1\xa1\xa1')
                if first_run_idx:
                    serial_offset = first_run_idx + 0x302
                    try:
                        if serial_offset + 15 < len(log.raw()):
                            potential_serial = log.unpack_str(serial_offset, count=15).strip('\x00')
                            if potential_serial and len(potential_serial) >= 8 and potential_serial.isalnum():
                                sys_info['Serial number'] = potential_serial
                                serial_found = True
                    except:
                        pass

                # Fallback: search near section headers if pattern-based approach didn't work
                if not serial_found:
                    for search_offset in [0x3bd10, 0x3bd00, 0x3bd20]:
                        try:
                            if search_offset + 15 < len(log.raw()):
                                potential_serial = log.unpack_str(search_offset, count=15).strip('\x00')
                                if potential_serial and len(potential_serial) >= 8 and potential_serial.isalnum():
                                    sys_info['Serial number'] = potential_serial
                                    serial_found = True
                                    break
                        except:
                            pass

                if not serial_found:
                    sys_info['Serial number'] = 'Unknown'

                # Look for first run date
                first_run_idx = log.index_of_sequence(b'\xa1\xa1\xa1\xa1')
                if first_run_idx:
                    try:
                        sys_info['Initial date'] = log.unpack_str(first_run_idx + 4, count=20).strip('\x00')
                    except:
                        sys_info['Initial date'] = 'Unknown'
                else:
                    sys_info['Initial date'] = 'Unknown'

                sys_info['Model'] = 'Unknown'  # Model not found in ring buffer format
                sys_info['Firmware rev.'] = 'Unknown'
                sys_info['Board rev.'] = 'Unknown'

            else:
                # Legacy format - use original logic
                vin_v0 = log.unpack_str(0x240, count=17)  # v0 (Gen2)
                vin_v1 = log.unpack_str(0x252, count=17)  # v1 (Gen2 2019+)
                vin_v2 = log.unpack_str(0x029, count=17, encoding='latin_1')  # v2 (Gen3)
                if is_vin(vin_v0):
                    log_version = REV0
                    sys_info['Serial number'] = log.unpack_str(0x200, count=21)
                    sys_info['VIN'] = vin_v0
                    sys_info['Firmware rev.'] = log.unpack('uint16', 0x27b)
                    sys_info['Board rev.'] = log.unpack('uint16', 0x27d)
                    model_offset = 0x27f
                elif is_vin(vin_v1):
                    log_version = REV1
                    sys_info['Serial number'] = log.unpack_str(0x210, count=13)
                    sys_info['VIN'] = vin_v1
                    sys_info['Firmware rev.'] = log.unpack('uint16', 0x266)
                    sys_info['Board rev.'] = log.unpack('uint16', 0x268)  # TODO confirm Board rev.
                    model_offset = 0x26B
                elif is_vin(vin_v2):
                    log_version = REV2
                    sys_info['Serial number'] = log.unpack_str(0x03C, count=13)
                    sys_info['VIN'] = vin_v2
                    sys_info['Firmware rev.'] = log.unpack_str(0x06b, count=7)
                    sys_info['Board rev.'] = log.unpack_str(0x05C, count=8)
                    model_offset = 0x019
                else:
                    logger.warning("Unknown Log Format")
                    sys_info['VIN'] = vin_v0
                    model_offset = 0x27f

                filename_vin = self.log_file.get_filename_vin()
                if 'VIN' not in sys_info or not BinaryTools.is_printable(sys_info['VIN']):
                    logger.warning("VIN unreadable: %s", sys_info['VIN'])
                elif sys_info['VIN'] != filename_vin:
                    logger.warning("VIN mismatch: header:%s filename:%s", sys_info['VIN'],
                                   filename_vin)
                sys_info['Model'] = log.unpack_str(model_offset, count=3)
                sys_info['Initial date'] = log.unpack_str(0x2a, count=20)
        if len(sys_info) == 0 and (self.log_file.is_bms() or self.log_file.is_unknown()):
            # Check for two log formats:
            log_version_code = log.unpack('uint8', 0x4)
            if log_version_code == 0xb6:
                log_version = REV0
            elif log_version_code == 0xde:
                log_version = REV1
            elif log_version_code == 0x79:
                log_version = REV2
            else:
                logger.warning("Unknown Log Format: %s", log_version_code)
            sys_info['Initial date'] = log.unpack_str(0x12, count=20)
            if log_version == REV0:
                sys_info['BMS serial number'] = log.unpack_str(0x300, count=21)
                sys_info['Pack serial number'] = log.unpack_str(0x320, count=8)
            elif log_version == REV1:
                # TODO identify BMS serial number
                sys_info['Pack serial number'] = log.unpack_str(0x331, count=8)
            elif log_version == REV2:
                sys_info['BMS serial number'] = log.unpack_str(0x038, count=13)
                sys_info['Pack serial number'] = log.unpack_str(0x06c, count=7)
        elif self.log_file.is_unknown():
            sys_info['System info'] = 'unknown'
        return log_version, sys_info

    def get_entries_and_counts(self, log: LogFile):
        logger = logger_for_input(log.file_path)
        raw_log = log.raw()
        if self.log_version == REV3:
            # Ring buffer format - entries start at beginning of file
            entries_header_idx = log.index_of_sequence(b'\xa2\xa2\xa2\xa2')
            if entries_header_idx is not None:
                entries_end = log.unpack('uint32', 0x4, offset=entries_header_idx)
                entries_start = log.unpack('uint32', 0x8, offset=entries_header_idx)
                claimed_entries_count = log.unpack('uint32', 0xc, offset=entries_header_idx)
                logger.info('Event log header found: start=0x%x, end=0x%x, count=%d',
                           entries_start, entries_end, claimed_entries_count)

                # For ring buffer, entries start at 0x0000 and wrap around
                if entries_start >= entries_end:
                    event_log = raw_log[entries_start:] + raw_log[0:entries_end]
                else:
                    event_log = raw_log[entries_start:entries_end]
            else:
                # Fallback: scan entire file for entries
                logger.warning("No event log header found, scanning entire file")
                event_log = raw_log
                claimed_entries_count = 0

            entries_count = event_log.count(b'\xb2')
            logger.info('%d entries found (%d claimed)', entries_count, claimed_entries_count)

        elif self.log_version < REV2:
            # handle missing header index
            entries_header_idx = log.index_of_sequence(b'\xa2\xa2\xa2\xa2')
            if entries_header_idx is not None:
                entries_end = log.unpack('uint32', 0x4, offset=entries_header_idx)
                entries_start = log.unpack('uint32', 0x8, offset=entries_header_idx)
                claimed_entries_count = log.unpack('uint32', 0xc, offset=entries_header_idx)
                entries_data_begin = entries_header_idx + 0x10
            else:
                entries_end = len(raw_log)
                entries_start = log.index_of_sequence(b'\xb2')
                entries_data_begin = entries_start
                claimed_entries_count = 0

            # Handle data wrapping across the upper bound of the ring buffer
            if entries_start >= entries_end:
                event_log = raw_log[entries_start:] + \
                            raw_log[entries_data_begin:entries_end]
            else:
                event_log = raw_log[entries_start:entries_end]

            # count entry headers
            entries_count = event_log.count(b'\xb2')

            logger.info('%d entries found (%d claimed)', entries_count, claimed_entries_count)
        elif self.log_version == REV2:
            entries_count, event_log = self.get_gen3_entries(log, raw_log)

        return entries_count, event_log

    def get_gen3_entries(self, log, raw_log):
        self.gen3_fencepost_byte0 = raw_log[0x0a]  # before log_type
        self.gen3_fencepost_byte2 = raw_log[0x0c]  # before log_type
        first_fencepost_re = re.compile(
            bytes([self.gen3_fencepost_byte0, ord('.'), self.gen3_fencepost_byte2]))
        match = re.search(first_fencepost_re, raw_log)
        if not match:
            raise ValueError()
        first_fencepost_value = match.group(0)[1]
        # chrg_fencepost = b'\xff\xff' + bytes('CHRG', encoding='utf8')
        # chrg_indexes = log.indexes_of_sequence(chrg_fencepost)
        # another_fencepost = b'\x80\x00\xa2\xa2\x01\x00'
        entries_end = len(raw_log)
        entries_count = 0
        event_log = []
        event_start = match.start(0)
        current_fencepost_value = first_fencepost_value
        current_fencepost = self.event_fencepost(current_fencepost_value)
        while event_start is not None and event_start < entries_end:
            next_event_start = log.index_of_sequence(current_fencepost, start=event_start + 1)
            if next_event_start is not None:
                event_start = next_event_start
            next_fencepost = self.next_event_fencepost(current_fencepost)
            event_end = log.index_of_sequence(next_fencepost, start=event_start + 1)
            while event_end is None or event_end - event_start > 256:
                next_fencepost = self.next_event_fencepost(next_fencepost)
                event_end = log.index_of_sequence(next_fencepost, start=event_start + 1)
                if next_fencepost == current_fencepost:
                    break
            event_payload = raw_log[event_start - 4:event_end - 4 if event_end else event_end]
            event_log.append(event_payload)
            entries_count += 1
            current_fencepost = next_fencepost
            if event_end is None or event_end >= entries_end:
                break
        return entries_count, event_log

    def event_fencepost(self, value):
        return bytes([self.gen3_fencepost_byte0, value, self.gen3_fencepost_byte2])

    def next_event_fencepost(self, previous_event_fencepost):
        previous_value = 0
        if isinstance(previous_event_fencepost, int):
            previous_value = previous_event_fencepost
        elif isinstance(previous_event_fencepost, bytes):
            previous_value = previous_event_fencepost[1]
        next_value = previous_value + 1
        # Wraparound that avoids certain other common byte sequences:
        if next_value == 0xfe:
            next_value = 0xff
        elif next_value == 0x00:
            next_value = 0x01
        if next_value > 0xff:
            next_value = 0x01
        return self.event_fencepost(next_value)

    header_divider = \
        '+--------+----------------------+--------------------------+----------------------------------\n'

    def has_official_output_reference(self):
        return self.log_version < REV2 or self.log_version == REV3

    def emit_tabular_decoding(self, output_file: str, out_format='tsv', logger=None, start_time=None, end_time=None, unnest=False):
        file_suffix = '.tsv' if out_format == 'tsv' else '.csv'
        tabular_output_file = output_file.replace('.txt', file_suffix, 1)
        field_sep = '\t' if out_format == 'tsv' else CSV_DELIMITER
        record_sep = os.linesep
        # Modify headers based on unnest option
        if unnest:
            headers = ['entry', 'timestamp', 'log_level', 'message', 'condition_key', 'condition_value', 'uninterpreted']
        else:
            headers = ['entry', 'timestamp', 'log_level', 'message', 'conditions', 'uninterpreted']

        if not logger:
            logger = logger_for_input(self.log_file.file_path)

        # Use cached processed entries with optional time filtering (OPTIMIZED)
        processed_entries = self._get_processed_entries(start_time, end_time)

        with open(tabular_output_file, 'w', encoding='utf-8') as output:
            def write_row(values):
                output.write(field_sep.join(values) + record_sep)

            write_row(headers)

            # Write processed entries
            for entry in processed_entries:
                if unnest and entry.structured_data:
                    # Unnest structured data into multiple rows
                    for key, value in entry.structured_data.items():
                        row_values = [
                            str(entry.entry_number),
                            entry.timestamp,
                            entry.log_level,
                            entry.event,
                            key,
                            str(value),
                            entry.uninterpreted
                        ]
                        write_row([print_value_tabular(x) for x in row_values])
                else:
                    # Standard single-row format
                    if unnest:
                        # For unnest format but no structured data, use empty key/value
                        row_values = [
                            str(entry.entry_number),
                            entry.timestamp,
                            entry.log_level,
                            entry.event,
                            '',  # condition_key
                            entry.conditions,  # condition_value (original conditions text)
                            entry.uninterpreted
                        ]
                    else:
                        # Use JSON-encoded structured data in conditions field if available
                        conditions_output = entry.conditions
                        if entry.structured_data:
                            conditions_output = json.dumps(entry.structured_data, separators=(',', ':'))

                        row_values = [
                            str(entry.entry_number),
                            entry.timestamp,
                            entry.log_level,
                            entry.event,
                            conditions_output,
                            entry.uninterpreted
                        ]
                    write_row([print_value_tabular(x) for x in row_values])

        logger_for_input(self.log_file.file_path).info('Saved to %s', tabular_output_file)

    def emit_json_decoding(self, output_file: str, logger=None, start_time=None, end_time=None):
        """Generate JSON output with structured log entries"""
        json_output_file = output_file.replace('.txt', '.json', 1)

        if not logger:
            logger = logger_for_input(self.log_file.file_path)

        # Use cached processed entries with optional time filtering (OPTIMIZED)
        processed_entries = self._get_processed_entries(start_time, end_time)

        # Prepare the JSON structure
        json_output = {
            'metadata': {
                'source_file': self.log_file.file_path,
                'log_type': 'MBB' if 'MBB' in self.log_file.file_path or 'Mbb' in self.log_file.file_path else 'BMS',
                'parser_version': f'zero-log-parser-{PARSER_VERSION}',
                'generated_at': datetime.now().isoformat(),
                'timezone': f'UTC{self.timezone_offset/3600:+.1f}' if self.timezone_offset else 'UTC+0.0',
                'total_entries': len(processed_entries)
            },
            'log_info': {
                'vin': getattr(self, 'vin', 'Unknown'),
                'serial_number': getattr(self, 'serial_number', 'Unknown'),
                'initial_date': getattr(self, 'initial_date', 'Unknown'),
                'model': getattr(self, 'model', 'Unknown'),
                'firmware_rev': getattr(self, 'firmware_rev', 'Unknown'),
                'board_rev': getattr(self, 'board_rev', 'Unknown')
            },
            'entries': []
        }

        # Convert processed entries to JSON format
        for entry in processed_entries:
            json_entry = {
                'entry_number': entry.entry_number,
                'timestamp': entry.timestamp,
                'sort_timestamp': entry.sort_timestamp,
                'log_level': entry.log_level,
                'event': entry.event,
                'conditions': entry.conditions if entry.conditions and not entry.structured_data else None,
                'uninterpreted': entry.uninterpreted if entry.uninterpreted else None,
                'is_structured_data': entry.has_structured_data,
                'message_type': entry.message_type
            }

            # Add structured data if available
            if entry.structured_data:
                json_entry['structured_data'] = entry.structured_data

            json_output['entries'].append(json_entry)

        # Write JSON output
        try:
            with open(json_output_file, 'w', encoding='utf-8') as f:
                json.dump(json_output, f, indent=2, ensure_ascii=False)
            logger.info('Saved to %s', json_output_file)
        except Exception as e:
            logger.error(f'Error writing JSON file {json_output_file}: {e}')
            raise

    @classmethod
    def output_line_number_field(cls, line: int):
        return ' {line:05d}'.format(line=line)

    @classmethod
    def output_time_field(cls, time: str):
        return '     {time:>19s}'.format(time=time)

    def emit_zero_compatible_decoding(self, output_file: str, logger=None, start_time=None, end_time=None):
        with codecs.open(output_file, 'wb', 'utf-8-sig') as f:
            logger = logger_for_input(self.log_file.file_path)

            def write_line(text=None):
                f.write(text + '\n' if text else '\n')

            write_line('Zero ' + self.log_file.log_type + ' log')
            write_line()

            for k, v in self.header_info.items():
                write_line('{0:18} {1}'.format(k, v))

            # Add timezone information
            tz_hours = self.timezone_offset / 3600
            if tz_hours >= 0:
                tz_str = f'UTC+{tz_hours:.1f}'
            else:
                tz_str = f'UTC{tz_hours:.1f}'
            write_line('{0:18} {1}'.format('Timezone', tz_str))
            write_line()

            # Use centralized processing
            processed_entries = self._collect_and_process_entries(logger, start_time, end_time)

            write_line('Printing {0} of {0} log entries..'.format(len(processed_entries)))
            write_line()
            write_line(' Entry    Time of Log            Level     Event                      Conditions')
            f.write(self.header_divider)

            # Track unknown entries for compatibility with original logic
            unknown_entries = 0
            unknown = []

            def format_structured_data(structured_data):
                """Format structured data as readable key-value pairs"""
                if not structured_data:
                    return ""

                # Create readable key-value pairs
                formatted_pairs = []
                for key, value in structured_data.items():
                    # Convert snake_case to readable format
                    readable_key = key.replace('_', ' ').title()

                    # Format value based on type and key patterns
                    if isinstance(value, str):
                        formatted_value = value
                    elif 'percent' in key.lower():
                        formatted_value = f"{value}%"
                    elif 'ma' in key.lower():  # Check mA before amps to avoid conflict
                        formatted_value = f"{value}mA"
                    elif 'amps' in key.lower() or 'current' in key.lower():
                        formatted_value = f"{value}A"
                    elif 'mv' in key.lower():
                        formatted_value = f"{value}mV"
                    elif 'volts' in key.lower() or 'voltage' in key.lower():
                        formatted_value = f"{value}V"
                    elif 'celsius' in key.lower() or 'temp' in key.lower():
                        formatted_value = f"{value}°C"
                    else:
                        formatted_value = str(value)

                    formatted_pairs.append(f"{readable_key}: {formatted_value}")

                return ", ".join(formatted_pairs)

            # Output processed entries with zero-compatible formatting
            for entry in processed_entries:
                line_prefix = (self.output_line_number_field(entry.entry_number)
                               + self.output_time_field(entry.timestamp)
                               + f'  {entry.log_level:8}')

                # Determine what to show in conditions field
                conditions_display = ""
                if entry.structured_data:
                    # Format structured data as readable key-value pairs
                    conditions_display = format_structured_data(entry.structured_data)
                elif entry.conditions:
                    conditions_display = entry.conditions

                if conditions_display:
                    if '???' in conditions_display:
                        u = conditions_display[0]
                        unknown_entries += 1
                        if u not in unknown:
                            unknown.append(u)
                        conditions_display = '???'
                        write_line(
                            line_prefix + '   {message} {conditions}'.format(
                                message=entry.event, conditions=conditions_display))
                    else:
                        write_line(
                            line_prefix + '   {message:25}  {conditions}'.format(
                                message=entry.event, conditions=conditions_display))
                elif entry.uninterpreted:
                    # Handle Gen3 format with uninterpreted field
                    output_line = line_prefix + '   {event} [{uninterpreted}]'.format(
                        event=entry.event,
                        uninterpreted=entry.uninterpreted)
                    if re.match(r'\s+\[', output_line):
                        raise ValueError()
                    write_line(output_line)
                else:
                    write_line(line_prefix + '   {message}'.format(message=entry.event))

            write_line()

        # Log statistics (compatibility with original logic)
        if unknown:
            logger.info('%d unknown entries of types %s',
                        unknown_entries,
                        ', '.join(hex(ord(x)) for x in unknown))

        logger.info('Saved to %s', output_file)

    def _get_vin(self):
        """Get VIN from header info, handling various formats"""
        return self.header_info.get('VIN', 'Unknown')

    def _get_entry_key(self, entry_payload, entry_num):
        """Generate a unique key for entry deduplication based on timestamp, event type, and content"""
        timestamp = entry_payload.get('time', '0')
        event = entry_payload.get('event', '')
        conditions = entry_payload.get('conditions', '')

        # For entries with identical content, use entry number as tiebreaker
        return (timestamp, event, conditions, entry_num)

    def _merge_entries(self, other_log_data):
        """Merge entries from another LogData using cached ProcessedLogEntry objects with optimized deduplication."""
        # Use cached ProcessedLogEntry objects for efficient set-based deduplication
        self_entries = set(self._get_processed_entries())
        other_entries = set(other_log_data._get_processed_entries())

        # Efficient set-based merge with automatic deduplication using ProcessedLogEntry.__hash__ and __eq__
        merged_entries_set = self_entries | other_entries

        # Convert back to sorted list (newest first, matching existing behavior)
        merged_entries_list = sorted(merged_entries_set,
                                   key=lambda x: x.sort_timestamp if x.sort_timestamp and x.sort_timestamp > 0 else 0,
                                   reverse=True)

        # Store merged entries in the cache
        self._processed_entries = merged_entries_list
        self._processing_complete = True

        # Update entries count
        self.entries_count = len(merged_entries_list)

        # For backward compatibility, we need to maintain the original binary/payload format
        # This is a necessary trade-off for the optimization while maintaining compatibility
        if self.log_version < REV2 or self.log_version == REV3:
            # For Gen2 format, we keep the original entries format but note that it's now optimized
            # The actual data is in _processed_entries cache
            self.entries = b'OPTIMIZED_MERGE_DATA'  # Placeholder - actual data in _processed_entries
        else:
            # For Gen3 format, similar approach
            self.entries = ['OPTIMIZED_MERGE_DATA']  # Placeholder

        return len(merged_entries_list)

    def __add__(self, other):
        """Merge two LogData objects using the + operator"""
        if not isinstance(other, LogData):
            raise TypeError(f"Cannot merge LogData with {type(other).__name__}")

        # Check VIN compatibility
        self_vin = self._get_vin()
        other_vin = other._get_vin()

        if self_vin != 'Unknown' and other_vin != 'Unknown' and self_vin != other_vin:
            raise MismatchingVinError(self_vin, other_vin)

        # Create new merged LogData
        from copy import deepcopy
        merged = deepcopy(self)

        # Merge entries with duplicate removal using optimized ProcessedLogEntry approach
        merged._merge_entries(other)

        # Prefer non-Unknown VIN
        if merged._get_vin() == 'Unknown' and other_vin != 'Unknown':
            merged.header_info['VIN'] = other_vin

        # Merge other header info, preferring non-Unknown values
        for key, value in other.header_info.items():
            if key not in merged.header_info or merged.header_info[key] == 'Unknown':
                if value != 'Unknown':
                    merged.header_info[key] = value

        return merged

    def __iadd__(self, other):
        """Merge another LogData object into this one using the += operator"""
        if not isinstance(other, LogData):
            raise TypeError(f"Cannot merge LogData with {type(other).__name__}")

        # Check VIN compatibility
        self_vin = self._get_vin()
        other_vin = other._get_vin()

        if self_vin != 'Unknown' and other_vin != 'Unknown' and self_vin != other_vin:
            raise MismatchingVinError(self_vin, other_vin)

        # Merge entries with duplicate removal
        self.entries, self.entries_count = self._merge_entries(other.entries, other.entries_count)

        # Prefer non-Unknown VIN
        if self._get_vin() == 'Unknown' and other_vin != 'Unknown':
            self.header_info['VIN'] = other_vin

        # Merge other header info, preferring non-Unknown values
        for key, value in other.header_info.items():
            if key not in self.header_info or self.header_info[key] == 'Unknown':
                if value != 'Unknown':
                    self.header_info[key] = value

        return self

    def __radd__(self, other):
        """Support sum() and other right-hand addition operations"""
        if other == 0:  # sum() starts with 0
            return self
        return other.__add__(self)


def parse_log(bin_file: str, output_file: str, tz_code=None, verbosity_level=1, verbose=None, logger=None, output_format='txt', start_time=None, end_time=None, unnest=False):
    """
    Parse a Zero binary log file into a human readable text file
    """
    # Handle backward compatibility for verbose parameter
    if verbose is not None:
        verbosity_level = 2 if verbose else 1

    if not logger:
        logger = console_logger(bin_file, verbosity_level=verbosity_level)
    logger.info('Parsing %s', bin_file)

    timezone_offset = get_timezone_offset(tz_code)

    log = LogFile(bin_file)
    log_data = LogData(log, timezone_offset=timezone_offset, verbosity_level=verbosity_level)

    if output_format.lower() in ['csv', 'tsv']:
        # Generate CSV/TSV output
        log_data.emit_tabular_decoding(output_file, out_format=output_format.lower(), start_time=start_time, end_time=end_time, unnest=unnest)
    elif output_format.lower() == 'json':
        # Generate JSON output
        log_data.emit_json_decoding(output_file, start_time=start_time, end_time=end_time)
    elif output_format.lower() == 'txt':
        # Generate standard text output
        if log_data.has_official_output_reference():
            log_data.emit_zero_compatible_decoding(output_file, start_time=start_time, end_time=end_time)
        else:
            log_data.emit_tabular_decoding(output_file, start_time=start_time, end_time=end_time, unnest=unnest)
            log_data.emit_zero_compatible_decoding(output_file, start_time=start_time, end_time=end_time)
    else:
        # Default to text format for unknown formats
        if log_data.has_official_output_reference():
            log_data.emit_zero_compatible_decoding(output_file, start_time=start_time, end_time=end_time)
        else:
            log_data.emit_tabular_decoding(output_file, start_time=start_time, end_time=end_time, unnest=unnest)
            log_data.emit_zero_compatible_decoding(output_file, start_time=start_time, end_time=end_time)


def default_parsed_output_for(bin_file_path: str):
    return os.path.splitext(bin_file_path)[0] + '.txt'


def is_log_file_path(file_path: str):
    return file_path.endswith('.bin')


def console_logger(name: str, verbosity_level=1, verbose=None):
    # Handle backward compatibility for verbose parameter
    if verbose is not None:
        verbosity_level = 2 if verbose else 1

    # Map verbosity levels to logging levels
    level_map = {
        0: logging.ERROR,    # Quiet - only errors
        1: logging.INFO,     # Normal - info and above
        2: logging.DEBUG,    # Verbose - debug and above
        3: logging.DEBUG,    # Very verbose - same as verbose for logging
        4: logging.DEBUG     # Debug - same as verbose for logging
    }

    log_level = level_map.get(verbosity_level, logging.INFO)
    logger = logging.Logger(name, level=log_level)
    logger_formatter = logging.Formatter('%(asctime)s [%(name)s] [%(levelname)s] %(message)s')
    logger_handler = logging.StreamHandler()
    logger_handler.setFormatter(logger_formatter)
    logger.addHandler(logger_handler)
    return logger


def logger_for_input(bin_file):
    return logging.getLogger(bin_file)


def generate_merged_output_name(bin_files, output_format='txt'):
    """Generate a meaningful output filename for merged log files"""
    import os
    from datetime import datetime

    # Extract common elements from filenames
    basenames = [os.path.basename(f) for f in bin_files]

    # Try to find common VIN/serial pattern
    common_vin = None
    for name in basenames:
        if len(name) > 17 and name.startswith('538'):  # Zero VIN pattern
            vin_candidate = name[:17]
            if all(vin_candidate in other_name for other_name in basenames):
                common_vin = vin_candidate
                break

    # Create descriptive filename
    if common_vin:
        timestamp = datetime.now().strftime('%Y%m%d_%H%M')
        filename = f"{common_vin}_merged_{len(bin_files)}files_{timestamp}.{output_format}"
    else:
        timestamp = datetime.now().strftime('%Y%m%d_%H%M')
        filename = f"zero_logs_merged_{len(bin_files)}files_{timestamp}.{output_format}"

    return filename


def parse_multiple_logs(bin_files, output_file, tz_code=None, verbosity_level=1, verbose=None, logger=None, output_format='txt', start_time=None, end_time=None, unnest=False):
    """
    Parse multiple Zero binary log files and merge them intelligently

    Args:
        bin_files: List of binary log file paths
        output_file: Output filename for merged result
        tz_code: Timezone offset
        verbose: Enable verbose logging
        logger: Logger instance
        output_format: Output format (txt, csv, tsv, json)
    """
    # Handle backward compatibility for verbose parameter
    if verbose is not None:
        verbosity_level = 2 if verbose else 1

    if not logger:
        logger = console_logger(' + '.join(bin_files), verbosity_level=verbosity_level)

    logger.info('Multi-file parsing: %d files', len(bin_files))

    # Validate all files exist and are readable
    for bin_file in bin_files:
        if not os.path.exists(bin_file):
            raise FileNotFoundError(f"Log file not found: {bin_file}")
        if not is_log_file_path(bin_file):
            logger.warning(f"File may not be a valid log file: {bin_file}")

    merged_log_data = None
    successful_files = 0

    # Convert timezone offset to seconds
    timezone_offset = get_timezone_offset(tz_code)

    # Process each file and merge
    for i, bin_file in enumerate(bin_files):
        try:
            logger.info('[%d/%d] Processing %s', i+1, len(bin_files), bin_file)

            # Parse individual file to LogData
            log_file = LogFile(bin_file, logger=logger)
            log_data = LogData(log_file, timezone_offset=timezone_offset, verbosity_level=verbosity_level)

            if merged_log_data is None:
                # First file becomes the base
                merged_log_data = log_data
                successful_files = 1
                logger.info('Base log data: %s entries', len(log_data.entries) if hasattr(log_data, 'entries') else 'unknown')
            else:
                # Merge with existing data using LogData's smart merging
                try:
                    merged_log_data = merged_log_data + log_data
                    successful_files += 1
                    logger.info('Merged successfully. Total entries: %s',
                              len(merged_log_data.entries) if hasattr(merged_log_data, 'entries') else 'unknown')
                except MismatchingVinError as e:
                    logger.error('VIN mismatch: %s', e)
                    logger.error('Skipping file: %s', bin_file)
                    continue
                except Exception as e:
                    logger.error('Failed to merge %s: %s', bin_file, e)
                    logger.error('Skipping file: %s', bin_file)
                    continue

        except Exception as e:
            logger.error('Failed to process %s: %s', bin_file, e)
            continue

    if merged_log_data is None:
        raise RuntimeError("No files could be processed successfully")

    if successful_files < len(bin_files):
        logger.warning('Successfully merged %d out of %d files', successful_files, len(bin_files))
    else:
        logger.info('Successfully merged all %d files', successful_files)

    # Output merged result
    logger.info('Writing merged log to %s', output_file)

    if output_format.lower() in ['csv', 'tsv']:
        # Generate CSV/TSV output
        merged_log_data.emit_tabular_decoding(output_file, out_format=output_format.lower(), start_time=start_time, end_time=end_time, unnest=unnest)
    elif output_format.lower() == 'json':
        # Generate JSON output
        merged_log_data.emit_json_decoding(output_file, start_time=start_time, end_time=end_time)
    elif output_format.lower() == 'txt':
        # Generate standard text output
        if merged_log_data.has_official_output_reference():
            merged_log_data.emit_zero_compatible_decoding(output_file, start_time=start_time, end_time=end_time)
        else:
            merged_log_data.emit_tabular_decoding(output_file, start_time=start_time, end_time=end_time, unnest=unnest)
            merged_log_data.emit_zero_compatible_decoding(output_file, start_time=start_time, end_time=end_time)
    else:
        # Default to text format for unknown formats
        if merged_log_data.has_official_output_reference():
            merged_log_data.emit_zero_compatible_decoding(output_file, start_time=start_time, end_time=end_time)
        else:
            merged_log_data.emit_tabular_decoding(output_file, start_time=start_time, end_time=end_time, unnest=unnest)
            merged_log_data.emit_zero_compatible_decoding(output_file, start_time=start_time, end_time=end_time)

    logger.info('Multi-file parsing completed: %s', output_file)


def main():
    import argparse
    parser = argparse.ArgumentParser(
        description='Parse Zero Motorcycle binary log files. Supports single or multiple files with smart merging.',
        epilog='For interactive plotting, use: zero-plotting <input_files>')
    parser.add_argument('bin_files', nargs='+', help='Zero *.bin log file(s) to decode. Multiple files will be intelligently merged.')
    parser.add_argument('--timezone', help='Timezone offset in hours from UTC (e.g., -8 for PST, +1 for CET) or timezone name (e.g., "Europe/Berlin", "US/Pacific"). Defaults to local system timezone.')
    parser.add_argument('-o', '--output', help='decoded log filename (auto-generated if not specified)')
    parser.add_argument('-f', '--format', choices=['txt', 'csv', 'tsv', 'json'], default='txt',
                       help='Output format: txt (default), csv, tsv, or json')
    parser.add_argument('--unnest', action='store_true',
                       help='For CSV/TSV formats: expand structured data into separate rows with condition_key and condition_value columns')
    parser.add_argument('--start', help='Filter entries after this time (e.g., "June 2025", "2025-06-15", "last month")')
    parser.add_argument('--end', help='Filter entries before this time (e.g., "June 2025", "2025-06-15", "last month")')
    parser.add_argument('--start-end', help='Filter entries within this period (e.g., "June 2025" sets both start and end boundaries automatically)')
    parser.add_argument('-v', '--verbose', action='count', default=1, help='Increase verbosity level (-v, -vv, -vvv for levels 2, 3, 4)')
    parser.add_argument('-q', '--quiet', action='store_true', help='Quiet mode (verbosity level 0)')
    args = parser.parse_args()

    # Calculate verbosity level
    if args.quiet:
        verbosity_level = 0
    else:
        verbosity_level = args.verbose

    # Handle single vs multiple files
    bin_files = args.bin_files
    tz_code = args.timezone
    output_format = args.format

    # Parse time filtering parameters
    start_time = None
    end_time = None

    # Handle --start-end shorthand
    if args.start_end:
        if args.start or args.end:
            parser.error("--start-end cannot be used with --start or --end")
        try:
            # Try to import time parsing utilities
            try:
                from src.zero_log_parser.utils import parse_time_range
            except ImportError:
                # Fallback for standalone script usage
                def parse_time_range(time_str, tz_code):
                    # Simple fallback implementation
                    raise ValueError("Time filtering requires package installation")

            start_time, end_time = parse_time_range(args.start_end, tz_code)
        except Exception as e:
            parser.error(f"Invalid --start-end time specification: {e}")
    else:
        # Handle individual --start and --end parameters
        if args.start:
            try:
                try:
                    from src.zero_log_parser.utils import parse_time_filter_start
                except ImportError:
                    raise ValueError("Time filtering requires package installation")
                start_time = parse_time_filter_start(args.start, tz_code)
            except Exception as e:
                parser.error(f"Invalid --start time specification: {e}")

        if args.end:
            try:
                try:
                    from src.zero_log_parser.utils import parse_time_filter_end
                except ImportError:
                    raise ValueError("Time filtering requires package installation")
                end_time = parse_time_filter_end(args.end, tz_code)
            except Exception as e:
                parser.error(f"Invalid --end time specification: {e}")

    if len(bin_files) == 1:
        # Single file - use existing behavior
        bin_file = bin_files[0]
        output_file = args.output or default_parsed_output_for(bin_file)
        parse_log(bin_file, output_file, tz_code=tz_code, verbosity_level=verbosity_level, output_format=output_format,
                  start_time=start_time, end_time=end_time, unnest=args.unnest)
    else:
        # Multiple files - use new multi-file parsing
        output_file = args.output or generate_merged_output_name(bin_files, output_format)
        parse_multiple_logs(bin_files, output_file,
                            tz_code=tz_code, verbosity_level=verbosity_level, output_format=output_format,
                            start_time=start_time, end_time=end_time, unnest=args.unnest)


if __name__ == '__main__':
    main()
