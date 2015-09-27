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
        'char':     's'
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

    entry_type = log.unpack('uint8', 0x2, offset=address)

    timestamp = log.unpack('uint32', 0x3, offset=address)
    # There's some odd entries in our time field occasional, filter them
    entry_time = localtime(timestamp) if timestamp > 0xfff else None

    entry_data = log.extract(0x7, length - 0x7, offset=address)

    if entry_type == 0xfd:
        message = BinaryTools.unpack('char', entry_data, 0x0,
                                     count=len(entry_data) - 1)
    else:
        message = ' '.join(['0x{:02x}'.format(c) for c in entry_data])

    entry = {
        'time':     entry_time,
        'message':  message
    }

    return (length, entry)


def parse_log(bin_file, output):
    '''
    Parse a Zero binary log file into a human readable text file
    '''
    print 'Parsing {}...'.format(bin_file)

    log = LogFile(bin_file)

    sys_info = OrderedDict()
    sys_info['Serial number'] = log.unpack('char', 0x200, count=21)
    sys_info['VIN'] = log.unpack('char', 0x240, count=17)
    sys_info['Firmware rev.'] = log.unpack('uint16', 0x27b)
    sys_info['Board rev.'] = log.unpack('uint16', 0x27d)

    entries_header_idx = log.index(b'\xa2\xa2\xa2\xa2')
    entries_end = log.unpack('uint32', 0x4, offset=entries_header_idx)
    entries_start = log.unpack('uint32', 0x8, offset=entries_header_idx)
    entries_count = log.unpack('uint32', 0xc, offset=entries_header_idx)

    print '{} entries found'.format(entries_count)

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

    print 'Saved to {}'.format(output_file)


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
