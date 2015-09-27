*note: values in raw logs are little endian*

# MBB log file layout

Address    | Length | Contents                                      
---------- | :----: | --------
0x00000000 | 3      | `MBB`
0x00000004 | 2      | `0xac 0x4e`
0x00000026 | 4      | `0xa1 0xa1 0xa1 0xa1` *(header / seperator?)*
0x0000002a | 20     | *First run date?*
0x00000200 | 21     | Serial number
0x00000240 | 17     | VIN number
0x00000257 | 4      | `0xa0 0xa0 0xa0 0xa0` *(header / seperator?)*
0x0000025b | 20     | *Date / time - unknown, but close to time @ 0x0000002a*
0x0000027a | 2      | Firmware revision
0x0000027c | 2      | Board revision
0x00000400 | 4      | `0xa3 0xa3 0xa3 0xa3` *(header / seperator?)*
0x00000600 | 4      | `0xa2 0xa2 0xa2 0xa2` Log entries header
0x00000604 | 4      | Log entries end address
0x00000608 | 4      | Log entries start address
0x0000060c | 2      | Log entries count




# BMS log file layout

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
0x00   | 1         | `0xb2` *header byte*
0x01   | 1         | Entry length (includes header byte)
0x02   | 1         | Entry type - see following section
0x03   | 4         | Timestamp (epoch)
0x07   | *variabe* | Log message

## Log file entry types

### `0x09` - key state
Offset | Length | Contents                                      
------ | :----: | --------
0x00   | 1      | state

### `0x28` - battery CAN link up
Offset | Length | Contents                                      
------ | :----: | --------
0x00   | 1      | module number

### `0x2a` - Sevcon CAN link up
*(no additional data)*

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

### `0x39` - battery discharge current limited
Offset | Length | Contents                                      
------ | :----: | --------
0x00   | 2      | discharge current

### `0x3d` - battery module contactor closed
Offset | Length | Contents                                      
------ | :----: | --------
0x00   | 1      | module number

### `0x3e` - *cell voltages?*

### `0xfd` - debug string
Offset | Length     | Contents                                      
------ | :--------: | --------
0x00   | *variable* | message text (ascii)
