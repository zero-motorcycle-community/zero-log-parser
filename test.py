import contextlib
import io
import os
import re
import unittest
import shutil
import tempfile

import zero_log_parser as parser


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

    def assertLineHasNumber(self, line: str, msg=None):
        line_no = line[1:6]
        self.assertRegex(line_no, r'^[0-9]+$', msg=msg or 'Has a line number: "{}"'.format(line))

    @classmethod
    def numberFromEntryLine(cls, line: str):
        return line[1:6]

    @classmethod
    def lineHasEntry(cls, line: str):
        return len(line) > 7

    def assertHeaderLinesMatch(self, expected_header_lines: [str], actual_header_lines: [str]):
        with self.subTest('header'):
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
        with self.subTest('header system information'):
            if expected_dict == sys_info_unknown:
                self.assertNotEqual(actual_dict, sys_info_unknown)
            else:
                self.assertDictEqual(expected_dict, actual_dict)

    def assertEntriesLinesMatch(self, expected_entry_lines: [str], actual_entry_lines: [str]):
        with self.subTest('entries'):
            self.assertLessEqual(len(expected_entry_lines), len(actual_entry_lines),
                                 msg='No fewer entries than before')
            expected_lines_by_no = {self.numberFromEntryLine(line): line[11:]
                                    for line in expected_entry_lines
                                    if self.lineHasEntry(line)}
            actual_lines_by_no = {self.numberFromEntryLine(line): line[11:]
                                  for line in actual_entry_lines
                                  if self.lineHasEntry(line)}
            for line_no, expected_line in expected_lines_by_no.items():
                actual_line = actual_lines_by_no.get(line_no)
                if ' 0x' in expected_line and ' 0x' in actual_line:
                    pass
                else:
                    self.assertEqual(expected_line, actual_line,
                                     msg='same entries at line: {}'.format(line_no))

    def _test_parse_of_logfile(self, logfile: str):
        actual_path = os.path.join(self.test_dir, 'log_output.txt')
        logger = parser.console_logger(logfile, verbose=True)
        with self.assertLogs(logfile, level='INFO') as logs:
            parser.parse_log(logfile, actual_path)
        for log_record in logs.records:
            if log_record.levelname != 'INFO':
                self.fail(msg=log_record.message)
        expected_path = parser.default_parsed_output_for(logfile)
        if not os.path.isfile(expected_path):
            return
        expected_lines = lines_from_log_path(expected_path)
        self.assertGreater(len(expected_lines), 0, 'expected file has lines')
        self.assertTrue(os.path.isfile(actual_path), 'output file exists')
        actual_lines = lines_from_log_path(actual_path)
        self.assertGreater(len(actual_lines), 0, 'output has lines')
        divider = parser.LogData.header_divider
        expected_divider_index = expected_lines.index(divider)
        actual_divider_index = actual_lines.index(divider)
        self.assertGreater(actual_divider_index, 0, 'output divider follows a header')
        expected_header_lines = expected_lines[0:expected_divider_index - 3]
        actual_divider_lines = actual_lines[0:actual_divider_index - 3]
        self.assertHeaderLinesMatch(expected_header_lines, actual_divider_lines)
        expected_entry_lines = expected_lines[actual_divider_index + 1:]
        actual_entry_lines = actual_lines[actual_divider_index + 1:]
        for line in actual_entry_lines:
            if self.lineIsError(line):
                self.fail(msg='line has error: ' + line)
            if self.lineHasEntry(line):
                self.assertLineHasNumber(line, msg='No line number in: "{}"'.format(line))
        self.assertEntriesLinesMatch(expected_entry_lines, actual_entry_lines)


LOG_DIR = os.getenv('LOG_DIR')
LOG_FILE = None


class TestLogParserDirectory(TestLogParser):
    def test_can_handle_logs_in_dir(self):
        if not LOG_DIR:
            return
        for dir_path, _, filenames in os.walk(LOG_DIR):
            with self.subTest(msg='log dir', dir=os.path.split(dir_path)[-1]):
                log_names = list(filter(lambda x: parser.is_log_file_path(x), filenames))
                for log_name in log_names:
                    with self.subTest(log_name=log_name):
                        log_file = os.path.join(dir_path, log_name)
                        self._test_parse_of_logfile(log_file)

    def test_can_handle_one_log(self):
        if not LOG_FILE:
            return
        self._test_parse_of_logfile(LOG_FILE)


def main():
    import sys
    import argparse
    global LOG_FILE
    global LOG_DIR
    arg_parser = argparse.ArgumentParser(
        description='Run the log parser against a log file or directory of log files.')
    arg_parser.add_argument('log_file_or_dir', help='log file, or directory of log files to test')
    arg_parser.add_argument('unittest_args', nargs='*')
    args = arg_parser.parse_args()
    log_file_or_dir = args.log_file_or_dir
    if os.path.isdir(log_file_or_dir):
        LOG_DIR = log_file_or_dir
    elif os.path.isfile(log_file_or_dir):
        LOG_FILE = log_file_or_dir

    # Ensure unittest doesn't interpret log_dir
    sys.argv[1:] = args.unittest_args
    unittest.main()


if __name__ == '__main__':
    main()
