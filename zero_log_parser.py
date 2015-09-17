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
import codecs


def bytes_from_file(filename, chunksize=8192):
    '''
    Generator for performing a chunked read from a binary file
    '''
    with open(filename, 'rb') as f:
        while True:
            chunk = f.read(chunksize)
            if chunk:
                for b in chunk:
                    yield b
            else:
                break


def parse_log_entry(bytes):
    '''
    Parse a list of bytes associated with an individual line / log entry into a
    human readable string.
    '''
    header = bytes[:6]
    msg = bytes[6:-1]
    footer = bytes[:-1]

    humanised_header = ' '.join([format(ord(c), '02x') for c in header])
    humanised_message = ''.join(msg).decode('utf-8', 'replace')

    return humanised_header + '   ' + humanised_message


def parse_log(bin_file, output):
    '''
    Parse a Zero binary log file into a human readable text file.
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
