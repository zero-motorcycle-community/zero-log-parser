#!/usr/bin/env python3

"""
Little decoder utility to parse Zero Motorcycle main bike board (MBB) and
battery management system (BMS) logs. These may be extracted from the bike
using the Zero mobile app. Once paired over bluetooth, select 'Support' >
'Email bike logs' and send the logs to yourself rather than / in addition to
zero support.

Usage:

   $ python zero_log_parser.py <*.bin file> [-o output_file]

"""

import os
import struct
import string
import codecs
from time import localtime, strftime, gmtime
from collections import OrderedDict
from math import trunc
from typing import Dict, Optional, Union

TIME_FORMAT = '%m/%d/%Y %H:%M:%S'
USE_MBB_TIME = True


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
        'int32': 'i',
        'uint32': 'I',
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
        return type_convert(unpacked) if type_convert else unpacked

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
    def decode_str(log_text_segment: bytearray) -> str:
        """Decodes UTF-8 strings from a test segment, ignoring any errors"""
        return log_text_segment.decode('utf-8', 'ignore')

    @classmethod
    def unpack_str(cls, log_text_segment: bytearray, address, count=1, offset=0) -> str:
        """Unpacks and decodes UTF-8 strings from a test segment, ignoring any errors"""
        unpacked = cls.unpack('char', log_text_segment, address, count, offset)
        return cls.decode_str(unpacked.partition(b'\0')[0])

    @staticmethod
    def is_printable(bytes_or_str: str) -> bool:
        return all(c in string.printable for c in bytes_or_str)


# noinspection PyMissingOrEmptyDocstring
class LogFile:
    """
    Wrapper for our raw log file
    """

    def __init__(self, file_path: str):
        with open(file_path, 'rb') as f:
            self._data = bytearray(f.read())

    def index_of_sequence(self, sequence):
        return self._data.index(sequence)

    def unpack(self, type_name, address, count=1, offset=0):
        return BinaryTools.unpack(type_name, self._data, address + offset,
                                  count=count)

    def decode_str(self, address, count=1, offset=0):
        return BinaryTools.decode_str(BinaryTools.unpack('char', self._data, address + offset,
                                                         count=count))

    def unpack_str(self, address, count=1, offset=0) -> str:
        """Unpacks and decodes UTF-8 strings from a test segment, ignoring any errors"""
        unpacked = self.unpack('char', address, count, offset)
        return BinaryTools.decode_str(unpacked.partition(b'\0')[0])

    def is_printable(self, address, count=1, offset=0) -> bool:
        unpacked = self.unpack('char', address, count, offset).decode('utf-8', 'ignore')
        return BinaryTools.is_printable(unpacked) and len(unpacked) == count

    def extract(self, start_address, length, offset=0):
        return self._data[start_address + offset:
                          start_address + length + offset]

    def raw(self):
        return bytearray(self._data)


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


def parse_entry(log_data, address, unhandled):
    """
    Parse an individual entry from a LogFile into a human readable form
    """
    try:
        header = log_data[address]
    # IndexError: bytearray index out of range
    except IndexError:
        # print "IndexError log_data[%r]: forcing header_bad"%(address)
        header = 0
    # correct header offset as needed to prevent errors
    header_bad = header != 0xb2
    while header_bad:
        address += 1
        try:
            header = log_data[address]
        except IndexError:
            # IndexError: bytearray index out of range
            # print "IndexError log_data[%r]: forcing header_bad"%(address)
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

    message_type = BinaryTools.unpack('uint8', unescaped_block, 0x00)
    timestamp = BinaryTools.unpack('uint32', unescaped_block, 0x01)
    message = unescaped_block[0x05:]

    def bms_discharge_level(x):
        bike = {
            0x01: 'Bike On',
            0x02: 'Charge',
            0x03: 'Idle'
        }
        return {
            'event': 'Discharge level',
            'conditions':
                '{AH:03.0f} AH, SOC:{SOC:3d}%, I:{I:3.0f}A, L:{L}, l:{l}, H:{H}, B:{B:03d}, PT:{PT:03d}C, BT:{BT:03d}C, PV:{PV:6d}, M:{M}'.format(
                    AH=trunc(BinaryTools.unpack('uint32', x, 0x06) / 1000000.0),
                    B=BinaryTools.unpack('uint16', x, 0x02) - BinaryTools.unpack('uint16', x, 0x0),
                    I=trunc(BinaryTools.unpack('int32', x, 0x10) / 1000000.0),
                    L=BinaryTools.unpack('uint16', x, 0x0),
                    H=BinaryTools.unpack('uint16', x, 0x02),
                    PT=BinaryTools.unpack('uint8', x, 0x04),
                    BT=BinaryTools.unpack('uint8', x, 0x05),
                    SOC=BinaryTools.unpack('uint8', x, 0x0a),
                    PV=BinaryTools.unpack('uint32', x, 0x0b),
                    l=BinaryTools.unpack('uint16', x, 0x14),
                    M=bike.get(BinaryTools.unpack('uint8', x, 0x0f)),
                    X=BinaryTools.unpack('uint16', x, 0x16))
        }

    def bms_charge_event_fields(x):
        return {
            'AH': trunc(BinaryTools.unpack('uint32', x, 0x06) / 1000000.0),
            'B': BinaryTools.unpack('uint16', x, 0x02) - BinaryTools.unpack('uint16', x, 0x0),
            'L': BinaryTools.unpack('uint16', x, 0x00),
            'H': BinaryTools.unpack('uint16', x, 0x02),
            'PT': BinaryTools.unpack('uint8', x, 0x04),
            'BT': BinaryTools.unpack('uint8', x, 0x05),
            'SOC': BinaryTools.unpack('uint8', x, 0x0a),
            'PV': BinaryTools.unpack('uint32', x, 0x0b)
        }

    def bms_charge_full(x):
        fields = bms_charge_event_fields(x)
        return {
            'event': 'Charged To Full',
            'conditions':
                '{AH:03.0f} AH, SOC: {SOC}%,         L:{L},         H:{H}, B:{B:03d}, PT:{PT:03d}C, BT:{BT:03d}C, PV:{PV:6d}'.format_map(
                    fields)
        }

    def bms_discharge_low(x):
        fields = bms_charge_event_fields(x)

        return {
            'event': 'Discharged To Low',
            'conditions':
                '{AH:03.0f} AH, SOC:{SOC:3d}%,         L:{L},         H:{H}, B:{B:03d}, PT:{PT:03d}C, BT:{BT:03d}C, PV:{PV:6d}'.format_map(
                    fields)
        }

    def bms_system_state(x):
        return {
            'event': 'System Turned ' + convert_bit_to_on_off(BinaryTools.unpack('bool', x, 0x0))
        }

    def bms_soc_adj_voltage(x):
        return {
            'event': 'SOC adjusted for voltage',
            'conditions':
                'old:   {old}uAH (soc:{old_soc}%), new:   {new}uAH (soc:{new_soc}%), low cell: {low} mV'.format(
                    old=BinaryTools.unpack('uint32', x, 0x00),
                    old_soc=BinaryTools.unpack('uint8', x, 0x04),
                    new=BinaryTools.unpack('uint32', x, 0x05),
                    new_soc=BinaryTools.unpack('uint8', x, 0x09),
                    low=BinaryTools.unpack('uint16', x, 0x0a))
        }

    def bms_curr_sens_zero(x):
        fields = {
            'old': BinaryTools.unpack('uint16', x, 0x00),
            'new': BinaryTools.unpack('uint16', x, 0x02),
            'corrfact': BinaryTools.unpack('uint8', x, 0x04)
        }
        return {
            'event': 'Current Sensor Zeroed',
            'conditions': 'old: {old}mV, new: {new}mV, corrfact: {corrfact}'.format(
                old=BinaryTools.unpack('uint16', x, 0x00),
                new=BinaryTools.unpack('uint16', x, 0x02),
                corrfact=BinaryTools.unpack('uint8', x, 0x04))
        }

    def bms_state(x):
        entering_hibernate = BinaryTools.unpack('bool', x, 0x0)
        return {
            'event': ('Entering' if entering_hibernate else 'Exiting') + ' Hibernate'
        }

    def bms_isolation_fault(x):
        return {
            'event': 'Chassis Isolation Fault',
            'conditions': '{ohms} ohms to cell {cell}'.format(
                ohms=BinaryTools.unpack('uint32', x, 0x00),
                cell=BinaryTools.unpack('uint8', x, 0x04))
        }

    def bms_reflash(x):

        return dict(event='BMS Reflash', conditions='Revision {rev}, ' 'Built {build}'.format(
            rev=BinaryTools.unpack('uint8', x, 0x00),
            build=BinaryTools.unpack_str(x, 0x01, 20)))

    def bms_change_can_id(x):
        return {
            'event': 'Changed CAN Node ID',
            'conditions': 'old: {old:02d}, new: {new:02d}'.format(
                old=BinaryTools.unpack('uint8', x, 0x00),
                new=BinaryTools.unpack('uint8', x, 0x01))
        }

    def bms_contactor_state(x):
        if BinaryTools.unpack('uint32', x, 0x01):
            prechg = trunc((BinaryTools.unpack('uint32', x, 0x05) * 1.0) / (
                    BinaryTools.unpack('uint32', x, 0x01) * 1.0) * 100)
        else:
            prechg = 0x0
        return {
            'event': '{state}'.format(
                state='Contactor was ' +
                      ('Closed' if BinaryTools.unpack('bool', x, 0x0) else 'Opened')),
            'conditions':
                'Pack V: {pv}mV, Switched V: {sv}mV, Prechg Pct: {pc:2.0f}%, Dischg Cur: {dc}mA'.format(
                    pv=BinaryTools.unpack('uint32', x, 0x01),
                    sv=BinaryTools.unpack('uint32', x, 0x05),
                    pc=prechg,
                    dc=BinaryTools.unpack('int32', x, 0x09))
        }

    def bms_discharge_cut(x):
        return {
            'event': 'Discharge cutback',
            'conditions': '{cut:2.0f}%'.format(
                cut=convert_ratio_to_percent(BinaryTools.unpack('uint8', x, 0x00), 255.0)
            )
        }

    def bms_contactor_drive(x):
        return {
            'event': 'Contactor drive turned on',
            'conditions': 'Pack V: {pv}mV, Switched V: {sv}mV, Duty Cycle: {dc}%'.format(
                pv=BinaryTools.unpack('uint32', x, 0x01),
                sv=BinaryTools.unpack('uint32', x, 0x05),
                dc=BinaryTools.unpack('uint8', x, 0x09))
        }

    def debug_message(x):
        return {
            'event': BinaryTools.unpack_str(x, 0x0, count=len(x) - 1)
        }

    def board_status(x):
        causes = {
            0x04: 'Software',
        }

        return {
            'event': 'BMS Reset',
            'conditions': causes.get(BinaryTools.unpack('uint8', x, 0x00),
                                     'Unknown')
        }

    def key_state(x):
        key_on = BinaryTools.unpack('bool', x, 0x0)

        return {
            'event': 'Key ' + convert_bit_to_on_off(key_on) + (' ' if key_on else '')
        }

    def battery_can_link_up(x):
        return {
            'event': 'Module {module:02} CAN Link Up'.format(
                module=BinaryTools.unpack('uint8', x, 0x0)
            )
        }

    def battery_can_link_down(x):
        return {
            'event': 'Module {module:02} CAN Link Down'.format(
                module=BinaryTools.unpack('uint8', x, 0x0)
            )
        }

    def sevcon_can_link_up(_):
        return {
            'event': 'Sevcon CAN Link Up'
        }

    def sevcon_can_link_down(x):
        return {
            'event': 'Sevcon CAN Link Down'
        }

    def run_status(x):
        mod_translate = {
            0x00: '00',
            0x01: '10',
            0x02: '01',
            0x03: '11',
        }

        return {
            'event': 'Riding',
            'conditions':
                'PackTemp: h {pack_temp_hi}C, l {pack_temp_low}C, PackSOC:{soc:3d}%, Vpack:{pack_voltage:7.3f}V, MotAmps:{motor_current:4d}, BattAmps:{battery_current:4d}, Mods: {mods}, MotTemp:{motor_temp:4d}C, CtrlTemp:{controller_temp:4d}C, AmbTemp:{ambient_temp:4d}C, MotRPM:{rpm:4d}, Odo:{odometer:5d}km'.format(
                    pack_temp_hi=BinaryTools.unpack('uint8', x, 0x0),
                    pack_temp_low=BinaryTools.unpack('uint8', x, 0x1),
                    soc=BinaryTools.unpack('uint16', x, 0x2),
                    pack_voltage=convert_mv_to_v(BinaryTools.unpack('uint32', x, 0x4)),
                    motor_temp=BinaryTools.unpack('int16', x, 0x8),
                    controller_temp=BinaryTools.unpack('int16', x, 0xa),
                    rpm=BinaryTools.unpack('uint16', x, 0xc),
                    battery_current=BinaryTools.unpack('int16', x, 0x10),
                    mods=mod_translate.get(BinaryTools.unpack('uint8', x, 0x12),
                                           'Unknown'),
                    motor_current=BinaryTools.unpack('int16', x, 0x13),
                    ambient_temp=BinaryTools.unpack('int16', x, 0x15),
                    odometer=BinaryTools.unpack('uint32', x, 0x17))
        }

    def charging_status(x):
        fields = {
            'pack_temp_hi': BinaryTools.unpack('uint8', x, 0x00),
            'pack_temp_low': BinaryTools.unpack('uint8', x, 0x01),
            'soc': BinaryTools.unpack('uint16', x, 0x02),
            'pack_voltage': convert_mv_to_v(BinaryTools.unpack('uint32', x, 0x4)),
            'battery_current': BinaryTools.unpack('int8', x, 0x08),
            'mods': BinaryTools.unpack('uint8', x, 0x0c),
            'ambient_temp': BinaryTools.unpack('int8', x, 0x0d),
        }

        return {
            'event': 'Charging',
            'conditions':
                'PackTemp: h {pack_temp_hi}C, l {pack_temp_low}C, AmbTemp: {ambient_temp}C, PackSOC:{soc:3d}%, Vpack:{pack_voltage:7.3f}V, BattAmps: {battery_current:3d}, Mods: {mods:02b}, MbbChgEn: Yes, BmsChgEn: No'.format(
                    pack_temp_hi=BinaryTools.unpack('uint8', x, 0x00),
                    pack_temp_low=BinaryTools.unpack('uint8', x, 0x01),
                    soc=BinaryTools.unpack('uint16', x, 0x02),
                    pack_voltage=convert_mv_to_v(BinaryTools.unpack('uint32', x, 0x4)),
                    battery_current=BinaryTools.unpack('int8', x, 0x08),
                    mods=BinaryTools.unpack('uint8', x, 0x0c),
                    ambient_temp=BinaryTools.unpack('int8', x, 0x0d))
        }

    def sevcon_status(x):
        cause = {
            0x4681: 'Preop',
            0x4884: 'Sequence Fault',
            0x4981: 'Throttle Fault',
        }

        return {
            'event': 'SEVCON CAN EMCY Frame',
            'conditions':
                'Error Code: 0x{code:04X}, Error Reg: 0x{reg:02X}, Sevcon Error Code: 0x{sevcon_code:04X}, Data: {data}, {cause}'.format(
                    code=BinaryTools.unpack('uint16', x, 0x00),
                    reg=BinaryTools.unpack('uint8', x, 0x04),
                    sevcon_code=BinaryTools.unpack('uint16', x, 0x02),
                    data=' '.join(['{:02X}'.format(c) for c in x[5:]]),
                    cause=cause.get(BinaryTools.unpack('uint16', x, 0x02), 'Unknown')
                )
        }

    def charger_status(x):
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

        charger_state = BinaryTools.unpack('uint8', x, 0x1)
        charger_id = BinaryTools.unpack('uint8', x, 0x0)
        return {
            'event': '{name} Charger {charger_id} {state:13s}'.format(
                charger_id=charger_id,
                state=states.get(charger_state),
                name=name.get(charger_id, 'Unknown')
            )
        }

    def battery_status(x):
        opening_contactor = 'Opening Contactor'
        closing_contactor = 'Closing Contactor'
        registered = 'Registered'
        events = {
            0x00: opening_contactor,
            0x01: closing_contactor,
            0x02: registered,
        }

        event = BinaryTools.unpack('uint8', x, 0x0)
        event_name = events.get(event, 'Unknown (0x{:02x})'.format(event))

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
        if event_name == opening_contactor:
            conditions_msg = 'vmod: {modvolt:7.3f}V, batt curr: {batcurr:3.0f}A'.format(
                modvolt=mod_volt,
                batcurr=battery_current
            )
        elif event_name == closing_contactor:
            conditions_msg = 'vmod: {modvolt:7.3f}V, maxsys: {sysmax:7.3f}V, minsys: {sysmin:7.3f}V, diff: {diff:0.03f}V, vcap: {vcap:6.3f}V, prechg: {prechg}%'.format(
                modvolt=mod_volt,
                sysmax=sys_max,
                sysmin=sys_min,
                vcap=capacitor_volt,
                batcurr=battery_current,
                serial=printable_serial_no,
                diff=sys_max - sys_min,
                prechg=int(convert_ratio_to_percent(capacitor_volt, mod_volt)))
        elif event_name == registered:
            conditions_msg = 'serial: {serial},  vmod: {modvolt:3.3f}V'.format(
                serial=printable_serial_no,
                modvolt=mod_volt
            )
        else:
            conditions_msg = ''

        return {
            'event': 'Module {module:02} {event}'.format(
                module=BinaryTools.unpack('uint8', x, 0x1),
                event=event_name
            ),
            'conditions': conditions_msg
        }

    def power_state(x):
        sources = {
            0x01: 'Key Switch',
            0x02: 'Ext Charger 0',
            0x03: 'Ext Charger 1',
            0x04: 'Onboard Charger',
        }

        power_on_cause = BinaryTools.unpack('uint8', x, 0x1)
        power_on = BinaryTools.unpack('bool', x, 0x0)

        return {
            'event': 'Power ' + convert_bit_to_on_off(power_on),
            'conditions': sources.get(power_on_cause, 'Unknown')
        }

    def sevcon_power_state(x):
        return {
            'event': 'Sevcon Turned ' + convert_bit_to_on_off(BinaryTools.unpack('bool', x, 0x0))
        }

    def show_bluetooth_state(x):
        return {
            'event': 'BT RX buffer reset'
        }

    def battery_discharge_current_limited(x):
        limit = BinaryTools.unpack('uint16', x, 0x00)
        max_amp = BinaryTools.unpack('uint16', x, 0x05)
        percent = convert_ratio_to_percent(limit, max_amp)

        return {
            'event': 'Batt Dischg Cur Limited',
            'conditions':
                '{limit} A ({percent}%), MinCell: {min_cell}mV, MaxPackTemp: {temp}C'.format(
                    limit=limit,
                    min_cell=BinaryTools.unpack('uint16', x, 0x02),
                    temp=BinaryTools.unpack('uint8', x, 0x04),
                    max_amp=max_amp,
                    percent=percent
                )
        }

    def low_chassis_isolation(x):
        return {
            'event': 'Low Chassis Isolation',
            'conditions': '{kohms} KOhms to cell {cell}'.format(
                kohms=BinaryTools.unpack('uint32', x, 0x00),
                cell=BinaryTools.unpack('uint8', x, 0x04)
            )
        }

    def precharge_decay_too_steep(x):
        return {
            'event': 'Precharge Decay Too Steep. Restarting Sevcon.'
        }

    def disarmed_status(x):
        return {
            'event': 'Disarmed',
            'conditions':
                'PackTemp: h {pack_temp_hi}C, l {pack_temp_low}C, PackSOC:{soc:3d}%, Vpack:{pack_voltage:03.3f}V, MotAmps:{motor_current:4d}, BattAmps:{battery_current:4d}, Mods: {mods:02b}, MotTemp:{motor_temp:4d}C, CtrlTemp:{controller_temp:4d}C, AmbTemp:{ambient_temp:4d}C, MotRPM:{rpm:4d}, Odo:{odometer:5d}km'.format(
                    pack_temp_hi=BinaryTools.unpack('uint8', x, 0x0),
                    pack_temp_low=BinaryTools.unpack('uint8', x, 0x1),
                    soc=BinaryTools.unpack('uint16', x, 0x2),
                    pack_voltage=convert_mv_to_v(BinaryTools.unpack('uint32', x, 0x4)),
                    motor_temp=BinaryTools.unpack('int16', x, 0x8),
                    controller_temp=BinaryTools.unpack('int16', x, 0xa),
                    rpm=BinaryTools.unpack('uint16', x, 0xc),
                    battery_current=BinaryTools.unpack('uint8', x, 0x10),
                    mods=BinaryTools.unpack('uint8', x, 0x12),
                    motor_current=BinaryTools.unpack('int8', x, 0x13),
                    ambient_temp=BinaryTools.unpack('int16', x, 0x15),
                    odometer=BinaryTools.unpack('uint32', x, 0x17))
        }

    def battery_contactor_closed(x):
        return {
            'event': 'Battery module {module:02} contactor closed'.format(
                module=BinaryTools.unpack('uint8', x, 0x0))
        }

    def unhandled_entry_format(x):
        return {
            'event': '{message_type} {message}'.format(
                message_type='0x{:02x}'.format(message_type),
                message=' '.join(['0x{:02x}'.format(c) for c in x])
            ),
            'conditions': chr(message_type) + '???'
        }

    parsers = {
        # Unknown entry types to be added when defined: type, length, source, example
        0x01: board_status,
        # 0x02: unknown, 2, 6350_MBB_2016-04-12, 0x02 0x2e 0x11 ???
        0x03: bms_discharge_level,
        0x04: bms_charge_full,
        # 0x05: unknown, 17, 6890_BMS0_2016-07-03, 0x05 0x34 0x0b 0xe0 0x0c 0x35 0x2a 0x89 0x71
        # 0xb5 0x01 0x00 0xa5 0x62 0x01 0x00 0x20 0x90 ???
        0x06: bms_discharge_low,
        0x08: bms_system_state,
        0x09: key_state,
        0x0b: bms_soc_adj_voltage,
        0x0d: bms_curr_sens_zero,
        # 0x0e: unknown, 3, 6350_BMS0_2017-01-30 0x0e 0x05 0x00 0xff ???
        0x10: bms_state,
        0x11: bms_isolation_fault,
        0x12: bms_reflash,
        0x13: bms_change_can_id,
        0x15: bms_contactor_state,
        0x16: bms_discharge_cut,
        0x18: bms_contactor_drive,
        # 0x1c: unknown, 8, 3455_MBB_2016-09-11, 0x1c 0xdf 0x56 0x01 0x00 0x00 0x00 0x30 0x02 ???
        # 0x1e: unknown, 4, 6472_MBB_2016-12-12, 0x1e 0x32 0x00 0x06 0x23 ???
        # 0x1f: unknown, 4, 5078_MBB_2017-01-20, 0x1f 0x00 0x00 0x08 0x43 ???
        # 0x20: unknown, 3, 6472_MBB_2016-12-12, 0x20 0x02 0x32 0x00 ???
        # 0x26: unknown, 6, 3455_MBB_2016-09-11, 0x26 0x72 0x00 0x40 0x00 0x80 0x00 ???
        0x28: battery_can_link_up,
        0x29: battery_can_link_down,
        0x2a: sevcon_can_link_up,
        0x2b: sevcon_can_link_down,
        0x2c: run_status,
        0x2d: charging_status,
        0x2f: sevcon_status,
        0x30: charger_status,
        # 0x31: unknown, 1, 6350_MBB_2016-04-12, 0x31 0x00 ???
        0x33: battery_status,
        0x34: power_state,
        # 0x35: unknown, 5, 6472_MBB_2016-12-12, 0x35 0x00 0x46 0x01 0xcb 0xff ???
        0x36: sevcon_power_state,
        # 0x37: unknown, 0, 3558_MBB_2016-12-25, 0x37  ???
        0x38: show_bluetooth_state,
        0x39: battery_discharge_current_limited,
        0x3a: low_chassis_isolation,
        0x3b: precharge_decay_too_steep,
        0x3c: disarmed_status,
        0x3d: battery_contactor_closed,
        0xfd: debug_message
    }
    entry_parser = parsers.get(message_type, unhandled_entry_format)

    try:
        entry = entry_parser(message)
    except Exception as e:
        entry = unhandled_entry_format(message)
        entry['event'] = 'Exception caught: ' + entry['event']
        unhandled += 1

    if timestamp > 0xfff:
        if USE_MBB_TIME:
            # The output from the MBB (via serial port) lists time as GMT-7
            entry['time'] = strftime(TIME_FORMAT,
                                     gmtime(timestamp - 7 * 60 * 60))
        else:
            entry['time'] = strftime(TIME_FORMAT, localtime(timestamp))
    else:
        entry['time'] = str(timestamp)

    return length, entry, unhandled


REV0 = 0
REV1 = 1
REV2 = 2


def is_vin(vin: str):
    """Whether the string matches a Zero VIN."""
    return (BinaryTools.is_printable(vin)
            and len(vin) == 17
            and vin.startswith('538'))


class LogData:
    log_type: str
    log_version: int
    header_info: Dict[str, str]
    entries_count: Optional[int]
    entries: [str]

    def __init__(self, log_file: LogFile):
        self.log_type = self.get_log_type(log_file)
        self.log_version, self.header_info = self.get_version_and_header(log_file)
        self.entries_count, self.entries = self.get_entries_and_counts(log_file)

    @classmethod
    def get_log_type(cls, log: LogFile):
        if log.is_printable(0x000, count=3):
            log_type = log.unpack_str(0x000, count=3)
        else:
            log_type = log.unpack_str(0x00d, count=3)
        if log_type not in ['MBB', 'BMS']:
            log_type = 'Unknown Type'
        return log_type

    def get_version_and_header(self, log: LogFile):
        sys_info = OrderedDict()
        log_version = REV0
        if self.log_type == 'MBB':
            # Check for log formats:
            vin_v0 = log.unpack_str(0x240, count=17)  # v0 (Gen2)
            vin_v1 = log.unpack_str(0x252, count=17)  # v1 (Gen2 2019+)
            vin_v2 = log.unpack_str(0x029, count=17)  # v2 (Gen3)
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
                print("Unknown Log Format")
                sys_info['VIN'] = vin_v0
                model_offset = 0x27f
            if 'VIN' not in sys_info or not BinaryTools.is_printable(sys_info['VIN']):
                print("VIN unreadable", sys_info['VIN'])
            sys_info['Model'] = log.unpack_str(model_offset, count=3)
            sys_info['Initial date'] = log.unpack_str(0x2a, count=20)
        elif self.log_type == 'BMS':
            # Check for two log formats:
            log_version_code = log.unpack('uint8', 0x4)
            if log_version_code == 0xb6:
                log_version = REV0
            elif log_version_code == 0xde:
                log_version = REV1
            else:
                print("Unknown Log Format", log_version_code)
            sys_info['Initial date'] = log.unpack_str(0x12, count=20)
            if log_version == REV0:
                sys_info['BMS serial number'] = log.unpack_str(0x300, count=21)
                sys_info['Pack serial number'] = log.unpack_str(0x320, count=8)
            elif log_version == REV1:
                # TODO identify BMS serial number
                sys_info['Pack serial number'] = log.unpack_str(0x331, count=8)
        elif self.log_type == 'Unknown Type':
            sys_info['System info'] = 'unknown'
        return log_version, sys_info

    def get_entries_and_counts(self, log: LogFile):
        raw_log = log.raw()
        if self.log_version < REV2:
            # handle missing header index
            try:
                entries_header_idx = log.index_of_sequence(b'\xa2\xa2\xa2\xa2')
                entries_end = log.unpack('uint32', 0x4, offset=entries_header_idx)
                entries_start = log.unpack('uint32', 0x8, offset=entries_header_idx)
                claimed_entries_count = log.unpack('uint32', 0xc, offset=entries_header_idx)
                entries_data_begin = entries_header_idx + 0x10
            except ValueError:
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

            print('{} entries found ({} claimed)'.format(entries_count, claimed_entries_count))
        elif self.log_version == REV2:
            entries_start = 0x0
            entries_data_begin = 0x0
            entries_end = len(raw_log)
            entries_count = 0
            # Handle data wrapping across the upper bound of the ring buffer
            if entries_start >= entries_end:
                event_log = raw_log[entries_start:] + \
                            raw_log[entries_data_begin:entries_end]
            else:
                event_log = raw_log[entries_start:entries_end]
        return entries_count, event_log

    header_divider = \
        '+--------+----------------------+--------------------------+----------------------------------\n'

    def emit_zero_compatible_decoding(self, output_file):
        with codecs.open(output_file, 'wb', 'utf-8-sig') as f:
            f.write('Zero ' + self.log_type + ' log\n')
            f.write('\n')

            for k, v in self.header_info.items():
                f.write('{0:18} {1}\n'.format(k, v))
            f.write('\n')

            f.write('Printing {0} of {0} log entries..\n'.format(self.entries_count))
            f.write('\n')
            f.write(' Entry    Time of Log            Event                      Conditions\n')
            f.write(self.header_divider)

            read_pos = 0
            unhandled = 0
            unknown_entries = 0
            unknown = []
            for entry_num in range(self.entries_count):
                (length, entry, unhandled) = parse_entry(self.entries, read_pos, unhandled)

                entry['line'] = entry_num + 1

                conditions = entry.get('conditions')
                if conditions:
                    if '???' in conditions:
                        u = conditions[0]
                        unknown_entries += 1
                        if u not in unknown:
                            unknown.append(u)
                        conditions = '???'
                        f.write(
                            ' {line:05d}     {time:>19s}   {event} {conditions}\n'.format(
                                **entry))
                    else:
                        f.write(
                            ' {line:05d}     {time:>19s}   {event:25}  {conditions}\n'.format(
                                **entry))
                else:
                    f.write(' {line:05d}     {time:>19s}   {event}\n'.format(**entry))

                read_pos += length

            f.write('\n')
        if unhandled > 0:
            print('{} exceptions in parser'.format(unhandled))
        if unknown:
            print('{} unknown entries of types {}'.format(unknown_entries,
                                                          ', '.join(
                                                              hex(ord(x)) for x in unknown),
                                                          '02x'))
        print('Saved to {}'.format(output_file))


def parse_log(bin_file: str, output_file: str):
    """
    Parse a Zero binary log file into a human readable text file
    """
    print('Parsing {}'.format(bin_file))

    log = LogFile(bin_file)
    log_data = LogData(log)

    log_data.emit_zero_compatible_decoding(output_file)


def default_parsed_output_for(bin_file_path: str):
    return os.path.splitext(bin_file_path)[0] + '.txt'


def is_log_file_path(file_path: str):
    return file_path.endswith('.bin')


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('bin_file', help='Zero *.bin log to decode')
    parser.add_argument('-o', '--output', help='decoded log filename')
    args = parser.parse_args()
    log_file = args.bin_file
    output_file = args.output or default_parsed_output_for(args.bin_file)
    parse_log(log_file, output_file)


if __name__ == '__main__':
    main()
