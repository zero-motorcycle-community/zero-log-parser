import io
import os
import unittest
import shutil
import tempfile

import zero_log_parser


def main():
    import sys
    import argparse
    arg_parser = argparse.ArgumentParser(
        description='Run the log parser against a log file or directory of log files.')
    arg_parser.add_argument('log_dir', help='directory of log files to parse into new output')
    arg_parser.add_argument('--replace', action='store_true',
                            help='whether to replace old outputs')
    args = arg_parser.parse_args()
    log_dir = args.log_dir
    if not os.path.isdir(log_dir):
        print("Not a directory: " + log_dir)
        sys.exit(1)
    for log_file in os.listdir(log_dir):
        if zero_log_parser.is_log_file_path(log_file):
            logfile_path = os.path.join(log_dir, log_file)
            new_output = os.path.splitext(logfile_path)[0] + '.new.txt'
            zero_log_parser.parse_log(logfile_path, new_output)


if __name__ == '__main__':
    main()
