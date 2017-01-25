import os
import sys
import unittest
import argparse
from ddt import ddt, data, file_data, unpack
import shutil, tempfile

import zero_log_parser

logfiles_to_test = None

def logfile_test_generator(logfile):
    def test(self):
        self._test_can_process_logfile(logfile)
    return test

class TestLogParser(unittest.TestCase):
    def setUp(self):
        # Create a temporary directory
        self.test_dir = tempfile.mkdtemp()

    def tearDown(self):
        # Remove the directory after the test
        shutil.rmtree(self.test_dir)

    def _test_can_process_logfile(self, logfile):
        output_file = os.path.join(self.test_dir, 'log_output.txt')
        zero_log_parser.parse_log(logfile, output_file)

arg_parser = argparse.ArgumentParser(description='Run the log parser against a directory of logfiles.')
arg_parser.add_argument('logdir', help='directory where log files to test are')
arg_parser.add_argument('unittest_args', nargs='*')

if __name__ == '__main__':
    args = arg_parser.parse_args()
    logfiles_to_test = [logfile for logfile in
                        os.listdir(args.logdir) if logfile.endswith('.bin')]
    for logfile in logfiles_to_test:
        test_name = 'test_%s' % logfile
        logfile_path = os.path.join(args.logdir, logfile)
        test = logfile_test_generator(logfile_path)
        setattr(TestLogParser, test_name, test)
    # Ensure unittest doesn't interpret logdir
    sys.argv[1:] = args.unittest_args
    unittest.main()
