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
- **0x1c**: BMS Disable - Low Bat (decoded, see below)
- **0x26**: High Mot/Ctrl (decoded, see below)
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

## FST Platform Format Layout

### Key Changes from Ring Buffer Format:
- **File Size**: 131,200 bytes (MBB) or 131,328 bytes (BMS), both page-aligned to 128 bytes
- **Structure**: `0xFB` log-header entry at offset 0, then log entries
- **Data Focus**: Structured binary telemetry vs human-readable diagnostic text
- **Message Density**: ~2,200 entries vs ~6,600 in ring buffer format
- **Encoding**: Little-endian structs with `0xFE` byte-stuffing

### Entry framing and where sizes are measured

Every entry has a fixed 13-byte header, followed by the payload:

Offset | Length | Contents
------ | :----: | --------
0x00   | 1      | `0xb2` entry start
0x01   | 1      | Entry length on disk, including the header and any escape bytes (max 128)
0x02   | 1      | Entry type
0x03   | 4      | Timestamp, seconds since the Unix epoch (u32le)
0x07   | 4      | Microseconds (u32le).
0x0b   | 1      | Rolling entry counter (u8)
0x0c   | 1      | UTC offset in hours (s8), from the bike's time-correction setting
0x0d   | *variable* | Payload

The UTC offset: `0xF9` (-7) and `0xF8` (-8) are, for example, US Pacific daylight and standard
time, and `0x01`/`0x02` are UTC+1/UTC+2.

From the type byte onwards, each `0xB2` or `0xFE` is written as `0xFE` followed by the byte's
complement (`0xB2` becomes `fe 4d`, `0xFE` becomes `fe 01`), and the length byte counts the
escaped size.

### Message Type Evolution:
The FST platform format replaces verbose diagnostic messages with structured telemetry:

**Ring Buffer Format (2024):**
- 1,754× "Riding" entries with full vehicle state text
- 264× "Module XX Opening Contactor" descriptive messages
- 156× "Sevcon CAN Link Up" verbose diagnostics

**FST Platform Format:**
- 151× telemetry snapshots (0x51)
- 152× motor/power snapshots (0x54)

### Vehicle State Machine (from 0x51 `state` field)

The `state` field is a 5-byte null-padded ASCII string at **payload offset 29**.

Index | State  | Meaning (inferred from the name)
----- | ------ | --------------------------------
0     | `NONE` | No state
1     | `STRT` | Starting up
2     | `PWSU` | Power supply up
3     | `WAKE` | Waking from hibernate
4     | `HIB`  | Hibernating
5     | `STOP` | Stopped / shutting down
6     | `RUN`  | Riding
7     | `LIMP` | Limp mode
8     | `CHRG` | Charging
9     | `WAIT` | Waiting (parked, key off)
10    | `FWUP` | Firmware update
11    | `REV`  | Reverse
12    | `PARK` | Park mode

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

### `0xf` - BMS Long Term Storage Stats
Fixed 10 bytes. Logged about every 12 hours in long-term storage mode, right
after the "LTSM stats: ..." debug string and right before a `0x3` discharge
level entry that repeats its cell voltages and BMS temperature.
Offset | Length | Contents
------ | :----: | --------
0x00   | 1      | Unknown (always 1-28)
0x01   | 1      | Unknown (always 1-28, never equal to 0x00)
0x02   | 1      | Balance resistors active (matches "Num bal res active" in the LTSM string)
0x03   | 2      | L low cell (mV)
0x05   | 2      | H high cell (mV)
0x07   | 2      | B balance = H - L (mV)
0x09   | 1      | BT BMS temp (°C)

### `0x10` and `0x11` - a namespace collision between BMS and classic MBB

The classic BMS and MBB firmwares both write events at these two type
ids, unrelated events, told apart by which file the entry is in
(`Gen2._entry_parsers(log_type=...)`, dispatched from the file's own
`LogFile.log_type`).
An Unknown Type file (`LogFile.log_type` neither MBB nor BMS) is also
routed to the MBB decoders when `LogFile.has_classic_vin` finds a real,
checksum-valid VIN at the classic offset (0x240 or 0x252) - a narrow,
file-level signal, safe on every real BMS log checked, that covers
part of the Unknown Type gap.

**BMS files:**

#### `0x10` - BMS Hibernate
Offset | Length | Contents
------ | :----: | --------
0x00   | 1      | state 0 = 'Exiting', 1 = 'Entering'

#### `0x11` - BMS Chassis Isolation Fault
Offset | Length | Contents
------ | :----: | --------
0x00   | 4      | ohms to cell
0x04   | 1      | cell

**MBB files** (the classic MBB firmware's own renderer names, previously
routed through the BMS decoders above regardless of file type - 5,733
entries in the real logs checked):

#### `0x10` - BMS Throt En Wire Disable
Offset | Length | Contents
------ | :----: | --------
0x00   | 4      | vpack, mV (uint32 LE)
0x04   | 4      | thr_en, mV (uint32 LE)

#### `0x11` - BMS Throt Wire Re-enable
Offset | Length | Contents
------ | :----: | --------
0x00   | 4      | vpack, mV (uint32 LE)
0x04   | 4      | thr_en, mV (uint32 LE)

Both decode identically (`vpack_voltage_volts`, `thr_en_voltage_volts`);
only the event name differs. Any length other than 8 bytes falls back to
the raw-hex report.

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

### `0x1c` - BMS Disable - Low Bat
Offset | Length | Contents
------ | :----: | --------
0x00   | 4      | pack sum, mV (uint32)
0x04   | 1      | capacity, % (uint8)
0x05   | 1      | module number
0x06   | 2      | status (uint16)

*Source: the classic MBB firmware's own event-log renderer and log writer (MY17 MBB image 75-08036-40), checked against the full classic MBB population with the real entry walker; see `analysis/mbb_firmware_strings.md`.*

### `0x1e` - BMS Disable - High Temp
Offset | Length | Contents
------ | :----: | --------
0x00   | 1      | pack temp, degrees C (int8; -100 = invalid thermistor)
0x01   | 1      | module number
0x02   | 2      | status (uint16)

### `0x1f` - BMS Disable - Low Temp
Same layout as `0x1e`.

### `0x20` - Batt Temp
Offset | Length | Contents
------ | :----: | --------
0x00   | 1      | state: `0`=Batt Temp Okay, `1`/`2`=Batt High Temp Stage 1/2, `3`=Batt Low Temp (other values: no name, raw fallback)
0x01   | 1      | pack temp, degrees C (int8)
0x02   | 1      | module number

*A `0x1e`/`0x1f` entry is usually followed in the same second by a `0x20` High Temp Stage 2 / Low Temp entry carrying the same pack temp and module.*

### `0x26` - High Mot/Ctrl
Offset | Length | Contents
------ | :----: | --------
0x00   | 2      | motor temp, degrees C (uint16)
0x02   | 2      | controller temp, degrees C (uint16)
0x04   | 2      | not read by the firmware renderer; kept in `raw_hex`

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

### `0x35` - Exceeded Max Charge Amps
Two forms, both handled by the firmware renderer's own length branch.

5 bytes:

Offset | Length | Contents
------ | :----: | --------
0x00   | 1      | module number
0x01   | 1      | amps exceeded (int8)
0x02   | 1      | seconds exceeded
0x03   | 2      | amps at log (int16; charging current is negative)

6 bytes:

Offset | Length | Contents
------ | :----: | --------
0x00   | 1      | module number
0x01   | 2      | amps exceeded (int16)
0x03   | 1      | seconds exceeded
0x04   | 2      | amps at log (int16)

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

### `0x3d` - two forms by length

`0x3d` carries two different events depending on firmware era, told apart
by payload length. Both are real: 4,681 entries in the file set are the
1-byte form, almost all from 2013 model year bikes; the rest are the
4-byte form.

**1 byte - Battery module N contactor closed** (early firmware, 2013
model year bikes)

Offset | Length | Contents
------ | :----: | --------
0x00   | 1      | module number (uint8)

This is the original upstream reading, unchanged. It typically follows a
"Module N FETs are now Closed" / "Contactor took N ms to close" debug
string.

**4 bytes - Sevcon Failed To Fully Precharge**

Offset | Length | Contents
------ | :----: | --------
0x00   | 4      | controller capacitor voltage reached, mV (uint32)

The value is the same firmware quantity `0x33` logs at offset `0x0e`
(vcap), logged when the precharge percentage stays under 98%. Before this
was found, every `0x3d` entry (both forms) was misread as the 1-byte
module-number event, which gave 4-byte entries nonsense like "Battery
module 233 contactor closed".

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

### `0x51` / `0x52` / `0x53` (Types 81-83) - Vehicle State Telemetry
*FST/Gen3 MBB, periodic*

Three sizes of one record: small (0x51, 64 or 68 bytes), medium (0x52, 81 or 85)
and large (0x53, 95 or 99, plus a rare 93). Each size has a variant 4 bytes longer
that adds the 12 V battery voltage. Field names and units follow John Myers'
(atomicdog) Gen3 log format datasheet. Offsets below are relative to the 4-byte
NUL-padded ASCII state tag (`RUN`, `STOP`, `HIB`, `PWSU`, `CHRG`, ...), which sits
at payload offset 35 (0x51), 39 (0x52) or 43 (0x53). All values are little-endian.

Offset (tag-relative) | Length | Contents
------ | :----: | --------
-25    | 1      | Status flags (validity of the controller and BMS groups), shown as `motor_controller_data_valid` / `bms_data_valid`
-21    | 3      | DC bus voltage, mV, shown as `dc_bus_voltage_volts`
-17    | 4      | DC bus current, mA (s32), shown as `dc_bus_current_amps`
-13    | 2      | Motor RPM, shown as `motor_rpm`
-9     | 1      | State of charge, %
-8     | 4      | Pack voltage, mV, shown as `pack_voltage_volts`
-4     | 4      | Pack current, mA (s32), shown as `battery_current_amps`
0      | 4      | State tag
+5     | 1      | `temp_flags`: one "read failed" bit per temperature (see below)
+9     | 4      | Ambient temperature (s32), shown as `ambient_temperature_c`; unit depends on firmware (see below)
+13    | 4      | Motor temperature, C (s32), shown as `motor_temp_c`
+17    | 4      | Inverter temperature, C (s32), shown as `inverter_temp_c`
+21    | 4      | Pack warmest cell temperature, C (s32), shown as `pack_temperature_warmest_c`
+25    | 4      | Pack coldest cell temperature, C (s32), shown as `pack_temperature_coldest_c`
+29    | 3      | 12 V battery voltage, uV, shown as `battery_12v_volts` (long variants only)
+35 / +31 | 3   | Distance counter, 0.1 km, shown as `distance_km` (0x52 / 0x53; long / short variant)
+39 / +35 | 4   | Speed, mm/h (s32), shown as `speed_kmh` = value / 1,000,000; null when negative
+43 / +39 | 2   | Estimated range, km x 100, shown as `range_km` = value / 100; null when 0xFFFF

**Firmware-dependent fields.** The MBB firmware revision an entry was written
under is the file header's revision, or, for an entry timestamped before a 0x4E
firmware-updated entry in the same file, that update's previous revision.

- Status flags and `temp_flags` use one bit map on rev 18 and later and the same
  map shifted up one bit on rev 13-17. A status bit that exists in only one map
  (0x2A rev 18+, 0x94 rev 13-17) decides the map for that entry; otherwise the
  firmware revision does.
- `temp_flags`, rev 18+ map: bit 1 ambient, bit 2 motor, bit 3 inverter, bit 4
  pack warmest, bit 5 pack coldest (rev 13-17: bits 2-6). A temperature whose bit
  is set is null.
- Ambient is C x 100 on rev 13-17 and 20+ and plain C on rev 18-19. A raw value
  outside -40..60 is C x 100 on any firmware. A raw value inside it on rev 20+ is
  C x 100 only when the same file holds an earlier C x 100 reading (with no update
  to rev 18-19 in between); otherwise the scale is not settled and ambient is null.
  A scaled value below -40 C or above 80 C is null.
- Motor and inverter temperatures, DC bus fields, RPM and speed are null when the
  controller group is not reporting; pack voltage and current are null when the
  BMS group is not reporting.

**Example JSON output** (0x52, 85 bytes, invented values):
```json
{
  "telemetry_tier": "medium",
  "state": "RUN",
  "subsecond_us": 400000,
  "sequence": 3,
  "marker": 1,
  "state_of_charge_percent": 77,
  "pack_voltage_volts": 108.0,
  "battery_current_amps": 9.0,
  "motor_controller_data_valid": true,
  "bms_data_valid": true,
  "temp_flags": 0,
  "dc_bus_voltage_volts": 104.0,
  "dc_bus_current_amps": 25.0,
  "motor_rpm": 3100,
  "ambient_temperature_c": 23.5,
  "motor_temp_c": 41,
  "inverter_temp_c": 33,
  "pack_temperature_warmest_c": 22,
  "pack_temperature_coldest_c": 21,
  "battery_12v_volts": 13.1,
  "distance_km": 6268.4,
  "speed_kmh": 56.25,
  "range_km": 123.45,
  "raw_hex": "..."
}
```

0x51 entries also carry `vehicle_state` (instead of `telemetry_tier` and `state`),
and their event is `Riding` in RUN and `Vehicle State (<state>)` otherwise. Earlier
versions also emitted `temperature_1_celsius` .. `temperature_4_celsius` on 0x51:
the same four bytes as the motor, inverter and pack temperatures, read as unsigned
bytes (so -1 showed as 255 and flagged sensors as real readings). They are no longer
emitted; use the named fields.

### `0x54` (Type 84) - Torque Info
*FST/Gen3 MBB, 22-byte payload*

Layout from John Myers' (atomicdog) Gen3 log format datasheet. Payload offset =
datasheet offset + 6: the payload starts with the shared 6-byte prefix
(sub-second microseconds, sequence, marker). All values are little-endian.

Offset | Length | Contents
------ | :----: | --------
0x00   | 4      | Sub-second, microseconds (u32), shown as `subsecond_us`
0x04   | 1      | Sequence counter, shown as `sequence`
0x05   | 1      | Marker byte, shown as `marker`
0x06   | 4      | Torque, micro-Nm (s32), shown as `torque_nm` in Nm
0x0A   | 4      | Torque demand, percent x 10 (s32), -1000..1000, negative = regen; shown as `torque_demand_percent`
0x0E   | 4      | The datasheet's filtered motor power (s32). Not an instantaneous power in W: it is about 1% of pack V x I, climbs steadily through a ride and restarts near 0 on entering RUN. Shown raw as `filtered_motor_power_raw`, no unit
0x12   | 4      | Discharge power limit, W (s32); -1 when the BMS is not active. Shown as `discharge_power_limit_watts` (null when -1) with `bms_power_limit_active`

Any other payload length, or a sub-second value above 1,000,000, falls back to the
raw-hex report. 0x54 entries outside FST/Gen3 MBB files keep the older
`sensor_data()` decoder.

**Example JSON output** (invented values):
```json
{
  "subsecond_us": 250000,
  "sequence": 7,
  "marker": 1,
  "torque_nm": 87.654321,
  "torque_demand_percent": 45.5,
  "filtered_motor_power_raw": 12345,
  "discharge_power_limit_watts": 61500,
  "bms_power_limit_active": true
}
```

### `0x52` / `0x53` (Types 82-83) - Vehicle State Telemetry

See `0x51` / `0x52` / `0x53` above.

### `0xfb` (Type 251) - System Information
*Added in 2025+ firmware - appears once per log session*

**Purpose**: System identification and configuration data. Decoded by
`Gen2.mbb_system_information()`.

Written by both MBB and BMS firmware under this same type id, with
genuinely different payloads - confirmed against real logs,
not assumed from the field names. A 4-byte identifier at payload
offset 6 (or 7, see shift below) reads `MBB\0` on the MBB side and
`BMS\0` on the BMS side; only the MBB shape is decoded today. BMS's
copy carries no vehicle VIN anywhere in the payload, and instead
reports a hardware descriptor (`"FST Mono"`), its own board serial and
firmware part number, at different offsets than the MBB layout below.

**MBB shape**, offsets relative to the entry's own payload (after the
standard 5-byte type+timestamp header), shifted by +1 together on a
small minority of files, the same firmware-variant ambiguity
the Gen3 header decode's own `0x29`/`0x2A` whole-file search handles
(not the same underlying cause, though):

Offset | Length | Contents
------ | :----: | --------
0x06   | 4      | Identifier, `"MBB\0"`
0x12   | 16     | Model (e.g. "SRF", null-terminated)
0x22   | 17     | VIN, checksum-validated (e.g., "538DZAZ81PCN25719")
0x35   | 16     | Serial number (e.g., "SJ0419ZER9901", null-terminated)
0x55   | 16     | Firmware version (e.g., "40-08198", null-terminated)
0x5E   | 1      | Board rev.
0x60   | 1      | Firmware rev.

A further hash-style build field (e.g. `"dfb7a27"`) follows Firmware
rev. but is not decoded: its position drifts relative to a preceding
checksum-like field whenever that field's raw bytes need byte-stuffing
removal, so it isn't a fixed offset the way the fields above are.

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

**For the FST Platform Format:**
- Un-stuff `0xFE` escapes before applying any offset. The header is always 13 bytes after
  un-stuffing; on raw bytes, every escape before a field shifts it by one
- Read byte `0x0c` as the UTC offset in hours rather than matching it against fixed values

**For 2024 Ring Buffer Format:**
- Parse verbose diagnostic messages
- Handle full "Riding" telemetry entries
- Process section headers for data location
- Expect higher entry count with descriptive text

**Cross-Format Compatibility:**
- The core entry structure (0xb2 header + length + type + timestamp) remains consistent
- Message interpretation and payload structure varies significantly
- Use format detection to select appropriate parsing logic
