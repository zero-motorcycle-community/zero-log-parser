"""
Core parsing logic for Zero Motorcycle log files.
This uses the proven working implementation from the standalone zero_log_parser.py
"""

import os
import sys
import importlib.util
from typing import Optional


def _load_standalone_parser():
    """Load the standalone zero_log_parser.py module dynamically."""
    standalone_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'zero_log_parser.py')
    if not os.path.exists(standalone_path):
        raise ImportError(f"Standalone parser not found at {standalone_path}")
    
    spec = importlib.util.spec_from_file_location("standalone_parser", standalone_path)
    standalone_parser = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(standalone_parser)
    return standalone_parser


# Load the working standalone implementation
_standalone = _load_standalone_parser()

# Export the working classes and functions directly
BinaryTools = _standalone.BinaryTools
LogFile = _standalone.LogFile
LogData = _standalone.LogData
Gen2 = _standalone.Gen2
Gen3 = _standalone.Gen3

# Export constants
REV0 = _standalone.REV0
REV1 = _standalone.REV1  
REV2 = _standalone.REV2
REV3 = _standalone.REV3

# Export utility functions
is_vin = _standalone.is_vin

# Export exceptions
MismatchingVinError = _standalone.MismatchingVinError

parse_log = _standalone.parse_log
parse_multiple_logs = _standalone.parse_multiple_logs
generate_merged_output_name = _standalone.generate_merged_output_name
