import os
import sys
import unittest
import argparse
from ddt import ddt, data, file_data, unpack

log_filenames = None

class TestLogParser(unittest.TestCase):
    pass

arg_parser = argparse.ArgumentParser(description='Run the log parser against a directory of logfiles.')
arg_parser.add_argument('logdir', help='directory where log files to test are')
arg_parser.add_argument('unittest_args', nargs='*')

if __name__ == '__main__':
    args = arg_parser.parse_args()
    log_filenames = [logfile for logfile in os.listdir(args.logdir) if logfile.endswith('.bin')]
    # Ensure unittest doesn't interpret logdir
    sys.argv[1:] = args.unittest_args
    unittest.main()
