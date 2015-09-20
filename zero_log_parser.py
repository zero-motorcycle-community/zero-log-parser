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


class LogFile:
    '''
    Wrapper for our raw log file
    '''

    def __init__(self, file_path):
        with open(file_path, 'rb') as f:
            m = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
            self._data = bytearray(m)

    def get_bytes(self, address, length):
        return self._data[address:address+length]

    def get_byte(self, address):
        return self._data[address]

    def get_short(self, address):
        return struct.unpack_from('<H', self._data, address)[0]

    def get_long(self, address):
        return struct.unpack_from('<L', self._data, address)[0]

    def get_string(self, address, length):
        return struct.unpack_from('{}s'.format(length), self._data, address)[0]


def parse_log_entry(bytes):
    '''
    Parse a list of bytes associated with an individual line / log entry into a
    human readable string
    '''
    header = bytes[:6]
    msg = bytes[6:-1]
    footer = bytes[:-1]

    humanised_header = ' '.join([format(ord(c), '02x') for c in header])
    humanised_message = ''.join(msg).decode('utf-8', 'replace')

    return humanised_header + '   ' + humanised_message


def parse_log(bin_file, output):
    '''
    Parse a Zero binary log file into a human readable text file
    '''
    log = LogFile(bin_file)

    sys_info = {
        'serial': log.get_string(0x200, 21),
        'vin': log.get_string(0x240, 17),
        'firmware': log.get_short(0x27b),
        'board_rev': log.get_short(0x27d)
    }

    num_entries = log.get_short(0x60c)

    with open(output, 'w') as f:
        f.write('# Zero MBB log\n')
        f.write('\n')
        f.write('Serial number:    {serial}\n'.format(**sys_info))
        f.write('VIN:              {vin}\n'.format(**sys_info))
        f.write('Firmware Rev.:    {firmware}\n'.format(**sys_info))
        f.write('Board Rev.:       {board_rev}\n'.format(**sys_info))
        f.write('\n')
        f.write('---')
        f.write('\n')

    print 'Saved to {}'.format(output_file)


    '''
    ENTRY_DELIMITER = 0xb2

    buff = []

    with codecs.open(output, 'w', 'utf-8-sig') as f:
        for b in bytes_from_file(bin_file):
            buff.append(b)

            if ord(b) == ENTRY_DELIMITER:
                print(parse_log_entry(buff))
                f.write(parse_log_entry(buff) + '\n')
                buff = []
    '''


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
