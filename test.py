import io
import os
import unittest
import shutil
import tempfile

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

    @classmethod
    def lineIsError(cls, line: str):
        return 'Exception caught:' in line

    def assertFileContentsMatch(self, expected_path: str, actual_path: str, full_diff=False):
        with io.open(expected_path, 'r') as expected_file:
            expected_lines = list(expected_file)
        with io.open(actual_path, 'r') as actual_file:
            actual_lines = list(actual_file)
        if full_diff:
            self.assertListEqual(expected_lines, actual_lines)
        else:
            expected = []
            actual = []
            for expected_line, actual_line in zip(expected_lines, actual_lines):
                if not self.lineIsError(expected_line):
                    if expected_line != actual_line:
                        expected.append(expected_line)
                        actual.append(actual_line)
            self.assertListEqual(expected, actual)

    def _test_can_process_logfile(self, logfile):
        output_file = os.path.join(self.test_dir, 'log_output.txt')
        zero_log_parser.parse_log(logfile, output_file)
        last_output_file = zero_log_parser.default_parsed_output_for(logfile)
        self.assertFileContentsMatch(last_output_file, output_file)


def main():
    import sys
    import argparse
    global log_files_to_test
    arg_parser = argparse.ArgumentParser(
        description='Run the log parser against a log file or directory of log files.')
    arg_parser.add_argument('log_file_or_dir', help='log file, or directory of log files to test')
    arg_parser.add_argument('unittest_args', nargs='*')
    args = arg_parser.parse_args()
    log_file_or_dir = args.log_file_or_dir
    if os.path.isdir(log_file_or_dir):
        log_dir = log_file_or_dir
        log_files_to_test = [log_file for log_file in
                             os.listdir(log_dir) if zero_log_parser.is_log_file_path(log_file)]
        for log_file in log_files_to_test:
            test_name = 'test_%s' % log_file
            logfile_path = os.path.join(log_dir, log_file)
            test = logfile_test_generator(logfile_path)
            setattr(TestLogParser, test_name, test)
    else:
        log_file = log_file_or_dir
        test_name = 'test_%s' % log_file
        test = logfile_test_generator(log_file)
        setattr(TestLogParser, test_name, test)

    # Ensure unittest doesn't interpret log_dir
    sys.argv[1:] = args.unittest_args
    unittest.main()


if __name__ == '__main__':
    main()
