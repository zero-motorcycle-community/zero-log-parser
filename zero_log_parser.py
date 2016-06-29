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
from time import localtime, strftime
from collections import OrderedDict


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
            m = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
            self._data = bytearray(m)

    def index(self, sequence):
        return self._data.index(sequence)

    def unpack(self, type_name, address, count=1, offset=0):
        return BinaryTools.unpack(type_name, self._data, address + offset,
                                  count=count)

    def extract(self, start_address, length, offset=0):
        return self._data[start_address+offset:start_address+length+offset]


def parse_entry(log, address):
    '''
    Parse an individual entry from a LogFile into a human readable form
    '''
    header = log.unpack('char', 0x0, offset=address)
    if header != b'\xb2':
        raise ValueError('Invalid entry header byte')
    length = log.unpack('uint8', 0x1, offset=address)
    message_type = log.unpack('uint8', 0x2, offset=address)
    timestamp = log.unpack('uint32', 0x3, offset=address)
    message = log.extract(0x7, length - 0x7, offset=address)

    def debug_message(x):
        return BinaryTools.unpack('char', x, 0x0, count=len(x) - 1)

    def key_state(x):
        fields = {
            'state': 'on' if BinaryTools.unpack('bool', x, 0x0) else 'off'
        }
        return 'Key {state}'.format(**fields)

    def battery_can_link_up(x):
        fields = {
            'module': BinaryTools.unpack('uint8', x, 0x0)
        }
        return 'Module {module:02} CAN link up'.format(**fields)

    def sevcon_can_link_up(x):
        return 'Sevcon CAN link up'

    def can_ack_error(x):
        return 'CAN ACK error'

    def run_status(x):
        fields = {
            'pack_temp_hi': BinaryTools.unpack('uint8', x, 0x0),
            'pack_temp_low': BinaryTools.unpack('uint8', x, 0x1),
            'soc': BinaryTools.unpack('uint16', x, 0x2),
            'pack_voltage': BinaryTools.unpack('uint32', x, 0x4) / 1000.0,
            'motor_temp': BinaryTools.unpack('uint8', x, 0x8),
            'controller_temp': BinaryTools.unpack('uint8', x, 0xa),
            'rpm': BinaryTools.unpack('uint16', x, 0xc),
            'battery_current': BinaryTools.unpack('uint8', x, 0x10),
            'motor_current': BinaryTools.unpack('uint8', x, 0x13),
        }
        return 'Riding - ' \
            'pack: h {pack_temp_hi}C, l {pack_temp_low}C, {pack_voltage:03.3f}V, {soc}% SOC | ' \
            'motor: {motor_temp}C, {rpm}rpm | '\
            'controller: {controller_temp}C, | '\
            'power delivery: battery {battery_current}A, motor {motor_current}A'.format(**fields)

    def battery_status(x):
        states = {
            0x00: 'disconnecting',
            0x01: 'connecting',
            0x02: 'registered'
        }

        fields = {
            'state': states.get(BinaryTools.unpack('uint8', x, 0x0)),
            'module': BinaryTools.unpack('uint8', x, 0x1),
            'modvolt': BinaryTools.unpack('uint32', x, 0x2) / 1000.0,
            'sysmax': BinaryTools.unpack('uint32', x, 0x6) / 1000.0,
            'sysmin': BinaryTools.unpack('uint32', x, 0xa) / 1000.0
        }
        return 'Battery module {module:02} {state} ('\
            'module: {modvolt:03.3f}V, '\
            'system max: {sysmax:03.3f}V, '\
            'system min: {sysmin:03.3f}V'\
            ')'.format(**fields)

    def power_state(x):
        sources = {
            0x01: 'key switch',
            0x04: 'onboard charger'
        }

        fields = {
            'state': 'on' if BinaryTools.unpack('bool', x, 0x0) else 'off',
            'source': sources.get(BinaryTools.unpack('uint8', x, 0x1))
        }

        return 'Power {state} ({source})'.format(**fields)

    def sevcon_power_state(x):
        is_on = BinaryTools.unpack('bool', x, 0x0)
        return 'Sevcon power {}'.format('on' if is_on else 'off')

    def battery_discharge_current_limited(x):
        limit = BinaryTools.unpack('uint16', x, 0x0)
        return 'Battery discharge current limited to {}A'.format(limit)

    def battery_contactor_closed(x):
        module = BinaryTools.unpack('uint8', x, 0x0)
        return 'Battery module {:02} contactor closed'.format(module)

    def unhandled_entry_format(x):
        return ' '.join(['0x{:02x}'.format(c) for c in x])

    parsers = {
        0x09: key_state,
        0x28: battery_can_link_up,
        0x2a: sevcon_can_link_up,
        0x2b: can_ack_error,
        0x2c: run_status,
        0x33: battery_status,
        0x34: power_state,
        0x36: sevcon_power_state,
        0x39: battery_discharge_current_limited,
        0x3d: battery_contactor_closed,
        0xfd: debug_message
    }
    entry_parser = parsers.get(message_type, unhandled_entry_format)

    entry = {
        # There's some odd entries in our time field occasional, filter them
        'time': localtime(timestamp) if timestamp > 0xfff else None,
        'message': entry_parser(message)
    }

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
        f.write('---\n')
        f.write('\n')

        read_pos = entries_start
        for entry_num in range(entries_count):
            (length, entry) = parse_entry(log, read_pos)

            fields = {
                'entry': entry_num + 1,
                'time': strftime('%Y-%m-%d %H:%M:%S', entry['time']) if entry['time'] else '',
                'message': entry['message']
            }
            f.write('{entry:05d}  {time:19s}  {message}\n'.format(**fields))

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
