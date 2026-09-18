"""Zero Motorcycle Log Parser.

A modern parser for Zero Motorcycle log files with structured data extraction.
Supports both MBB (Main Bike Board) and BMS (Battery Management System) logs.
"""

from .core import LogData, parse_log

__version__ = "2.3.0-dev"

# Don't import plotting at package level - it will be imported only when needed
__all__ = [
    "LogData",
    "parse_log",
]

def _get_plotter_class():
    """Lazy import of plotting functionality."""
    try:
        from .plotting import ZeroLogPlotter
        return ZeroLogPlotter
    except ImportError as e:
        raise ImportError("plotly and pandas are required for plotting. Install with: pip install -e \".[plotting]\"") from e
