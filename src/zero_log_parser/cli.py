"""Command line interface for Zero Log Parser."""

import argparse
import logging
import os
import sys
from typing import Optional

from . import __version__
from .core import parse_log, parse_multiple_logs, generate_merged_output_name
from .utils import console_logger, default_parsed_output_for, get_timezone_offset


def create_parser() -> argparse.ArgumentParser:
    """Create the command line argument parser."""
    parser = argparse.ArgumentParser(
        description="Parse Zero Motorcycle log files into human-readable format",
        epilog="For interactive plotting, use: zero-plotting <input_files>\n"
               "For more information, visit: https://github.com/ilja-radusch/zero-log-parser"
    )
    
    parser.add_argument(
        'input_files',
        nargs='+',
        help="Input log file(s) (.bin). Multiple files will be intelligently merged."
    )
    
    parser.add_argument(
        '-o', '--output',
        help="Output file (default: input filename with .txt extension)"
    )
    
    parser.add_argument(
        '-f', '--format',
        choices=['txt', 'csv', 'tsv', 'json'],
        default='txt',
        help="Output format (default: txt)"
    )
    
    parser.add_argument(
        '--unnest',
        action='store_true',
        help="For CSV/TSV formats: expand structured data into separate rows with condition_key and condition_value columns"
    )
    
    parser.add_argument(
        '-t', '--timezone',
        help="Timezone offset in hours from UTC (e.g., -8, +1) or timezone name (e.g., Europe/Berlin, America/New_York). Default: system timezone"
    )
    
    parser.add_argument(
        '--start',
        help="Filter entries after this time (e.g., 'June 2025', '2025-06-15', 'last month')"
    )
    
    parser.add_argument(
        '--end', 
        help="Filter entries before this time (e.g., 'June 2025', '2025-06-15', 'last month')"
    )
    
    parser.add_argument(
        '--start-end',
        help="Filter entries within this period (e.g., 'June 2025' sets both start and end boundaries automatically)"
    )
    
    parser.add_argument(
        '-v', '--verbose',
        action='count',
        default=1,
        help="Increase verbosity level (-v, -vv, -vvv for levels 2, 3, 4)"
    )
    
    parser.add_argument(
        '-q', '--quiet',
        action='store_true',
        help="Quiet mode (verbosity level 0)"
    )
    
    parser.add_argument(
        '--version',
        action='version',
        version=f'zero-log-parser {__version__}'
    )
    
    
    return parser


def validate_input_files(file_paths: list) -> None:
    """Validate that all input files exist and are readable."""
    for file_path in file_paths:
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"Input file not found: {file_path}")
        
        if not os.path.isfile(file_path):
            raise ValueError(f"Input path is not a file: {file_path}")
        
        if not os.access(file_path, os.R_OK):
            raise PermissionError(f"Cannot read input file: {file_path}")


def determine_output_file(input_files: list, output_file: Optional[str], format_type: str) -> str:
    """Determine the output file path for single or multiple input files."""
    if output_file:
        return output_file
    
    if len(input_files) == 1:
        # Single file - use traditional naming
        base_name = os.path.splitext(input_files[0])[0]
        extensions = {
            'txt': '.txt',
            'csv': '.csv',
            'tsv': '.tsv',
            'json': '.json'
        }
        return base_name + extensions.get(format_type, '.txt')
    else:
        # Multiple files - use merged naming
        return generate_merged_output_name(input_files, format_type)


def setup_logging(verbosity_level: int) -> logging.Logger:
    """Setup logging configuration based on verbosity level.
    
    Args:
        verbosity_level: 0=quiet, 1=normal, 2=verbose, 3=very verbose, 4=debug
    """
    # Map verbosity levels to logging levels
    level_map = {
        0: logging.ERROR,    # Quiet - only errors
        1: logging.INFO,     # Normal - info and above
        2: logging.DEBUG,    # Verbose - debug and above
        3: logging.DEBUG,    # Very verbose - same as verbose for logging
        4: logging.DEBUG     # Debug - same as verbose for logging
    }
    
    level = level_map.get(verbosity_level, logging.INFO)
    logging.basicConfig(
        level=level,
        format='%(asctime)s [%(name)s] [%(levelname)s] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    return logging.getLogger('zero-log-parser')


def main() -> int:
    """Main entry point for the CLI."""
    try:
        # Parse command line arguments
        parser = create_parser()
        args = parser.parse_args()
        
        # Calculate verbosity level
        if args.quiet:
            verbosity_level = 0
        else:
            verbosity_level = args.verbose
        
        # Setup logging
        logger = setup_logging(verbosity_level)
        
        # Validate input files
        validate_input_files(args.input_files)
        
        # Parse time filtering parameters
        start_time = None
        end_time = None
        
        # Handle --start-end shorthand
        if args.start_end:
            if args.start or args.end:
                parser.error("--start-end cannot be used with --start or --end")
            try:
                from .utils import parse_time_range
                start_time, end_time = parse_time_range(args.start_end, args.timezone)
            except Exception as e:
                parser.error(f"Invalid --start-end time specification: {e}")
        else:
            # Handle individual --start and --end parameters
            if args.start:
                try:
                    from .utils import parse_time_filter_start
                    start_time = parse_time_filter_start(args.start, args.timezone)
                except Exception as e:
                    parser.error(f"Invalid --start time specification: {e}")
            
            if args.end:
                try:
                    from .utils import parse_time_filter_end
                    end_time = parse_time_filter_end(args.end, args.timezone)
                except Exception as e:
                    parser.error(f"Invalid --end time specification: {e}")
        
        # Determine output file
        output_file = determine_output_file(args.input_files, args.output, args.format)
        
        # Check if output directory exists
        output_dir = os.path.dirname(output_file)
        if output_dir and not os.path.exists(output_dir):
            os.makedirs(output_dir, exist_ok=True)
        
        # Parse the log file(s)
        if len(args.input_files) == 1:
            # Single file parsing
            input_file = args.input_files[0]
            logger.info(f"Parsing {input_file} -> {output_file} (format: {args.format})")
            
            parse_log(
                bin_file=input_file,
                output_file=output_file,
                tz_code=args.timezone,
                verbosity_level=verbosity_level,
                logger=logger,
                output_format=args.format,
                start_time=start_time,
                end_time=end_time,
                unnest=args.unnest
            )
        else:
            # Multiple file parsing with merging
            logger.info(f"Parsing {len(args.input_files)} files -> {output_file} (format: {args.format})")
            
            parse_multiple_logs(
                bin_files=args.input_files,
                output_file=output_file,
                tz_code=args.timezone,
                verbosity_level=verbosity_level,
                logger=logger,
                output_format=args.format,
                start_time=start_time,
                end_time=end_time,
                unnest=args.unnest
            )
        
        logger.info("Parsing completed successfully")
        
        return 0
        
    except KeyboardInterrupt:
        print("\nOperation cancelled by user", file=sys.stderr)
        return 130
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2
    except PermissionError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 13
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        if '--verbose' in sys.argv or '-v' in sys.argv:
            import traceback
            traceback.print_exc()
        return 1


if __name__ == '__main__':
    sys.exit(main())
