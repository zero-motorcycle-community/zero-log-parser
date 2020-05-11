import contextlib
import io
import os
import re
import unittest
import shutil
import tempfile

import zero_log_parser as parser

log_files_to_test = None


def logfile_test_generator(logfile):
    def test(self):
        """zero_log_parser can handle this log file."""
        self._test_can_process_logfile(logfile)
    return test


def lines_from_log_path(log_path: str) -> [str]:
    if os.path.isfile(log_path):
        with io.open(log_path, 'r', encoding='utf8') as log_file:
            return list(log_file)
    return []


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

    def numberFromEntryLine(self, line: str):
        line_no = line[1:6]
        self.assertRegex(line_no, r'^[0-9]+$', 'has a line number')
        return line_no

    def assertHeaderLinesMatch(self, expected_header_lines: [str], actual_header_lines: [str]):
        self.assertNotEqual(actual_header_lines[0], 'Zero Unknown Type log',
                            msg='Log type unknown')
        self.assertLessEqual(len(expected_header_lines), len(actual_header_lines),
                             msg='No fewer header lines than before')
        expected_headers = {line for line in expected_header_lines
                            if line != '\n' and '  ' not in line}
        actual_headers = {line for line in actual_header_lines
                          if line != '\n' and '  ' not in line}
        self.assertSetEqual(expected_headers, actual_headers,
                            msg='headers differ')
        expected_dict = {}
        for line in expected_header_lines:
            if '   ' in line:
                try:
                    key, value = re.split(r'[ ]{3,}', line.strip(), 2)
                    expected_dict[key] = value
                except ValueError:
                    pass
        actual_dict = {}
        for line in actual_header_lines:
            if '   ' in line:
                try:
                    key, value = re.split(r'[ ]{3,}', line.strip(), 2)
                    actual_dict[key] = value
                except ValueError:
                    pass
        sys_info_unknown = {'System info': 'unknown'}
        if expected_dict == sys_info_unknown:
            self.assertNotEqual(actual_dict, sys_info_unknown)
        else:
            self.assertDictEqual(expected_dict, actual_dict,
                                 msg='header metadata matches')

    def assertEntriesLinesMatch(self, expected_entry_lines: [str], actual_entry_lines: [str]):
        for actual_line in actual_entry_lines:
            self.assertFalse(self.lineIsError(actual_line), 'no errors in new output')
        self.assertLessEqual(len(expected_entry_lines), len(actual_entry_lines),
                             msg='No fewer entries than before')
        expected_lines_by_no = {self.numberFromEntryLine(line): line[11:]
                                for line in expected_entry_lines
                                if len(line) > 7}
        actual_lines_by_no = {self.numberFromEntryLine(line): line[11:]
                              for line in actual_entry_lines
                              if len(line) > 7}
        for line_no, expected_line in expected_lines_by_no.items():
            actual_line = actual_lines_by_no.get(line_no)
            with self.subTest('lines match', line_no=line_no):
                if ' 0x' in expected_line and ' 0x' in actual_line:
                    pass
                else:
                    self.assertEqual(expected_line, actual_line,
                                     msg='same entries at line: {}'.format(line_no))

    def _test_can_process_logfile(self, logfile: str, suppress_logging=True):
        # with self.subTest('zero_log_parser can handle this log file: ' + logfile, file=logfile):
        actual_path = os.path.join(self.test_dir, 'log_output.txt')
        logger = parser.console_logger(logfile, verbose=True)
        if suppress_logging:
            with open(os.devnull, 'w') as devnull:
                with contextlib.redirect_stdout(devnull):
                    logger.disabled = True
                    parser.parse_log(logfile, actual_path, logger=logger)
        else:
            parser.parse_log(logfile, actual_path, logger=logger)
        expected_path = parser.default_parsed_output_for(logfile)
        expected_lines = lines_from_log_path(expected_path)
        if len(expected_lines) == 0:
            return
        actual_lines = lines_from_log_path(actual_path)
        divider = parser.LogData.header_divider
        expected_divider_index = expected_lines.index(divider)
        self.assertIn(divider, actual_lines)
        actual_divider_index = actual_lines.index(divider)
        # with self.subTest('headers', section='header'):
        expected_header_lines = expected_lines[0:expected_divider_index - 3]
        actual_divider_lines = actual_lines[0:actual_divider_index - 3]
        self.assertHeaderLinesMatch(expected_header_lines, actual_divider_lines)
        # with self.subTest('entries', section='entries'):
        expected_entry_lines = expected_lines[actual_divider_index + 1:]
        actual_entry_lines = actual_lines[actual_divider_index + 1:]
        self.assertEntriesLinesMatch(expected_entry_lines, actual_entry_lines)


def test_log_parse_output_against(log_file):
    test_name = 'test_log({})'.format(log_file)
    test = logfile_test_generator(log_file)
    setattr(TestLogParser, test_name, test)


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
        for dir_path, _, filenames in os.walk(log_dir):
            for filename in filenames:
                if parser.is_log_file_path(filename):
                    log_file = os.path.join(dir_path, filename)
                    test_log_parse_output_against(log_file)
    else:
        log_file = log_file_or_dir
        test_log_parse_output_against(log_file)

    # Ensure unittest doesn't interpret log_dir
    sys.argv[1:] = args.unittest_args
    unittest.main()


if __name__ == '__main__':
    main()
