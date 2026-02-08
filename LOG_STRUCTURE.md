# MBB log file layout

*note: values in raw logs are little endian*

## Format Variations

This document describes three observed MBB log file formats:

### Legacy Format (Original Documentation)
- Static data at fixed addresses (0x200, 0x240, etc.)
- Log sections with headers at predictable locations

### Ring Buffer Format (2024 firmware)
- File starts directly with log entries at offset 0x0000
- No static data at documented addresses
- Section headers located at end of file
- File size: exactly 0x40000 bytes (262144 bytes)
- Human-readable diagnostic messages ("Riding", "Key On", etc.)
- Full message payloads with verbose descriptions

### Compressed Telemetry Format (2025+ firmware)
- File size: 131,200 bytes (exactly half of ring buffer format)
- Linear dump structure without ring buffer headers
- Structured telemetry data prioritized over human-readable messages
- Compressed message encoding with abbreviated hex patterns
- New message types: Vehicle State (Type 81), Sensor Data (Type 84)
- Entry count: ~2,200 vs ~6,600 in ring buffer format

## Parser Improvements (v2.2.0+)

Based on JavaScript parser analysis, the following enhancements have been implemented:

### Enhanced Message Type Support
- **0x05**: BMS Unknown Type 5 - now displays raw hex data
- **0x0e**: BMS Unknown Type 14 - now displays raw hex data  
- **0x1c**: MBB Unknown Type 28 - now displays raw hex data
- **0x26**: MBB Unknown Type 38 - now displays raw hex data
- **0x37**: MBB BT RX Buffer Overflow - now properly decoded

### Enhanced BMS Discharge Decoder (0x03)
- **State name translation**: Converts numeric states to readable names:
  - 0x01 = 'Bike On'
  - 0x02 = 'Charge' 
  - 0x03 = 'Idle'
- **Improved formatting**: Better field labels and unit conversions
- **Enhanced precision**: Proper handling of signed current values

### Improved Calculations
- **Discharge Cutback (0x16)**: Uses JavaScript parser formula for percentage calculation
- **Voltage conversions**: Standardized voltage display with proper decimal precision
- **Current handling**: Enhanced signed 32-bit current parsing

These improvements ensure better compatibility with existing log analysis tools and provide more comprehensive message decoding.

## Static addresses (Legacy Format Only)

Address    | Length | Contents
---------- | :----: | --------
0x00000200 | 21     | Serial number
0x00000240 | 17     | VIN number
0x0000027b | 2      | Firmware revision
0x0000027d | 2      | Board revision
0x0000027f | 3      | Bike model (`SS`, `SR`, `DS`, 'FX')

## Ring Buffer Format Layout (2024 firmware)

In 2024 firmware log files, the structure is:

Address    | Length | Contents
---------- | :----: | --------
0x00000000 | variable | Log entries start immediately (ring buffer)
0x0003BA0E | 24     | First run date section (0xa1a1a1a1 + date string)
0x0003BD10 | ~13    | Serial number (e.g., "RKT212200313")
0x0003BF00 | 16+    | Error log section (0xa3a3a3a3 + header info)
0x0003C100 | 16+    | Event log section (0xa2a2a2a2 + header info)

*Note: Addresses may vary between files. Section headers should be located by scanning for the 4-byte sequences.*

## Compressed Telemetry Format Layout (2025+ firmware)

The 2025+ firmware introduces a significantly different log structure optimized for telemetry collection:

### Key Changes from Ring Buffer Format:
- **File Size**: 131,200 bytes (exactly 50% of ring buffer format)
- **Structure**: Linear entry dump without section headers
- **Data Focus**: Structured JSON telemetry vs human-readable diagnostic text
- **Message Density**: ~2,200 entries vs ~6,600 in ring buffer format
- **Encoding**: Binary/hex abbreviated patterns vs full text descriptions

### Message Type Evolution:
The 2025+ format replaces verbose diagnostic messages with structured telemetry:

**Ring Buffer Format (2024):**
- 1,754× "Riding" entries with full vehicle state text
- 264× "Module XX Opening Contactor" descriptive messages
- 156× "Sevcon CAN Link Up" verbose diagnostics

**Compressed Format (2025+):**
- 151× Vehicle State Telemetry (Type 81) with JSON data
- 152× Sensor Data (Type 84) with structured readings
- 45× Abbreviated hex patterns (e.g., "0x2c 0x01", "0x28 0x02")

### Abbreviated Message Patterns:
The new format uses compressed hex identifiers instead of full text:

Pattern | Count | Meaning | 2024 Equivalent
--------|-------|---------|----------------
`0x28 0x02` | 52× | Battery CAN Link Up, Module 02 | "Module 02 CAN Link Up"
`0x01` | 124× | Board Status (abbreviated) | "Board Status"
`0x2c 0x01` | 45× | Riding Status (compressed) | "Riding" (full telemetry)
`0x7a 0x01` | 44× | Unknown MBB Type 122 | (New in 2025+)
`0x58 0x15 0x01` | 35× | Unknown complex pattern | (New in 2025+)

### Vehicle State Machine (from Type 81 analysis):

State | Full Name | Odometer Behavior | Interpretation
------|-----------|-------------------|---------------
`WSU` | Wakeup/Startup | Large drops (-51 to -83km avg) | Trip odometer reset/different source
`IB` | In-motion/Battery active | Increases (+46-48km avg) | Normal riding operation
`UN` | Unknown/Neutral/Idle | Small changes (-3 to +2km) | Parked/idle state
`TOP` | Trip completion/Peak | Large increases (+101-247km) | End-of-trip summary/total odometer
`AIT` | Auto/Initialization/Timer | Mixed large changes | System initialization events
`AKE` | Awake/Active | Very large drops (-423km avg) | Major system reset
`HRG` | Charging/High-voltage | Mixed, includes large drops | Charging mode
`EV` | Electric Vehicle mode | Large drop (-142km) | Explicit EV operation mode
`RUN` | Running | Normal progression | Active vehicle operation  
`STOP` | Stopped | Normal progression | Vehicle stopped
`STRT` | Starting | Normal progression | Vehicle startup sequence
`PWSU` | Power Supply/Startup | Normal progression | Power system initialization

**Key Insight**: The "odometer inconsistencies" are actually multiple concurrent odometer sources (trip, session, total) being switched based on vehicle state. This is normal behavior, not data corruption.

## Log sections (located by header sequence)

### Unknown *(build date?)*

Offset     | Length | Contents
---------- | :----: | --------
0x00000000 | 4      | `0xa0 0xa0 0xa0 0xa0` section header
0x00000004 | 20     | Date and time (ascii)

### Unknown *(first run date?)*

Offset     | Length | Contents
---------- | :----: | --------
0x00000000 | 4      | `0xa1 0xa1 0xa1 0xa1` section header
0x00000004 | 20     | Date and time (ascii)

### Event Log

Offset     | Length     | Contents
---------- | :--------: | --------
0x00000000 | 4          | `0xa2 0xa2 0xa2 0xa2` section header
0x00000004 | 4          | Log entries end address
0x00000008 | 4          | Log entries start address
0x0000000c | 4          | Log entries count
0x00000010 | *variable* | Log data begins

### Error Log

Offset     | Length     | Contents
---------- | :--------: | --------
0x00000000 | 4          | `0xa3 0xa3 0xa3 0xa3` section header
0x00000004 | 4          | Log entries end address
0x00000008 | 4          | Log entries start address
0x0000000c | 4          | Log entries count
0x00000010 | *variable* | Log data begins

The event log file appears to be a direct memory dump from a ring buffer. All logs export as 0x3ffff bytes in length. Bikes logs which exceed this offset begin overwriting themself from the top of the log data section.

# BMS log file layout

Address    | Length | Contents
---------- | :----: | --------
0x00000000 | 3      | `BMS`
0x0000000e | 4      | `0xa1 0xa1 0xa1 0xa1` section header
0x00000012 | 20     | *First run date?*
0x00000300 | 21     | Serial number
0x00000320 | 8      | Pack serial number
0x0000036a | 4      | `0xa0 0xa0 0xa0 0xa0` section header
0x0000036e | 20     | *Date / time - unknown, but close to time @ 0x00000012*
0x00000500 | 4      | `0xa3 0xa3 0xa3 0xa3` section header
0x00000700 | 4      | `0xa2 0xa2 0xa2 0xa2` section header
0x00000704 |        | Main log begins

# Log entry format (shared by MBB and BMS)

Offset | Length    | Contents
------ | :-------: | --------
0x00   | 1         | `0xb2` Entry header
0x01   | 1         | Entry length (including header byte)
0x02   | 1         | Entry type - see section `Log file entry types`
0x03   | 4         | Timestamp (seconds since the Unix epoch)
0x07   | *variable* | Entry data

Note that the entry appears to be encoded in some format starting from the entry type onwards (ie Entry type, timestamp, Entry data). Any bytes of 0xFE are xor'ed with the next byte -1.

For example, the byte sequenze 0xFE, 0x01 becomes 0xFE. The byte sequence OxFE, 0x4d becomes 0xb2. The length of the message is reduced accordingly.

## Log file entry types

### `0x0` - board status
Offset | Length | Contents
------ | :----: | --------
0x00   | 1      | cause of reset

### `0x2` - MBB High Throttle Disable.
Offset | Length | Contents
------ | :----: | --------
0x00   | 2      | thr in mv
0x02   | 3      | unknown

### `0x3` - BMS discharge level (Enhanced)
**Parser improvements**: Enhanced with state name translation and improved formatting.
Offset | Length | Contents
------ | :----: | --------
0x00   | 2      | Low cell voltage (mV)
0x02   | 2      | High cell voltage (mV) 
0x04   | 1      | Pack temperature (°C)
0x05   | 1      | BMS temperature (°C)
0x06   | 4      | Amp hours (µAh, converted to Ah in parser)
0x0a   | 1      | State of Charge (%)
0x0b   | 4      | Pack voltage (mV, converted to V in parser)
0x0f   | 1      | State: 0x01 = 'Bike On', 0x02 = 'Charge', 0x03 = 'Idle'
0x10   | 4      | Current (µA signed, converted to A in parser)
0x14   | 2      | Unloaded cell voltage (mV)
0x16   | 2      | Additional data
Balance = High cell - Low cell

### `0x4` - BMS charge full
Offset | Length | Contents
------ | :----: | --------
0x00   | 2      | L low cell
0x02   | 2      | H high cell
0x04   | 1      | PT pack temp
0x05   | 1      | BT board? temp
0x06   | 4      | AH microamp hours
0x0a   | 1      | SOC %
0x0b   | 4      | PV pack voltage mv
B balance = H - L

### `0x5` - BMS Unknown Type 5 (Implemented)
**Parser status**: Now implemented with raw hex display.
Offset | Length | Contents
------ | :----: | --------
0x00   | 17     | Unknown data (displayed as hex in parser)

### `0x6` - BMS discharge low
Offset | Length | Contents
------ | :----: | --------
0x00   | 2      | L low cell
0x02   | 2      | H high cell
0x04   | 1      | PT pack temp
0x05   | 1      | BT board? temp
0x06   | 4      | AH microamp hours
0x0a   | 1      | SOC %
0x0b   | 4      | PV pack voltage mv
B balance = H - L

### `0x8` - BMS system
Offset | Length | Contents
------ | :----: | --------
0x00   | 1      | state 0 = 'Off', 1 = 'On'

### `0x09` - key state
Offset | Length | Contents
------ | :----: | --------
0x00   | 1      | state 0 = 'Off', 1 = 'On'

### `0xb` - BMS SOC adjusted for voltage
Offset | Length | Contents
------ | :----: | --------
0x00   | 4      | old uAH
0x02   | 1      | old SOC
0x04   | 4      | new uAH
0x05   | 1      | new SOC
0x06   | 2      | low cell mV

### `0xd` - BMS Current Sensor Zeroed
Offset | Length | Contents
------ | :----: | --------
0x00   | 2      | old mV
0x02   | 2      | new mV
0x04   | 1      | corrfact

### `0xe` - BMS Unknown Type 14 (Implemented)
**Parser status**: Now implemented with raw hex display.
Offset | Length | Contents
------ | :----: | --------
0x00   | 3      | Unknown data (displayed as hex in parser)

### `0x10` - BMS Hibernate
Offset | Length | Contents
------ | :----: | --------
0x00   | 1      | state 0 = 'Exiting', 1 = 'Entering'

### `0x11` - BMS Chassis Isolation Fault
Offset | Length | Contents
------ | :----: | --------
0x00   | 4      | ohms to cell
0x04   | 1      | cell

### `0x12` - BMS Reflash
Offset | Length | Contents
------ | :----: | --------
0x00   | 1      | Rev
0x02   | 20     | Built date time string

### `0x13` - BMS Changed CAN Node ID
Offset | Length | Contents
------ | :----: | --------
0x00   | 1      | old
0x01   | 1      | new

### `0x15` - BMS Contactor
Offset | Length | Contents
------ | :----: | --------
0x00   | 1      | state 0 = 'Contactor was Opened 1 = 'Contactor was Closed'
0x01   | 4      | Pack mV
0x05   | 4      | Switched mV
0x09   | 4      | Dischg Cur mA

### `0x16` - BMS Discharge cutback
Offset | Length | Contents
------ | :----: | --------
0x00   | 1      | cut % (/255*100.0)

### `0x18` - BMS Contactor drive turned on
Offset | Length | Contents
------ | :----: | --------
0x01   | 4      | Pack mV
0x05   | 4      | Switched mV
0x09   | 1      | Duty cycle %

### `0x1c` - MBB Unknown Type 28 (Implemented)
**Parser status**: Now implemented with raw hex display.
Offset | Length | Contents
------ | :----: | --------
0x00   | 8      | Unknown data (displayed as hex in parser)

### `0x1e` - MBB unknown
Offset | Length | Contents
------ | :----: | --------
0x00   | 4      | ???

### `0x1f` - MBB unknown
Offset | Length | Contents
------ | :----: | --------
0x00   | 4      | ???

### `0x20` - MBB unknown
Offset | Length | Contents
------ | :----: | --------
0x00   | 3      | ???

### `0x26` - MBB Unknown Type 38 (Implemented)
**Parser status**: Now implemented with raw hex display.
Offset | Length | Contents
------ | :----: | --------
0x00   | 6      | Unknown data (displayed as hex in parser)

### `0x28` - battery CAN link up
Offset | Length | Contents
------ | :----: | --------
0x00   | 1      | module number

### `0x29` - battery CAN link down
Offset | Length | Contents
------ | :----: | --------
0x00   | 1      | module number

### `0x2a` - Sevcon CAN link up
*(no additional data)*

### `0x2b` - Sevcon CAN link down
*(no additional data)*

### `0x2c` - Riding / run status
Offset | Length | Contents
------ | :----: | --------
0x00   | 1      | pack temp (high)
0x01   | 1      | pack temp (low)
0x02   | 2      | pack state of charge (%)
0x04   | 4      | pack voltage - fixed point, scaling factor 1/1000
0x08   | 1      | motor temp
0x0a   | 1      | controller temp
0x0c   | 2      | motor RPM
0x10   | 2      | battery current
0x12   | 1      | mods (??)
0x13   | 2      | motor current
0x15   | 2      | ambient temperature
0x17   | 4      | odometer

*note: all temperatures in degrees celcius*

### `0x2d` - Charging status
Offset | Length | Contents
------ | :----: | --------
0x00   | 1      | pack temp (high)
0x01   | 1      | pack temp (low)
0x02   | 2      | pack state of charge (%)
0x04   | 4      | pack voltage - fixed point, scaling factor 1/1000
0x08   | 1      | battery current
0x0c   | 1      | mods (??)
0x0d   | 2      | ambient temperature

*note: all temperatures in degrees celcius*

### `0x2f` - sevcon status
Offset | Length | Contents
------ | :----: | --------
0x00   | 2      | error code
0x02   | 2      | sevcon error code
0x04   | 1      | error reg
0x05   | 1+     | error data

### `0x30` - charger status
Offset | Length | Contents
------ | :----: | --------
0x00   | 1      | Module type
0x01   | 1      | Module state

### `0x31` - MBB BMS Isolation Fault
Offset | Length | Contents
------ | :----: | --------
0x00   | 1      | BMS module
0x01   | 3      | unknown

### `0x33` - battery status
Offset | Length | Contents
------ | :----: | --------
0x00   | 1      | `0x00`=disconnecting, `0x01`=connecting, `0x02`=registered
0x01   | 1      | module number
0x02   | 4      | module voltage - fixed point, scaling factor 1/1000
0x06   | 4      | maximum system voltage - fixed point, scaling factor 1/1000
0x0a   | 4      | minimum system voltage - fixed point, scaling factor 1/1000

### `0x34` - power state
Offset | Length | Contents
------ | :----: | --------
0x00   | 1      | state
0x01   | 1      | `0x01`=key switch, `0x04`=onboard charger

### `0x35` - MBB unknown
Offset | Length | Contents
------ | :----: | --------
0x00   | 5      | ???

### `0x36` - Sevcon power state
Offset | Length | Contents
------ | :----: | --------
0x00   | 1      | state

### `0x37` - MBB BT RX Buffer Overflow (Implemented)
**Parser status**: Now implemented with proper decoding.
Offset | Length | Contents
------ | :----: | --------
0x00   | 3      | Buffer overflow data (displayed as hex in parser)

### `0x38` - bluetooth state
*(no additional data)*

### `0x39` - battery discharge current limited
Offset | Length | Contents
------ | :----: | --------
0x00   | 2      | discharge current

### `0x3a` - Low Chassis isolation
Offset | Length | Contents
------ | :----: | --------
0x00   | 4      | kOhms of isolation
0x04   | 1      | Cell affected

### `0x3b` - precharge decay too steep
*(no additional data)*

### `0x3c` - disarmed status
Offset | Length | Contents
------ | :----: | --------
0x00   | 1      | pack temp (high)
0x01   | 1      | pack temp (low)
0x02   | 2      | pack state of charge (%)
0x04   | 4      | pack voltage - fixed point, scaling factor 1/1000
0x08   | 2      | motor temp
0x0a   | 2      | controller temp
0x0c   | 2      | motor RPM
0x10   | 2      | battery current
0x12   | 1      | mods (??)
0x13   | 2      | motor current
0x15   | 2      | ambient temperature
0x17   | 4      | odometer

### `0x3d` - battery module contactor closed
Offset | Length | Contents
------ | :----: | --------
0x00   | 1      | module number

### `0x3e` - cell voltages
Offset | Length | Contents
------ | :----: | --------
0x00   | 2      | cell 1 mV
0x02   | 2      | cell 2 mV
0x04   | 2      | cell 3 mV
0x06   | 2      | cell 4 mV
0x08   | 2      | cell 5 mV
0x0a   | 2      | cell 6 mV
0x0c   | 2      | cell 7 mV
0x10   | 2      | cell 8 mV
0x12   | 2      | cell 9 mV
0x14   | 2      | cell 10 mV
0x16   | 2      | cell 11 mV
0x18   | 2      | cell 12 mV
0x1a   | 2      | cell 13 mV
0x1c   | 2      | cell 14 mV
0x1e   | 2      | cell 15 mV
0x20   | 2      | cell 16 mV
0x22   | 2      | cell 17 mV
0x24   | 2      | cell 18 mV
0x26   | 2      | cell 19 mV
0x28   | 2      | cell 20 mV
0x2a   | 2      | cell 21 mV
0x2c   | 2      | cell 22 mV
0x2e   | 2      | cell 23 mV
0x30   | 2      | cell 24 mV
0x32   | 2      | cell 25 mV
0x34   | 2      | cell 26 mV
0x36   | 2      | cell 27 mV
0x38   | 2      | cell 28 mV

### `0x51` (Type 81) - Vehicle State Telemetry
*Added in 2025+ firmware - replaces periodic "Riding" entries*

**Purpose**: Primary vehicle telemetry data collection (151 entries per log session)

Offset | Length | Contents
------ | :----: | --------
0x00   | 68     | Vehicle state telemetry data (68 bytes total)

**Decoded structure (little-endian):**
- Bytes 36-39: Vehicle state string ("TOP", "RUN", "STOP", "UN", "STRT", "PWSU", etc.)
- Bytes 0-3: Odometer reading (meters)
- Bytes 4-7: SOC raw value (State of Charge)
- Bytes 8-11: Ambient temperature raw value
- Bytes 12-15: Temperature sensor 1 (°C)
- Bytes 16-19: Temperature sensor 2 (°C)  
- Bytes 20-23: Temperature sensor 3 (°C)
- Bytes 24-27: Temperature sensor 4 (°C)
- Remaining bytes: Additional telemetry values (motor data, power readings)

**Example JSON output:**
```json
{
  "vehicle_state": "RUN",
  "odometer_m": 688000,
  "soc_raw": 426,
  "ambient_temp_raw": 1966,
  "temp_1": 23,
  "temp_2": 24,
  "temp_3": 24,
  "temp_4": 22
}
```

### `0x54` (Type 84) - Sensor Data
*Added in 2025+ firmware - supplementary sensor telemetry*

**Purpose**: Additional sensor readings and system status (152 entries per log session)

Offset | Length | Contents
------ | :----: | --------
0x00   | 22     | Sensor telemetry data (22 bytes total)

**Decoded structure (little-endian):**
- Bytes 0-3: Odometer reading (meters)
- Bytes 4-7: Sensor reading 1 (power/voltage related)
- Bytes 8-11: Sensor reading 2 (system status)
- Bytes 12-15: Sensor reading 3 (temperature/performance)
- Bytes 16-19: Sensor reading 4 (motor/controller data)
- Bytes 20-21: Status flags (uint16)

**Example JSON output:**
```json
{
  "odometer_m": 689000,
  "sensor_1": 429,
  "sensor_2": 4278583296,
  "sensor_3": 4390911,
  "sensor_4": 1739784192,
  "status": 1
}
```

**Sensor Field Analysis (from multi-vehicle data):**

Field | Value Range | Vehicle State Correlation | Interpretation
------|-------------|---------------------------|---------------
`sensor_1` | 200-4.2B | WSU/IB: low (200-600)<br>UN/TOP: high (millions) | Primary sensor, state-dependent
`sensor_2` | 0-4.3B | WSU: always 0<br>HRG: very high<br>Mixed otherwise | Secondary sensor, 32-bit flags
`sensor_3` | 0-57M | WSU: always 0<br>Others: variable | Tertiary sensor, temp/voltage?
`sensor_4` | 0-4.29B | Frequently 4294901760 (0xFFF00000)<br>State-dependent variations | Status/flag register
`status` | 0-65535 | 16-bit status register<br>Varies by vehicle and state | System status flags

**Sensor Patterns by State:**
- **WSU (Wakeup)**: sensor_2,3→0, sensor_4→max (clean startup)
- **IB (In-motion)**: sensor_1→low, sensor_4→max (active operation) 
- **HRG (Charging)**: sensor_1→low, sensor_2→high (charging mode)
- **UN (Idle)**: High sensor values (monitoring/background)

### `0x52` (Type 82) - Unknown Type 82
*Added in 2025+ firmware - frequently occurring entry*

**Purpose**: Unknown structured data (20 entries per log session)

Offset | Length | Contents
------ | :----: | --------
0x00   | variable | Complex structured data with vehicle state and sensor information

**Observed patterns:**
- Contains vehicle state strings ("RUN", "STOP")
- Includes odometer and power readings
- Mixed binary and text data
- Requires further reverse engineering

### `0xfb` (Type 251) - System Information
*Added in 2025+ firmware - appears once per log session*

**Purpose**: System identification and configuration data

Offset | Length | Contents
------ | :----: | --------
0x00   | ~110   | System information block

**Decoded contents:**
- **MBB identifier**: "MBB" string at start
- **VIN number**: 17-character vehicle identification (e.g., "538DZAZ81PCN25719")
- **Serial numbers**: Multiple system component serials (e.g., "RKT2302023467")
- **Firmware version**: Version strings (e.g., "40-08198")
- **System configuration**: Hardware IDs and setup parameters
- **Checksums**: Data integrity verification values

**Example data extracted:**
```
VIN: 538DZAZ81PCN25719
Serial: RKT2302023367
Firmware: 40-08198
System ID: 39e9c5ea1
```

### `0xfd` - debug string
Offset | Length     | Contents
------ | :--------: | --------
0x00   | *variable* | message text (ascii)

---

## Format Detection and Parser Implementation Notes

### Identifying Log Format Version:
1. **File Size Check**:
   - 262,144 bytes (0x40000) → Ring Buffer Format (2024)
   - 131,200 bytes → Compressed Telemetry Format (2025+)
   - Other sizes → Legacy Format

2. **Header Pattern Detection**:
   - Look for section headers (0xa0-0xa3) → Ring Buffer Format
   - Starts with 0xb2 entries → Compressed Telemetry Format
   - Static data at 0x200+ → Legacy Format

3. **Message Type Indicators**:
   - Presence of Type 81/84/251 → 2025+ firmware
   - High frequency of "Riding" messages → 2024 firmware
   - Static addresses populated → Legacy firmware

### Parser Implementation Guidelines:

**For 2025+ Compressed Format:**
- Handle abbreviated hex patterns as valid message types
- Implement JSON decoders for Type 81/84 structured data
- Parse Type 251 for system identification
- Process compressed message identifiers (0x28 0x02, etc.)
- Expect lower entry count but higher data density

**For 2024 Ring Buffer Format:**
- Parse verbose diagnostic messages
- Handle full "Riding" telemetry entries
- Process section headers for data location
- Expect higher entry count with descriptive text

**Cross-Format Compatibility:**
- The core entry structure (0xb2 header + length + type + timestamp) remains consistent
- Message interpretation and payload structure varies significantly
- Use format detection to select appropriate parsing logic
