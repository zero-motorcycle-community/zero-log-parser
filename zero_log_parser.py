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
    def unpack(type_name, buff, address, length=1):
        type_char = BinaryTools.TYPES[type_name.lower()]
        type_format = '<{}{}'.format(length, type_char)
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

    def unpack(self, type_name, address, length=1, offset=0):
        return BinaryTools.unpack(type_name, self._data, address + offset,
                                  length=length)

    def extract(self, start_address, end_address):
        return self._data[start_address: end_address]


def parse_entry(raw_entry):
    '''
    Parse an individual entry from a LogFile into a human readable form
    '''
    length = BinaryTools.unpack('uint8', raw_entry, 0x0)

    entry_type = BinaryTools.unpack('uint8', raw_entry, 0x1)

    timestamp = BinaryTools.unpack('uint32', raw_entry, 0x2)
    # There's some odd entries in our time field occasional, filter them
    entry_time = localtime(timestamp) if timestamp > 0xfff else None

    entry_data = raw_entry[0x6:length]

    if entry_type == 0xfd:
        message = str(entry_data[:-1])
    else:
        message = ' '.join(['0x{:02x}'.format(c) for c in entry_data])

    return (entry_time, message)


def parse_log(bin_file, output):
    '''
    Parse a Zero binary log file into a human readable text file
    '''
    print 'Parsing {}...'.format(bin_file)

    log = LogFile(bin_file)

    sys_info = OrderedDict()
    sys_info['Serial number'] = log.unpack('char', 0x200, length=21)
    sys_info['VIN'] = log.unpack('char', 0x240, length=17)
    sys_info['Firmware rev.'] = log.unpack('uint16', 0x27b)
    sys_info['Board rev.'] = log.unpack('uint16', 0x27d)

    entries_header_idx = log.index(b'\xa2\xa2\xa2\xa2')
    entries_end = log.unpack('uint32', 0x4, offset=entries_header_idx)
    entries_start = log.unpack('uint32', 0x8, offset=entries_header_idx)
    entries_count = log.unpack('uint32', 0xc, offset=entries_header_idx)
    raw_entries = log.extract(entries_start+1, entries_end).split(b'\xb2')
    entries = map(parse_entry, raw_entries)

    print '{} entries found'.format(entries_count)

    with open(output, 'w') as f:
        f.write('Zero MBB log\n')
        f.write('\n')

        for k, v in sys_info.items():
            f.write('{0:18} {1}\n'.format(k, v))
        f.write('\n')
        f.write('---\n')
        f.write('\n')

        for item, entry in enumerate(entries):
            fields = {
                'line': item + 1,
                'time': strftime('[%Y-%m-%d %H:%M:%S]', entry[0]) if entry[0]
                else '',
                'message': entry[1]
            }
            f.write('{line:05d} {time:19s} {message}\n'.format(**fields))

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
