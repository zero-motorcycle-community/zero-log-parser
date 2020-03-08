import os
import sys
import unittest
import argparse
import shutil, tempfile

import zero_log_parser

log_files_to_test = None


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


if __name__ == '__main__':
    arg_parser = argparse.ArgumentParser(
        description='Run the log parser against a directory of log files.')
    arg_parser.add_argument('log_dir', help='directory where log files to test are')
    arg_parser.add_argument('unittest_args', nargs='*')
    args = arg_parser.parse_args()
    LOG_DIR = args.log_dir
    log_files_to_test = [log_file for log_file in
                         os.listdir(LOG_DIR) if log_file.endswith('.bin')]
    for log_file in log_files_to_test:
        test_name = 'test_%s' % log_file
        logfile_path = os.path.join(LOG_DIR, log_file)
        test = logfile_test_generator(logfile_path)
        setattr(TestLogParser, test_name, test)
    # Ensure unittest doesn't interpret log_dir
    sys.argv[1:] = args.unittest_args
    unittest.main()
