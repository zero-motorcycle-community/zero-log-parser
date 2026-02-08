"""Interactive plotting module for Zero Motorcycle log data.

Generates interactive plotly visualizations from Zero Motorcycle log data.
Supports both binary (.bin) and CSV input files.
"""

import json
import os
import tempfile
from datetime import datetime
from typing import Dict, List, Optional, Union

try:
    import pandas as pd
    import plotly.graph_objects as go
    import plotly.express as px
    from plotly.subplots import make_subplots
    PLOTLY_AVAILABLE = True
    Figure = go.Figure
except ImportError:
    PLOTLY_AVAILABLE = False
    pd = None
    go = None
    px = None
    # Mock Figure class for type hints
    class Figure:
        pass

from .core import parse_log, LogData, LogFile, MismatchingVinError


class ZeroLogPlotter:
    """Generate interactive plots from Zero Motorcycle log data."""
    
    def __init__(self, input_file: str,
                 start_time: Optional['datetime'] = None, end_time: Optional['datetime'] = None,
                 tz_code: int | str | None = None):
        """Initialize plotter with input file (bin or csv) and optional time filters."""
        if not PLOTLY_AVAILABLE:
            raise ImportError("plotly and pandas are required for plotting. Install with: pip install -e \".[plotting]\"")
        
        self.input_file = input_file
        self.start_time = start_time
        self.end_time = end_time
        self.tz_code = tz_code
        self.data = {}
        self.file_type = self._detect_file_type()
        self._load_data()

    @classmethod
    def from_multiple_files(cls, input_files: List[str],
                            start_time: Optional['datetime'] = None, end_time: Optional['datetime'] = None,
                            tz_code: int | str | None = None):
        """Create plotter from multiple log files by merging them first."""
        if not PLOTLY_AVAILABLE:
            raise ImportError("plotly and pandas are required for plotting. Install with: pip install -e \".[plotting]\"")
        
        if not input_files:
            raise ValueError("At least one input file must be provided")
        
        if len(input_files) == 1:
            return cls(input_files[0], start_time=start_time,
                       end_time=end_time, tz_code=tz_code)
        
        # Check if all files are the same type
        file_types = set()
        for file_path in input_files:
            if file_path.endswith('.bin'):
                file_types.add('binary')
            elif file_path.endswith('.csv'):
                file_types.add('csv')
            else:
                raise ValueError(f"Unsupported file type: {file_path}")
        
        if len(file_types) > 1:
            raise ValueError("All input files must be the same type (.bin or .csv)")
        
        file_type = file_types.pop()
        
        # Always use LogData merging for intelligent duplicate removal
        # Convert CSV files to LogData objects first if needed
        return cls._merge_using_logdata(input_files, file_type,
                                        start_time=start_time,
                                        end_time=end_time,
                                        tz_code=tz_code)

    @classmethod
    def _merge_using_logdata(cls, input_files: List[str], file_type: str,
                             start_time: Optional['datetime'] = None,
                             end_time: Optional['datetime'] = None,
                             tz_code: int | str | None = None):
        """Merge multiple files using LogData merge functionality for intelligent duplicate removal."""
        print(f"Loading and merging {len(input_files)} files using LogData merge operators...")
        
        from .utils import get_timezone_offset
        timezone_offset = get_timezone_offset(tz_code)

        try:
            log_data_objects = []
            log_file_types = set()  # Track what types of logs we have
            
            for i, input_file in enumerate(input_files):
                print(f"Loading file {i+1}/{len(input_files)}: {os.path.basename(input_file)}")
                
                if file_type == 'binary':
                    # Load binary file directly
                    log_file = LogFile(input_file)
                    log_data = LogData(log_file, timezone_offset)
                    
                    # Track log file type for basename generation
                    if log_file.is_mbb():
                        log_file_types.add('MBB')
                    elif log_file.is_bms():
                        log_file_types.add('BMS')
                    else:
                        log_file_types.add('Unknown')
                        
                else:
                    # For CSV files, we need to convert back to binary LogData
                    # This ensures we get the smart duplicate detection from LogData merge
                    print(f"  Note: CSV files will be processed through LogData for intelligent merging")
                    
                    # For now, skip CSV->LogData conversion as it's complex
                    # Fall back to simple DataFrame merging for CSV files
                    if i == 0:
                        print("  Warning: CSV file merging uses simpler duplicate detection")
                        return cls._merge_csv_files_simple(input_files, start_time=start_time, end_time=end_time)
                
                log_data_objects.append(log_data)
            
            # Use the LogData merge operators for intelligent merging
            print("Merging log data using LogData + operators...")
            try:
                # This uses the sophisticated merge logic with smart duplicate detection
                merged_log_data = sum(log_data_objects)
                print(f"✓ Successfully merged {len(input_files)} files using LogData operators")
                print(f"  Total entries: {merged_log_data.entries_count}")
                print(f"  VIN: {merged_log_data._get_vin()}")
                
            except MismatchingVinError as e:
                print(f"Warning: VIN mismatch detected: {e}")
                print("Proceeding with merge anyway (files may be from different motorcycles)")
                
                # Force merge by temporarily making VINs compatible
                for log_data in log_data_objects[1:]:
                    log_data.header_info['VIN'] = log_data_objects[0].header_info.get('VIN', 'Unknown')
                
                merged_log_data = sum(log_data_objects)
                print(f"✓ Force-merged {len(input_files)} files with VIN override")
                
            # Convert merged LogData to CSV for plotting
            temp_csv = tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False)
            temp_csv.close()
            
            print("Converting merged LogData to CSV for plotting...")
            merged_log_data.emit_tabular_decoding(temp_csv.name, out_format='csv')
            
            # Create plotter instance with merged CSV
            plotter = cls(temp_csv.name, start_time=start_time,
                          end_time=end_time, tz_code=tz_code)
            
            # Generate meaningful filename from VIN, log types, and latest date
            base_name = cls._generate_merged_basename(merged_log_data, temp_csv.name, log_file_types)
            plotter.input_file = base_name
            
            return plotter
            
        except Exception as e:
            raise RuntimeError(f"Failed to merge files using LogData: {e}")
    
    @classmethod
    def _merge_csv_files_simple(cls, csv_files: List[str], start_time: Optional['datetime'] = None, end_time: Optional['datetime'] = None):
        """Simple CSV file merging (fallback when LogData conversion is not available)."""
        temp_csv = tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False)
        temp_csv.close()
        
        try:
            print("Using simple CSV merging (timestamp-based duplicate removal only)")
            
            # Read and combine all CSV files
            combined_dfs = []
            for csv_file in csv_files:
                df = pd.read_csv(csv_file, sep=';')
                df['source_file'] = os.path.basename(csv_file)
                combined_dfs.append(df)
            
            # Merge DataFrames
            merged_df = pd.concat(combined_dfs, ignore_index=True)
            
            # Sort by timestamp if available
            if 'timestamp' in merged_df.columns:
                merged_df['timestamp'] = pd.to_datetime(merged_df['timestamp'])
                merged_df = merged_df.sort_values('timestamp')
            
            # Remove duplicates based on timestamp and message content
            duplicate_cols = ['timestamp', 'message']
            if 'conditions' in merged_df.columns:
                duplicate_cols.append('conditions')
            
            initial_count = len(merged_df)
            merged_df = merged_df.drop_duplicates(subset=duplicate_cols, keep='first')
            removed_count = initial_count - len(merged_df)
            
            print(f"Removed {removed_count} duplicate entries during CSV merge")
            
            # Write merged CSV
            merged_df.to_csv(temp_csv.name, sep=';', index=False)
            
            # Create plotter instance
            plotter = cls(temp_csv.name, start_time=start_time, end_time=end_time)
            
            # Generate meaningful filename from CSV data
            base_name = cls._generate_csv_merged_basename(merged_df, len(csv_files))
            plotter.input_file = base_name
            
            return plotter
            
        except Exception as e:
            if os.path.exists(temp_csv.name):
                os.unlink(temp_csv.name)
            raise

    @classmethod
    def _generate_merged_basename(cls, merged_log_data, csv_file_path: str, log_file_types: set) -> str:
        """Generate meaningful basename from log types, VIN and latest date in merged LogData."""
        try:
            # Generate log type prefix
            log_prefix = cls._generate_log_type_prefix(log_file_types)
            
            # Get VIN from merged LogData
            vin = merged_log_data._get_vin()
            if vin == 'Unknown':
                vin = 'UnknownVIN'
            else:
                # Clean VIN for filename use (remove invalid characters)
                vin = ''.join(c for c in vin if c.isalnum() or c in '-_')
            
            # Find latest date by reading the generated CSV
            latest_date = cls._extract_latest_date_from_csv(csv_file_path)
            
            if latest_date:
                return f"{vin}_{log_prefix}_{latest_date}"
            else:
                return f"{vin}_{log_prefix}_merged"
                
        except Exception as e:
            print(f"Warning: Could not generate VIN-based filename: {e}")
            return "merged_data"

    @classmethod
    def _generate_log_type_prefix(cls, log_file_types: set) -> str:
        """Generate log type prefix from set of log types."""
        # Remove 'Unknown' if we have other types
        valid_types = {t for t in log_file_types if t != 'Unknown'}
        if valid_types:
            log_file_types = valid_types
        
        # Sort for consistent ordering
        sorted_types = sorted(log_file_types)
        
        if len(sorted_types) == 0:
            return "Unknown"
        elif len(sorted_types) == 1:
            return sorted_types[0]
        else:
            # Multiple types: combine them
            return "+".join(sorted_types)
    
    @classmethod
    def _generate_csv_merged_basename(cls, merged_df, file_count: int) -> str:
        """Generate meaningful basename from CSV DataFrame."""
        try:
            # Detect log types from message content
            log_file_types = cls._detect_log_types_from_csv(merged_df)
            log_prefix = cls._generate_log_type_prefix(log_file_types)
            
            # Try to extract VIN from the data (might be in conditions or other fields)
            vin = "UnknownVIN"
            
            # Look for VIN in various possible columns/fields
            if 'conditions' in merged_df.columns:
                for _, row in merged_df.iterrows():
                    conditions_str = str(row.get('conditions', ''))
                    if 'VIN' in conditions_str and len(conditions_str) > 10:
                        # Try to extract VIN from JSON conditions
                        try:
                            import json
                            conditions = json.loads(conditions_str)
                            if 'VIN' in conditions:
                                vin = conditions['VIN']
                                break
                        except:
                            pass
            
            # Clean VIN for filename
            vin = ''.join(c for c in vin if c.isalnum() or c in '-_')
            
            # Extract latest date from timestamp column
            latest_date = None
            if 'timestamp' in merged_df.columns:
                try:
                    timestamps = pd.to_datetime(merged_df['timestamp'], errors='coerce')
                    latest_timestamp = timestamps.max()
                    if pd.notna(latest_timestamp):
                        latest_date = latest_timestamp.strftime('%Y-%m-%d')
                except:
                    pass
            
            if latest_date:
                return f"{vin}_{log_prefix}_{latest_date}.csv"
            else:
                return f"{vin}_{log_prefix}_merged_{file_count}files.csv"
                
        except Exception as e:
            print(f"Warning: Could not generate meaningful CSV filename: {e}")
            return f"merged_{file_count}files.csv"

    @classmethod
    def _detect_log_types_from_csv(cls, merged_df) -> set:
        """Detect log types (MBB/BMS) from CSV message content."""
        log_types = set()
        
        if 'message' not in merged_df.columns:
            return {'Unknown'}
        
        # Sample some messages to determine log type
        messages = merged_df['message'].dropna().unique()[:50]  # Check first 50 unique messages
        
        # MBB-specific message patterns
        mbb_indicators = [
            'Riding', 'Disarmed', 'Board status', 'Key state', 'Motor', 'Speed',
            'Battery current', 'RPM', 'Temperature', 'Voltage', 'Power'
        ]
        
        # BMS-specific message patterns  
        bms_indicators = [
            'Discharge level', 'SOC Data', 'Charge', 'Cell', 'Pack', 'Balance',
            'Isolation', 'Contactor', 'Current sensor', 'BMS'
        ]
        
        mbb_score = 0
        bms_score = 0
        
        for message in messages:
            message_str = str(message).lower()
            
            # Check for MBB indicators
            for indicator in mbb_indicators:
                if indicator.lower() in message_str:
                    mbb_score += 1
                    break
                    
            # Check for BMS indicators
            for indicator in bms_indicators:
                if indicator.lower() in message_str:
                    bms_score += 1
                    break
        
        # Determine log types based on scores
        if mbb_score > 0:
            log_types.add('MBB')
        if bms_score > 0:
            log_types.add('BMS')
            
        # If no clear indicators, return Unknown
        if not log_types:
            log_types.add('Unknown')
            
        return log_types
    
    @classmethod
    def _extract_latest_date_from_csv(cls, csv_file_path: str) -> str:
        """Extract the latest date from a CSV file's timestamp column."""
        try:
            # Read just the timestamp column to find latest date
            df = pd.read_csv(csv_file_path, sep=';', usecols=['timestamp'], nrows=1000)
            
            if len(df) == 0:
                return None
                
            # Convert to datetime and find max
            timestamps = pd.to_datetime(df['timestamp'], errors='coerce')
            valid_timestamps = timestamps.dropna()
            
            if len(valid_timestamps) == 0:
                return None
                
            latest_timestamp = valid_timestamps.max() 
            return latest_timestamp.strftime('%Y-%m-%d')
            
        except Exception as e:
            print(f"Warning: Could not extract latest date from CSV: {e}")
            return None
    
    def _detect_file_type(self) -> str:
        """Detect if input is binary or CSV file."""
        if self.input_file.endswith('.bin'):
            return 'binary'
        elif self.input_file.endswith('.csv'):
            return 'csv'
        else:
            raise ValueError("Input file must be .bin or .csv")
    
    def _load_data(self):
        """Load and parse data from input file."""
        if self.file_type == 'binary':
            self._load_from_binary()
        else:
            self._load_from_csv()
    
    def _load_from_binary(self):
        """Load data from binary file by converting to CSV first."""
        # Create temporary CSV file
        temp_csv = tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False)
        temp_csv.close()
        
        # Parse binary to CSV using the core module
        try:
            parse_log(self.input_file, temp_csv.name,
                      tz_code=self.tz_code, output_format='csv')
            self.csv_file = temp_csv.name
            self._load_from_csv()
        finally:
            # Clean up temporary file
            if os.path.exists(temp_csv.name):
                os.unlink(temp_csv.name)
    
    def _load_from_csv(self):
        """Load data from CSV file."""
        csv_file = self.csv_file if hasattr(self, 'csv_file') else self.input_file
        df = pd.read_csv(csv_file, sep=';')
        
        # Parse timestamp column
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        
        # Parse JSON conditions into separate columns
        df_expanded = []
        for _, row in df.iterrows():
            row_dict = row.to_dict()
            if pd.notna(row['conditions']) and row['conditions'].strip():
                try:
                    conditions = json.loads(row['conditions'])
                    row_dict.update(conditions)
                except (json.JSONDecodeError, ValueError):
                    pass
            df_expanded.append(row_dict)
        
        self.df = pd.DataFrame(df_expanded)
        
        # Apply time filtering if specified
        if self.start_time or self.end_time:
            from .utils import apply_time_filter
            self.df = apply_time_filter(self.df, self.start_time, self.end_time)
            print(f"Filtered to {len(self.df)} entries")
        
        # Separate by message type for easier access
        self.data = {
            'bms_discharge': self.df[self.df['message'] == 'Discharge level'],
            'bms_soc': self.df[self.df['message'] == 'SOC Data'],
            'mbb_riding': self.df[self.df['message'] == 'Riding'],
            'mbb_disarmed': self.df[self.df['message'] == 'Disarmed'],
            'mbb_charging': self.df[self.df['message'] == 'Charging'],
            'charger_charging': self.df[self.df['message'] == 'Charger 6 Charging'],
            'charger_stopped': self.df[self.df['message'] == 'Charger 6 Stopped'],
        }
    
    def _insert_gaps_for_temporal_breaks(self, df: pd.DataFrame, gap_threshold_minutes: int = 30):
        """Insert NaN values where there are large temporal gaps in the data."""
        if df.empty:
            return df
        
        df_sorted = df.sort_values('timestamp').copy()
        df_with_gaps = []
        
        for i in range(len(df_sorted)):
            df_with_gaps.append(df_sorted.iloc[i])
            
            # Check if there's a gap to the next data point
            if i < len(df_sorted) - 1:
                current_time = df_sorted.iloc[i]['timestamp']
                next_time = df_sorted.iloc[i + 1]['timestamp']
                time_diff = (pd.to_datetime(next_time) - pd.to_datetime(current_time)).total_seconds() / 60  # minutes
                
                # If gap is larger than threshold, insert NaN rows at both ends of the gap
                if time_diff > gap_threshold_minutes:
                    # Insert gap start marker
                    gap_start = df_sorted.iloc[i].copy()
                    for col in gap_start.index:
                        if col != 'timestamp' and pd.api.types.is_numeric_dtype(df_sorted[col]):
                            gap_start[col] = None  # Use None instead of pd.NA for better plotly compatibility
                    gap_start['timestamp'] = pd.to_datetime(current_time) + pd.Timedelta(minutes=1)
                    df_with_gaps.append(gap_start)
                    
                    # Insert gap end marker
                    gap_end = df_sorted.iloc[i].copy()
                    for col in gap_end.index:
                        if col != 'timestamp' and pd.api.types.is_numeric_dtype(df_sorted[col]):
                            gap_end[col] = None  # Use None instead of pd.NA for better plotly compatibility
                    gap_end['timestamp'] = pd.to_datetime(next_time) - pd.Timedelta(minutes=1)
                    df_with_gaps.append(gap_end)
        
        return pd.DataFrame(df_with_gaps).reset_index(drop=True)
    
    def plot_battery_performance(self) -> Figure:
        """Plot battery SOC over time with riding modes."""
        fig = go.Figure()
        
        # Combine all data with SOC
        soc_data = []
        for name, df in self.data.items():
            if not df.empty and 'state_of_charge_percent' in df.columns:
                temp_df = df[['timestamp', 'state_of_charge_percent']].copy()
                temp_df['mode'] = name.replace('_', ' ').title()
                soc_data.append(temp_df)
        
        if soc_data:
            combined_df = pd.concat(soc_data).sort_values('timestamp')
            
            # Color map for different modes
            colors = {
                'Bms Discharge': '#1f77b4',
                'Mbb Riding': '#ff7f0e', 
                'Mbb Disarmed': '#2ca02c',
                'Mbb Charging': '#d62728'
            }
            
            for mode in combined_df['mode'].unique():
                mode_data = combined_df[combined_df['mode'] == mode]
                # Insert gaps for temporal breaks within each mode
                mode_data_with_gaps = self._insert_gaps_for_temporal_breaks(mode_data)
                
                fig.add_trace(go.Scatter(
                    x=mode_data_with_gaps['timestamp'],
                    y=mode_data_with_gaps['state_of_charge_percent'],
                    mode='lines+markers',
                    name=mode,
                    line=dict(color=colors.get(mode, '#8c564b')),
                    connectgaps=False  # Show gaps between separate sessions
                ))
        
        fig.update_layout(
            title='Battery State of Charge Over Time',
            xaxis_title='Time',
            yaxis_title='State of Charge (%)',
            hovermode='x unified'
        )
        
        return fig
    
    def plot_power_consumption(self) -> Figure:
        """Plot power consumption during riding."""
        riding_data = self.data['mbb_riding']
        
        if riding_data.empty:
            return go.Figure().add_annotation(text="No riding data available", 
                                            xref="paper", yref="paper", x=0.5, y=0.5)
        
        # Insert gaps for temporal breaks
        riding_data = self._insert_gaps_for_temporal_breaks(riding_data)
        
        fig = make_subplots(specs=[[{"secondary_y": True}]])
        
        # Battery current (primary y-axis)
        fig.add_trace(
            go.Scatter(x=riding_data['timestamp'], y=riding_data['battery_current_amps'],
                      name='Battery Current', line=dict(color='blue'), connectgaps=False),
            secondary_y=False,
        )
        
        # Motor current (secondary y-axis)
        if 'motor_current_amps' in riding_data.columns:
            fig.add_trace(
                go.Scatter(x=riding_data['timestamp'], y=riding_data['motor_current_amps'],
                          name='Motor Current', line=dict(color='red'), connectgaps=False),
                secondary_y=True,
            )
        
        fig.update_xaxes(title_text='Time')
        fig.update_yaxes(title_text='Battery Current (A)', secondary_y=False)
        fig.update_yaxes(title_text='Motor Current (A)', secondary_y=True)
        fig.update_layout(title_text='Power Consumption Analysis')
        
        return fig
    
    def plot_thermal_management(self) -> Figure:
        """Plot temperature deltas relative to ambient temperature."""
        riding_data = self.data['mbb_riding']
        
        # Only use riding data to show gaps when motorcycle is off
        temp_data = self._insert_gaps_for_temporal_breaks(riding_data.sort_values('timestamp'))
        
        if temp_data.empty:
            return go.Figure().add_annotation(text="No temperature data available",
                                            xref="paper", yref="paper", x=0.5, y=0.5)
        
        # Check if ambient temperature is available
        if 'ambient_temp_celsius' not in temp_data.columns:
            return go.Figure().add_annotation(text="No ambient temperature data available for delta calculation",
                                            xref="paper", yref="paper", x=0.5, y=0.5)
        
        fig = go.Figure()
        
        # Add zero line for ambient temperature baseline
        fig.add_hline(y=0, line_dash="dash", line_color="gray", 
                     annotation_text="Ambient Temperature (Baseline)")
        
        # Calculate temperature deltas relative to ambient
        temp_columns = [
            ('motor_temp_celsius', 'Motor Δ Temperature', '#ff7f0e'),
            ('controller_temp_celsius', 'Controller Δ Temperature', '#2ca02c'),
            ('pack_temp_high_celsius', 'Pack High Δ Temperature', '#d62728')
        ]
        
        for col, name, color in temp_columns:
            if col in temp_data.columns:
                # Calculate delta from ambient temperature
                delta_temp = temp_data[col] - temp_data['ambient_temp_celsius']
                
                fig.add_trace(go.Scatter(
                    x=temp_data['timestamp'],
                    y=delta_temp,
                    mode='lines',
                    name=name,
                    line=dict(color=color),
                    connectgaps=False,  # Show gaps when motorcycle is off
                    customdata=list(zip(temp_data[col], temp_data['ambient_temp_celsius'])),
                    hovertemplate='<b>%{fullData.name}</b><br>' +
                                'Time: %{x}<br>' +
                                'Δ Temperature: %{y:.1f}°C<br>' +
                                'Absolute: %{customdata[0]:.1f}°C<br>' +
                                'Ambient: %{customdata[1]:.1f}°C<br>' +
                                '<extra></extra>'
                ))
        
        fig.update_layout(
            title='Thermal Management - Temperature Deltas from Ambient',
            xaxis_title='Time',
            yaxis_title='Temperature Delta (°C above ambient)',
            hovermode='x unified',
            annotations=[
                dict(
                    text="Positive values indicate temperature above ambient<br>Zero line represents ambient temperature",
                    x=0.02, y=0.98,
                    xref="paper", yref="paper",
                    showarrow=False,
                    font=dict(size=10),
                    bgcolor="rgba(255,255,255,0.8)",
                    bordercolor="gray",
                    borderwidth=1
                )
            ]
        )
        
        return fig
    
    def plot_voltage_analysis(self) -> Figure:
        """Plot voltage analysis over time with separate pack and cell voltage graphs."""
        bms_data = self.data['bms_soc']
        bms_discharge_data = self.data['bms_discharge']
        riding_data = self.data['mbb_riding']
        disarmed_data = self.data['mbb_disarmed']
        
        # Separate pack voltage data sources from cell voltage data sources
        pack_voltage_data = []
        cell_voltage_data = []
        
        # Add BMS data (has both pack_voltage_volts and cell voltages)
        if not bms_data.empty:
            pack_voltage_data.append(bms_data)
            # Only add BMS data to cell voltage if it actually has cell voltage values
            if 'voltage_max_volts' in bms_data.columns and bms_data['voltage_max_volts'].notna().any():
                cell_voltage_data.append(bms_data)
        if not bms_discharge_data.empty:
            pack_voltage_data.append(bms_discharge_data)
            # Only add BMS discharge data to cell voltage if it actually has cell voltage values
            if 'voltage_max_volts' in bms_discharge_data.columns and bms_discharge_data['voltage_max_volts'].notna().any():
                cell_voltage_data.append(bms_discharge_data)
            
        # Add MBB data (has pack_voltage_volts but not meaningful cell voltages)
        if not riding_data.empty and 'pack_voltage_volts' in riding_data.columns:
            pack_voltage_data.append(riding_data)
        if not disarmed_data.empty and 'pack_voltage_volts' in disarmed_data.columns:
            pack_voltage_data.append(disarmed_data)
        
        if not pack_voltage_data:
            return go.Figure().add_annotation(text="No voltage data available",
                                            xref="paper", yref="paper", x=0.5, y=0.5)
        
        # Apply gap insertion separately for pack voltage and cell voltage data
        pack_voltage_combined = self._insert_gaps_for_temporal_breaks(pd.concat(pack_voltage_data).sort_values('timestamp'))
        
        # For cell voltage data, only process if we have actual cell voltage data
        cell_voltage_combined = None
        if cell_voltage_data:
            cell_voltage_combined = self._insert_gaps_for_temporal_breaks(pd.concat(cell_voltage_data).sort_values('timestamp'))
        
        # Use pack voltage data as the primary combined data for backward compatibility
        combined_data = pack_voltage_combined
        
        # Create subplots: Pack voltage on top, Cell voltages on bottom
        fig = make_subplots(
            rows=2, cols=1,
            subplot_titles=('Pack Voltage', 'Cell Voltages'),
            vertical_spacing=0.1,
            shared_xaxes=True
        )
        
        # Pack voltage subplot (row 1)
        if 'pack_voltage_volts' in combined_data.columns:
            fig.add_trace(go.Scatter(
                x=combined_data['timestamp'],
                y=combined_data['pack_voltage_volts'],
                mode='lines',
                name='Pack Voltage',
                line=dict(color='blue'),
                connectgaps=False
            ), row=1, col=1)
        
        # Cell voltage subplot (row 2) - use cell voltage data with proper gap handling
        cell_data_to_plot = cell_voltage_combined if cell_voltage_combined is not None else combined_data
        
        # Use standardized volt fields if available
        if 'voltage_max_volts' in cell_data_to_plot.columns and 'voltage_min_1_volts' in cell_data_to_plot.columns:
            # Use pre-converted volt values
            fig.add_trace(go.Scatter(
                x=cell_data_to_plot['timestamp'],
                y=cell_data_to_plot['voltage_max_volts'],
                mode='lines',
                name='Max Cell Voltage',
                line=dict(color='red', dash='dash'),
                connectgaps=False
            ), row=2, col=1)
            
            fig.add_trace(go.Scatter(
                x=cell_data_to_plot['timestamp'], 
                y=cell_data_to_plot['voltage_min_1_volts'],
                mode='lines',
                name='Min Cell Voltage',
                line=dict(color='orange', dash='dash'),
                fill='tonexty',
                fillcolor='rgba(255,165,0,0.2)',
                connectgaps=False
            ), row=2, col=1)
        
        # Fallback to millivolt fields if volt fields not available (backward compatibility)
        elif 'voltage_max' in cell_data_to_plot.columns and 'voltage_min_1' in cell_data_to_plot.columns:
            # Convert mV to V (fallback for older data)
            voltage_max_v = cell_data_to_plot['voltage_max'] / 1000
            voltage_min_v = cell_data_to_plot['voltage_min_1'] / 1000
            
            fig.add_trace(go.Scatter(
                x=cell_data_to_plot['timestamp'],
                y=voltage_max_v,
                mode='lines',
                name='Max Cell Voltage',
                line=dict(color='red', dash='dash'),
                connectgaps=False
            ), row=2, col=1)
            
            fig.add_trace(go.Scatter(
                x=cell_data_to_plot['timestamp'],
                y=voltage_min_v,
                mode='lines',
                name='Min Cell Voltage',
                line=dict(color='orange', dash='dash'),
                fill='tonexty',
                fillcolor='rgba(255,165,0,0.2)',
                connectgaps=False
            ), row=2, col=1)
        
        # Update layout
        fig.update_layout(
            title='Voltage Analysis',
            height=800,  # Taller for two subplots
            hovermode='x unified'
        )
        
        # Update axes labels
        fig.update_xaxes(title_text='Time', row=2, col=1)
        fig.update_yaxes(title_text='Voltage (V)', row=1, col=1)
        fig.update_yaxes(title_text='Voltage (V)', row=2, col=1)
        
        return fig
    
    def plot_performance_efficiency(self) -> Figure:
        """Plot performance vs efficiency scatter."""
        riding_data = self.data['mbb_riding']
        
        if riding_data.empty or 'motor_rpm' not in riding_data.columns:
            return go.Figure().add_annotation(text="No performance data available",
                                            xref="paper", yref="paper", x=0.5, y=0.5)
        
        # Filter out zero RPM for meaningful analysis
        perf_data = riding_data[riding_data['motor_rpm'] > 0]
        
        fig = go.Figure()
        
        fig.add_trace(go.Scatter(
            x=perf_data['motor_rpm'],
            y=perf_data['battery_current_amps'],
            mode='markers',
            marker=dict(
                color=perf_data['state_of_charge_percent'],
                colorscale='Viridis',
                colorbar=dict(title='SOC (%)'),
                size=6
            ),
            text=perf_data['state_of_charge_percent'],
            hovertemplate='RPM: %{x}<br>Current: %{y}A<br>SOC: %{text}%<extra></extra>'
        ))
        
        fig.update_layout(
            title='Performance vs Efficiency',
            xaxis_title='Motor RPM',
            yaxis_title='Battery Current (A)',
        )
        
        return fig
    
    def plot_charging_analysis(self) -> Figure:
        """Plot charging session analysis including recuperation."""
        charging_data = self.data['charger_charging']
        stopped_data = self.data['charger_stopped']
        riding_data = self.data['mbb_riding']
        
        # Check if we have any relevant data
        has_charging_data = not (charging_data.empty and stopped_data.empty)
        has_recuperation_data = not riding_data.empty and 'battery_current_amps' in riding_data.columns
        
        if not has_charging_data and not has_recuperation_data:
            return go.Figure().add_annotation(text="No charging or recuperation data available",
                                            xref="paper", yref="paper", x=0.5, y=0.5)
        
        fig = make_subplots(
            rows=4, cols=1,
            subplot_titles=('AC Voltage', 'EVSE Current', 'State of Charge', 'Recuperation (Regen Braking)'),
            vertical_spacing=0.06
        )
        
        # Add charging data if available
        if has_charging_data:
            all_charging = self._insert_gaps_for_temporal_breaks(pd.concat([charging_data, stopped_data]).sort_values('timestamp'))
            
            if 'voltage_ac' in all_charging.columns:
                fig.add_trace(go.Scatter(
                    x=all_charging['timestamp'],
                    y=all_charging['voltage_ac'],
                    mode='lines+markers',
                    name='AC Voltage',
                    line=dict(color='blue'),
                    connectgaps=False
                ), row=1, col=1)
            
            if 'evse_current_amps' in all_charging.columns:
                fig.add_trace(go.Scatter(
                    x=all_charging['timestamp'],
                    y=all_charging['evse_current_amps'],
                    mode='lines+markers',
                    name='EVSE Current',
                    line=dict(color='red'),
                    connectgaps=False
                ), row=2, col=1)
        
        # Add SOC data if available
        soc_charging = self.data['mbb_charging']
        if not soc_charging.empty and 'state_of_charge_percent' in soc_charging.columns:
            # Apply gap insertion to SOC data
            soc_with_gaps = self._insert_gaps_for_temporal_breaks(soc_charging)
            
            fig.add_trace(go.Scatter(
                x=soc_with_gaps['timestamp'],
                y=soc_with_gaps['state_of_charge_percent'],
                mode='lines+markers',
                name='SOC',
                line=dict(color='green'),
                connectgaps=False
            ), row=3, col=1)
        
        # Add recuperation analysis (negative battery current during riding)
        if has_recuperation_data:
            riding_with_gaps = self._insert_gaps_for_temporal_breaks(riding_data)
            
            # Create recuperation data by converting negative current to positive, keeping NaN gaps
            recuperation_data = riding_with_gaps.copy()
            
            # Convert negative current to positive, set positive current to NaN
            recuperation_data['recuperation_amps'] = riding_with_gaps['battery_current_amps'].apply(
                lambda x: -x if pd.notna(x) and x < 0 else (pd.NA if pd.notna(x) else x)
            )
            
            # Only add trace if we have actual recuperation events
            has_recuperation_events = recuperation_data['recuperation_amps'].notna().any()
            
            if has_recuperation_events:
                fig.add_trace(go.Scatter(
                    x=recuperation_data['timestamp'],
                    y=recuperation_data['recuperation_amps'],
                    mode='lines+markers',
                    name='Recuperation Current',
                    line=dict(color='orange'),
                    connectgaps=False,
                    hovertemplate='<b>Recuperation</b><br>' +
                                'Time: %{x}<br>' +
                                'Regen Current: %{y:.1f}A<br>' +
                                '<extra></extra>'
                ), row=4, col=1)
                
                # Add zero line for reference
                valid_recuperation_data = recuperation_data.dropna(subset=['recuperation_amps'])
                if len(valid_recuperation_data) > 0:
                    time_range = [valid_recuperation_data['timestamp'].min(), valid_recuperation_data['timestamp'].max()]
                    fig.add_trace(go.Scatter(
                        x=time_range,
                        y=[0, 0],
                        mode='lines',
                        name='Zero Line',
                        line=dict(color='gray', dash='dash', width=1),
                        showlegend=False,
                        hoverinfo='skip'
                    ), row=4, col=1)
        
        fig.update_layout(title_text='Charging & Recuperation Analysis', height=1000)
        return fig
    
    def plot_cell_balance(self) -> Figure:
        """Plot cell balance health."""
        bms_data = self.data['bms_discharge']
        
        if bms_data.empty or 'voltage_balance' not in bms_data.columns:
            return go.Figure().add_annotation(text="No cell balance data available",
                                            xref="paper", yref="paper", x=0.5, y=0.5)
        
        # Insert gaps for temporal breaks
        bms_data = self._insert_gaps_for_temporal_breaks(bms_data)
        
        fig = go.Figure()
        
        fig.add_trace(go.Scatter(
            x=bms_data['timestamp'],
            y=bms_data['voltage_balance'],
            mode='lines+markers',
            name='Voltage Balance',
            line=dict(color='purple'),
            connectgaps=False
        ))
        
        # Add threshold line (typical good balance is <10mV)
        fig.add_hline(y=10, line_dash="dash", line_color="red", 
                     annotation_text="10mV Threshold")
        
        fig.update_layout(
            title='Cell Balance Health',
            xaxis_title='Time',
            yaxis_title='Voltage Balance (mV)',
        )
        
        return fig
    
    def plot_range_analysis(self) -> Figure:
        """Plot odometer vs SOC for range analysis."""
        riding_data = self.data['mbb_riding']
        
        if riding_data.empty or 'odometer_km' not in riding_data.columns:
            return go.Figure().add_annotation(text="No odometer data available",
                                            xref="paper", yref="paper", x=0.5, y=0.5)
        
        fig = go.Figure()
        
        fig.add_trace(go.Scatter(
            x=riding_data['odometer_km'],
            y=riding_data['state_of_charge_percent'],
            mode='markers',
            marker=dict(
                color=riding_data['battery_current_amps'],
                colorscale='RdYlBu_r',
                colorbar=dict(title='Battery Current (A)'),
                size=6
            ),
            text=riding_data['battery_current_amps'],
            hovertemplate='Odometer: %{x} km<br>SOC: %{y}%<br>Current: %{text}A<extra></extra>'
        ))
        
        fig.update_layout(
            title='Range Analysis: Odometer vs SOC',
            xaxis_title='Odometer (km)',
            yaxis_title='State of Charge (%)',
        )
        
        return fig
    
    def generate_all_plots(self, output_dir: str = '.'):
        """Generate all available plots and save as HTML files."""
        if not PLOTLY_AVAILABLE:
            print("Error: plotly is required for plotting. Install with: pip install -e \".[plotting]\"")
            return
        
        # Create output directory if it doesn't exist
        import os
        try:
            os.makedirs(output_dir, exist_ok=True)
        except OSError as e:
            print(f"Error creating output directory '{output_dir}': {e}")
            return
        
        plots = {
            'battery_performance': self.plot_battery_performance,
            'power_consumption': self.plot_power_consumption,
            'thermal_management': self.plot_thermal_management,
            'voltage_analysis': self.plot_voltage_analysis,
            'performance_efficiency': self.plot_performance_efficiency,
            'charging_analysis': self.plot_charging_analysis,
            'cell_balance': self.plot_cell_balance,
            'range_analysis': self.plot_range_analysis,
        }
        
        base_name = os.path.splitext(os.path.basename(self.input_file))[0]
        
        for plot_name, plot_func in plots.items():
            try:
                fig = plot_func()
                output_file = os.path.join(output_dir, f"{base_name}_{plot_name}.html")
                fig.write_html(output_file)
                print(f"Generated: {output_file}")
            except Exception as e:
                print(f"Error generating {plot_name}: {e}")

