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
    replace = args.replace
    output_suffix = '.txt' if replace else '.new.txt'
    for dir_path, _, filenames in os.walk(log_dir):
        for filename in filenames:
            if zero_log_parser.is_log_file_path(filename):
                logfile_path = os.path.join(dir_path, filename)
                new_output = os.path.splitext(logfile_path)[0] + output_suffix
                logger = zero_log_parser.console_logger(filename, verbose=True)
                zero_log_parser.parse_log(logfile_path, new_output, logger=logger)


if __name__ == '__main__':
    main()
