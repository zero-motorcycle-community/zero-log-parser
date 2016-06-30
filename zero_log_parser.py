#!/usr/bin/python

'''
Little decoder utility to parse Zero Motorcycle main bike board (MBB) and
battery management system (BMS) logs. These may be extracted from the bike
using the Zero mobile app. Once paired over bluetooth, select 'Support' >
'Email bike logs' and send the logs to yourself rather than / in addition to
zero support.

Usage:

   $ python zero_log_parser.py <*.bin file> [-o output_file]

'''

import argparse
import os
import mmap
import struct
import string
from time import localtime, strftime, gmtime
from collections import OrderedDict

TRADITIONAL_FORMAT = {
        'logline': '{entry:05d}  {time:19s}  {message}\n',
        'time_format': '%Y-%m-%d %H:%M:%S',
        'disarmed_format': 'Disarmed - pack: h {pack_temp_hi}C, l {pack_temp_low}C, {pack_voltage:03.3f}V, {soc}% SOC | motor: {motor_temp}C, {rpm}rpm | controller: {controller_temp}C, | power delivery: battery {battery_current}A, motor {motor_current}A',
        'riding_format': 'Riding - pack: h {pack_temp_hi}C, l {pack_temp_low}C, {pack_voltage:03.3f}V, {soc}% SOC | motor: {motor_temp}C, {rpm}rpm | controller: {controller_temp}C, | power delivery: battery {battery_current}A, motor {motor_current}A',
        'power_format': 'Power {state} ({source})',
    }

OFFICIAL_FORMAT = {
        'logline': ' {entry:05d}     {time:>19s}   {message}\n',
        'time_format': '%m/%d/%Y %H:%M:%S',
        'disarmed_format': 'Disarmed                   PackTemp: h {pack_temp_hi}C, l {pack_temp_low}C, PackSOC:{soc:3d}%, Vpack:{pack_voltage:03.3f}V, MotAmps:{motor_current:4d}, BattAmps:{battery_current:4d}, Mods: {mods:02b}, MotTemp:{motor_temp:4d}C, CtrlTemp:{controller_temp:4d}C, AmbTemp:{ambient_temp:4d}C, MotRPM:{rpm:4d}, Odo:{odometer:5d}km',
        'riding_format': 'Riding                     PackTemp: h {pack_temp_hi}C, l {pack_temp_low}C, PackSOC:{soc:3d}%, Vpack:{pack_voltage:7.3f}V, MotAmps:{motor_current:4d}, BattAmps:{battery_current:4d}, Mods: {mods}, MotTemp:{motor_temp:4d}C, CtrlTemp:{controller_temp:4d}C, AmbTemp:{ambient_temp:4d}C, MotRPM:{rpm:4d}, Odo:{odometer:5d}km',
        'power_format': 'Power {state:3s}                  {source}',
    }

FORMAT = OFFICIAL_FORMAT
USE_MBB_TIME = True

class BinaryTools:
    '''
    Utility class for dealing with serialised data from the Zero's
    '''

    TYPES = {
        'int8':     'b',
        'uint8':    'B',
        'int16':    'h',
        'uint16':   'H',
        'int32':    'i',
        'uint32':   'I',
        'int64':    'q',
        'uint64':   'Q',
        'float':    'f',
        'double':   'd',
        'char':     's',
        'bool':     '?'
    }

    @staticmethod
    def unpack(type_name, buff, address, count=1):
        type_char = BinaryTools.TYPES[type_name.lower()]
        type_format = '<{}{}'.format(count, type_char)
        return struct.unpack_from(type_format, buff, address)[0]


class LogFile:
    '''
    Wrapper for our raw log file
    '''

    def __init__(self, file_path):
        with open(file_path, 'rb') as f:
            self._data = bytearray(f.read())

    def unescape_block(self, data):
        start_offset = 0

        escape_offset = data.find('\xfe')

        while escape_offset != -1:
            escape_offset += start_offset
            data[escape_offset] = data[escape_offset] ^ data[escape_offset+1] - 1
            data = data[0:escape_offset+1] + data[escape_offset + 2:]
            start_offset = escape_offset + 1
            escape_offset = data[start_offset:].find('\xfe')

        return data

    def index(self, sequence):
        return self._data.index(sequence)

    def unpack(self, type_name, address, count=1, offset=0):
        return BinaryTools.unpack(type_name, self._data, address + offset,
                                  count=count)

    def extract(self, start_address, length, offset=0):
        return self._data[start_address+offset:start_address+length+offset]


def parse_entry(log, address, entry_num):
    '''
    Parse an individual entry from a LogFile into a human readable form
    '''
    header = log.unpack('char', 0x0, offset=address)
    if header != b'\xb2':
        raise ValueError('Invalid entry header byte')

    length = log.unpack('uint8', 0x1, offset=address)

    unescaped_block = log.unescape_block(log.extract(0x02, length-2, offset=address))

    message_type = BinaryTools.unpack('uint8', unescaped_block, 0x00)
    timestamp = BinaryTools.unpack('uint32', unescaped_block, 0x01)
    message = unescaped_block[0x05:]

    def debug_message(x):
        return BinaryTools.unpack('char', x, 0x0, count=len(x) - 1)

    def board_status(x):
        fields = {
            'state': BinaryTools.unpack('uint8', x, 0x00)
        }

        condition = 'Unknown'
        if fields['state'] == 0x04:
            condition = 'Software'

        return 'Board Reset                {0}'.format(condition)

    def key_state(x):
        fields = {
            'state': 'On ' if BinaryTools.unpack('bool', x, 0x0) else 'Off'
        }
        return 'Key {state}'.format(**fields)

    def battery_can_link_up(x):
        fields = {
            'module': BinaryTools.unpack('uint8', x, 0x0)
        }
        return 'Module {module:02} CAN Link Up'.format(**fields)

    def battery_can_link_down(x):
        fields = {
            'module': BinaryTools.unpack('uint8', x, 0x0)
        }
        return 'Module {module:02} CAN Link Down'.format(**fields)

    def sevcon_can_link_up(x):
        return 'Sevcon CAN Link Up'

    def sevcon_can_link_down(x):
        return 'Sevcon CAN Link Down'

    def run_status(x):
        mod_translate = {
            0x00: '00',
            0x01: '10',
            0x02: '01',
            0x03: '11',
        }

        fields = {
            'pack_temp_hi': BinaryTools.unpack('uint8', x, 0x0),
            'pack_temp_low': BinaryTools.unpack('uint8', x, 0x1),
            'soc': BinaryTools.unpack('uint16', x, 0x2),
            'pack_voltage': BinaryTools.unpack('uint32', x, 0x4) / 1000.0,
            'motor_temp': BinaryTools.unpack('int16', x, 0x8),
            'controller_temp': BinaryTools.unpack('int16', x, 0xa),
            'rpm': BinaryTools.unpack('uint16', x, 0xc),
            'battery_current': BinaryTools.unpack('int16', x, 0x10),
            'mods': mod_translate.get(BinaryTools.unpack('uint8', x, 0x12), "Unknown"),
            'motor_current': BinaryTools.unpack('int16', x, 0x13),
            'ambient_temp': BinaryTools.unpack('int16', x, 0x15),
            'odometer': BinaryTools.unpack('uint32', x, 0x17),
        }
        return FORMAT['riding_format'].format(**fields)

    def calex_status(x):
        states = {
            0x00: 'Disconnected',
            0x01: 'Connected',
        }

        size = {
            0x00: "720W",
            0x01: "1200W",
        }
        module_name = {
            0x00: 'Calex 720W',
            0x01: 'Calex 1200W',
            0x02: 'External Chg 0',
            0x03: 'External Chg 1',
        }

        fields = {
            'module': BinaryTools.unpack('uint8', x, 0x0),
            'state': states.get(BinaryTools.unpack('uint8', x, 0x1)),
            'module_name': module_name.get(BinaryTools.unpack('uint8', x, 0x0), "Unknown")
        }

        return '{module_name} Charger {module} {state:13s}'.format(**fields)

    def charging_status(x):
#        s = "XXXXXXXXXXX %x" % (message_type) + " " + ' '.join(['0x{:02x}'.format(c) for c in x])
        fields = {
            'pack_temp_hi': BinaryTools.unpack('uint8', x, 0x00),
            'pack_temp_low': BinaryTools.unpack('uint8', x, 0x01),
            'soc': BinaryTools.unpack('uint16', x, 0x02),
            'pack_voltage': BinaryTools.unpack('uint32', x, 0x4) / 1000.0,
            'battery_current': BinaryTools.unpack('int8', x, 0x08),
            'mods': BinaryTools.unpack('uint8', x, 0x0c),
            'ambient_temp': BinaryTools.unpack('int8', x, 0x0d),
        }

        return 'Charging                   PackTemp: h {pack_temp_hi}C, l {pack_temp_low}C, AmbTemp: {ambient_temp}C, PackSOC:{soc:3d}%, Vpack:{pack_voltage:7.3f}V, BattAmps: {battery_current:3d}, Mods: {mods:02b}, MbbChgEn: Yes, BmsChgEn: No'.format(**fields)

    def sevcon_status(x):
        cause = {
            0x4681: "Preop",
            0x4884: "Sequence Fault",
            0x4981: "Throttle Fault",
        }

        fields = {
            'code': BinaryTools.unpack('uint16', x, 0x00),
            'reg': BinaryTools.unpack('uint8', x, 0x04),
            'sevcon_code': BinaryTools.unpack('uint16', x, 0x02),
            'data': ' '.join(['{:02X}'.format(c) for c in x[5:]]),
            'cause': cause.get(BinaryTools.unpack('uint16', x, 0x02), "Unknown!"),
        }

        return 'SEVCON CAN EMCY Frame      Error Code: 0x{code:04X}, Error Reg: 0x{reg:02X}, Sevcon Error Code: 0x{sevcon_code:04X}, Data: {data}, {cause}'.format(**fields)

    def battery_status(x):
        states = {
            0x00: 'Opening Contactor',
            0x01: 'Closing Contactor',
            0x02: 'Registered'
        }

        fields = {
            'state': states.get(BinaryTools.unpack('uint8', x, 0x0), "Unknown!"),
            'module': BinaryTools.unpack('uint8', x, 0x1),
            'modvolt': BinaryTools.unpack('uint32', x, 0x2) / 1000.0,
            'sysmax': BinaryTools.unpack('uint32', x, 0x6) / 1000.0,
            'sysmin': BinaryTools.unpack('uint32', x, 0xa) / 1000.0,
            'vcap': BinaryTools.unpack('uint32', x, 0x0e) / 1000.0,
            'batcurr': BinaryTools.unpack('int16', x, 0x12),
            'serial': BinaryTools.unpack('char', x, 0x14, count=len(x[0x14:])),
        }

        # Ensuring the serial is printable
        fields['serial'] = filter(lambda x: x in string.printable, fields['serial'])

        if BinaryTools.unpack('uint8', x, 0x0) == 0x00:
            return 'Module {module:02} {state}  vmod: {modvolt:7.3f}V, batt curr: {batcurr:3.0f}A'.format(**fields)
        elif BinaryTools.unpack('uint8', x, 0x00) == 0x01:
            fields['diff'] = fields['sysmax'] - fields['sysmin']
            fields['prechg'] = int(fields['vcap'] * 100 / fields['modvolt'])
            return 'Module {module:02} {state}  vmod: {modvolt:7.3f}V, maxsys: {sysmax:7.3f}V, minsys: {sysmin:7.3f}V, diff: {diff:0.03f}V, vcap: {vcap:6.3f}V, prechg: {prechg}%'.format(**fields)
        else:
            return 'Module {module:02} {state}     serial: {serial},  vmod: {modvolt:3.3f}V'.format(**fields)

    def power_state(x):
        sources = {
            0x01: 'Key Switch',
            0x03: 'Ext Charger 1',
            0x04: 'Onboard Charger',
        }

        fields = {
            'state': 'On' if BinaryTools.unpack('bool', x, 0x0) else 'Off',
            'source': sources.get(BinaryTools.unpack('uint8', x, 0x1), 'Unknown')
        }

        return FORMAT['power_format'].format(**fields)

    def sevcon_power_state(x):
        is_on = BinaryTools.unpack('bool', x, 0x0)
        return 'Sevcon {}'.format('Turned On' if is_on else 'Turned Off')

    def show_bluetooth_state(x):
        return 'BT RX buffer reset'

    def battery_discharge_current_limited(x):
#        return "XXXXXXXXXXX %x" % (message_type) + " " + ' '.join(['0x{:02x}'.format(c) for c in x])
        fields = {
            'limit': BinaryTools.unpack('uint16', x, 0x00),
            'min_cell': BinaryTools.unpack('uint16', x, 0x02),
            'temp': BinaryTools.unpack('uint8', x, 0x04),
            'max_amp': BinaryTools.unpack('uint16', x, 0x05),
        }

        fields['percent'] = fields['limit'] * 100 / fields['max_amp']

        return 'Batt Dischg Cur Limited    {limit} A ({percent}%), MinCell: {min_cell}mV, MaxPackTemp: {temp}C'.format(**fields)

    def low_chassis_isolation(x):
        fields = {
            'kohms': BinaryTools.unpack('uint32', x, 0x00),
            'cell': BinaryTools.unpack('uint8', x, 0x04),
        }
        return 'Low Chassis Isolation      {kohms} KOhms to cell {cell}'.format(**fields)

    def precharge_decay_too_steep(x):
        return 'Precharge Decay Too Steep. Restarting Sevcon.'

    def disarmed_status(x):
        fields = {
            'pack_temp_hi': BinaryTools.unpack('uint8', x, 0x0),
            'pack_temp_low': BinaryTools.unpack('uint8', x, 0x1),
            'soc': BinaryTools.unpack('uint16', x, 0x2),
            'pack_voltage': BinaryTools.unpack('uint32', x, 0x4) / 1000.0,
            'motor_temp': BinaryTools.unpack('int16', x, 0x8),
            'controller_temp': BinaryTools.unpack('int16', x, 0xa),
            'rpm': BinaryTools.unpack('uint16', x, 0xc),
            'battery_current': BinaryTools.unpack('uint8', x, 0x10),
            'mods': BinaryTools.unpack('uint8', x, 0x12),
            'motor_current': BinaryTools.unpack('int8', x, 0x13),
            'ambient_temp': BinaryTools.unpack('int16', x, 0x15),
            'odometer': BinaryTools.unpack('uint32', x, 0x17),
        }
        return FORMAT['disarmed_format'].format(**fields)

    def battery_contactor_closed(x):
        module = BinaryTools.unpack('uint8', x, 0x0)
        return 'Battery module {:02} contactor closed'.format(module)

    def unhandled_entry_format(x):
        return "(%x) " % (message_type) + ' '.join(['0x{:02x}'.format(c) for c in x])

    parsers = {
        0x01: board_status,
        0x09: key_state,
        0x28: battery_can_link_up,
        0x29: battery_can_link_down,
        0x2a: sevcon_can_link_up,
        0x2b: sevcon_can_link_down,
        0x2c: run_status,
        0x2d: charging_status,
        0x2f: sevcon_status,
        0x30: calex_status,
        0x33: battery_status,
        0x34: power_state,
        0x36: sevcon_power_state,
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
        entry = {
            'message': entry_parser(message)
        }
    except:
        entry = {
            'message': "Exception caught: " + unhandled_entry_format(message)
        }

    if timestamp > 0xfff:
        if USE_MBB_TIME:
            # The output from the MBB (via serial port) lists time as GMT-7, so we should adjust
            entry['time'] = strftime(FORMAT['time_format'], gmtime(timestamp - 7*60*60))
        else:
            entry['time'] = strftime(FORMAT['time_format'], localtime(timestamp))
    else:
        entry['time'] = str(timestamp)

    return (length, entry)


def parse_log(bin_file, output):
    '''
    Parse a Zero binary log file into a human readable text file
    '''
    print('Parsing {}...'.format(bin_file))

    log = LogFile(bin_file)

    sys_info = OrderedDict()
    sys_info['Serial number'] = log.unpack('char', 0x200, count=21)
    sys_info['VIN'] = log.unpack('char', 0x240, count=17)
    sys_info['Firmware rev.'] = log.unpack('uint16', 0x27b)
    sys_info['Board rev.'] = log.unpack('uint16', 0x27d)
    sys_info['Model'] = log.unpack('char', 0x27f, count=3)

    entries_header_idx = log.index(b'\xa2\xa2\xa2\xa2')
    entries_end = log.unpack('uint32', 0x4, offset=entries_header_idx)
    entries_start = log.unpack('uint32', 0x8, offset=entries_header_idx)
    entries_count = log.unpack('uint32', 0xc, offset=entries_header_idx)

    print('{} entries found'.format(entries_count))

    with open(output, 'w') as f:
        f.write('Zero MBB log\n')
        f.write('\n')

        for k, v in sys_info.items():
            f.write('{0:18} {1}\n'.format(k, v))
        f.write('\n')

        # This conforms to the output of the MBB log
        f.write('Printing {0} of {0} log entries..\n'.format(entries_count))
        f.write('\n')
        f.write(' Entry    Time of Log            Event                      Conditions\n')
        f.write('+--------+----------------------+--------------------------+----------------------------------\n')

        read_pos = entries_start
        for entry_num in range(entries_count):
            (length, entry) = parse_entry(log, read_pos, entry_num+1)

            fields = {
                'entry': entry_num + 1,
                'time': entry['time'],
                'message': entry['message']
            }
            f.write(FORMAT['logline'].format(**fields))

            read_pos += length

        f.write('\n')

    print('Saved to {}'.format(output_file))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('bin_file', help='Zero *.bin log to decode')
    parser.add_argument('-o', '--output', help='decoded log filename')
    args = parser.parse_args()

    log_file = args.bin_file
    if args.output:
        output_file = args.output
    else:
        output_file = os.path.splitext(args.bin_file)[0] + '.txt'

    parse_log(log_file, output_file)
