import os

import zero_log_parser as parser


def parse_logs(dir_path, filename, output_suffix):
    logfile_path = os.path.join(dir_path, filename)
    new_output = os.path.splitext(logfile_path)[0] + output_suffix
    logger = parser.console_logger(filename, verbose=True)
    parser.parse_log(logfile_path, new_output, logger=logger)
    return True


def main():
    import sys
    import argparse
    arg_parser = argparse.ArgumentParser(
        description='Run the log parser against a log file or directory of log files.')
    arg_parser.add_argument('log_dir', help='directory of log files to parse into new output')
    arg_parser.add_argument('--threads', type=int, default=1,
                            help='number of threads to parse logs')
    arg_parser.add_argument('--replace', action='store_true',
                            help='whether to replace old outputs')
    args = arg_parser.parse_args()
    log_dir = args.log_dir
    if not os.path.isdir(log_dir):
        print("Not a directory: " + log_dir)
        sys.exit(1)
    replace = args.replace
    output_suffix = '.txt' if replace else '.new.txt'
    import multiprocessing as mp
    pool = mp.Pool(processes=args.threads)
    for dir_path, _, filenames in os.walk(log_dir):
        log_names = list(filter(lambda x: parser.is_log_file_path(x), filenames))
        log_args = [(dir_path, log_name, output_suffix,) for log_name in log_names]
        pool.starmap_async(parse_logs, log_args)
    pool.close()
    pool.join()


if __name__ == '__main__':
    main()
