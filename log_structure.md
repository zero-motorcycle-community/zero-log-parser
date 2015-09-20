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
0x00000400 | 4      | `0xa3 0xa3 0xa3 0xa3` *(header / seperator?)*
0x00000600 | 4      | `0xa2 0xa2 0xa2 0xa2` *(header / seperator?)*
0x00000604 |        | Main log begins



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
0x01   | 1         | Entry length (number of bytes - includes header byte)
0x02   | 1         | Entry type - see table below
0x03   | 4         | Timestamp - Epoch, little endian *(local time)*
0x07   | *variabe* | Log message

## Log file entry types

Value | Description             | Format
----- | ----------------------- | ------
0x09  | *key on*?               | key_state
0x28  | *can link*?             |
0x33  | *battery module*?       | 
0x34  | *power on*?             | power_state *source?*
0x36  | Sevcon power state      | state
0x39  | *unknown*               |
0x3d  | Sevcon contactor state  | 
0x3e  | *cell voltages*?        | 
0xfd  | Debug string            | 


