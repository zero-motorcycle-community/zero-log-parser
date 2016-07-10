# MBB log file layout

*note: values in raw logs are little endian*

## Static addresses

Address    | Length | Contents
---------- | :----: | --------
0x00000200 | 21     | Serial number
0x00000240 | 17     | VIN number
0x0000027b | 1      | Firmware revision
0x0000027d | 1      | Board revision
0x0000027f | 3      | Bike model (`SS`, `SR`, `DS`, *FX?*)


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


# BMS log file layout (*WIP*)

Address    | Length | Contents
---------- | :----: | --------
0x00000000 | 3      | `BMS`
0x0000000e | 4      | `0xa1 0xa1 0xa1 0xa1` *(header / seperator?)*
0x00000012 | 20     | *First run date?*
0x00000300 | 21     | Serial number
0x0000036a | 4      | `0xa0 0xa0 0xa0 0xa0` *(header / seperator?)*
0x0000036e | 20     | *Date / time - unknown, but close to time @ 0x00000012*
0x00000500 | 4      | `0xa3 0xa3 0xa3 0xa3` *(header / seperator?)*
0x00000700 | 4      | `0xa2 0xa2 0xa2 0xa2` *(header / seperator?)*
0x00000704 |        | Main log begins



# Log entry format (shared by MBB and BMS)

Offset | Length    | Contents
------ | :-------: | --------
0x00   | 1         | `0xb2` Entry header
0x01   | 1         | Entry length (including header byte)
0x02   | 1         | Entry type - see following section
0x03   | 4         | Timestamp (epoch)
0x07   | *variabe* | Entry data

Note that the entry appears to be encoded in some format starting from the entry type onwards (ie Entry type, timestamp, Entry data). Any bytes of 0xFE are xor'ed with the next byte -1.

For example, the byte sequenze 0xFE, 0x01 becomes 0xFE. The byte sequence OxFE, 0x4d becomes 0xb2. The length of the message is reduced accordingly.

## Log file entry types

### `0x0` - board status
Offset | Length | Contents
------ | :----: | --------
0x00   | 1      | cause of reset

This is still WIP as there aren't many messages available

### `0x09` - key state
Offset | Length | Contents
------ | :----: | --------
0x00   | 1      | state

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

### `0x36` - Sevcon power state
Offset | Length | Contents
------ | :----: | --------
0x00   | 1      | state

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

### `0x3e` - *cell voltages?*

### `0xfd` - debug string
Offset | Length     | Contents
------ | :--------: | --------
0x00   | *variable* | message text (ascii)
