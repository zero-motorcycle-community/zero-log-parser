#!/usr/bin/python

'''
Little decoder utility to parse Zero Motorcycle main bike board (MBB) and
battery management system (BMS) logs. These may be extracted from the bike
using the Zero mobile app. Once paired over bluetooth, select 'Support' >
'Email bike logs' and send the logs to yourself rather than / in addition to
zero support.

Usage:

   $ python zero_log_parser.py <*.bin file> [-o output_file]

'''

import argparse
import os
import struct
import string
import codecs
import sys
from time import localtime, strftime, gmtime
from collections import OrderedDict
from math import trunc

TIME_FORMAT = '%m/%d/%Y %H:%M:%S'
CSV_TIME_FORMAT = '%d/%m/%Y\t%H:%M:%S'
USE_MBB_TIME = False

class BinaryTools:
    '''
    Utility class for dealing with serialised data from the Zero's
    '''

    TYPES = {
        'int8':     'b',
        'uint8':    'B',
        'int16':    'h',
        'uint16':   'H',
        'int32':    'i',
        'uint32':   'I',
        'int64':    'q',
        'uint64':   'Q',
        'float':    'f',
        'double':   'd',
        'char':     's',
        'bool':     '?'
    }

    @staticmethod
    def unpack(type_name, buff, address, count=1, offset=0):
        buff = buff+bytearray(32)
        type_char = BinaryTools.TYPES[type_name.lower()]
        type_format = '<{}{}'.format(count, type_char)
        return struct.unpack_from(type_format, buff, address + offset)[0]

    @staticmethod
    def unescape_block(data):
        start_offset = 0

        escape_offset = data.find(b'\xfe')

        while escape_offset != -1:
            escape_offset += start_offset
            data[escape_offset] = data[escape_offset] ^ data[escape_offset + 1] - 1
            data = data[0:escape_offset + 1] + data[escape_offset + 2:]
            start_offset = escape_offset + 1
            escape_offset = data[start_offset:].find(b'\xfe')

        return data


class LogFile:
    '''
    Wrapper for our raw log file
    '''

    def __init__(self, file_path):
        with open(file_path, 'rb') as f:
            self._data = bytearray(f.read())

    def index(self, sequence):
        return self._data.index(sequence)

    def unpack(self, type_name, address, count=1, offset=0):
        return BinaryTools.unpack(type_name, self._data, address + offset,
                                  count=count)

    def extract(self, start_address, length, offset=0):
        return self._data[start_address + offset:
                          start_address + length + offset]

    def raw(self):
        return bytearray(self._data)


def parse_entry(log_data, address, unhandled):
    '''
    Parse an individual entry from a LogFile into a human readable form
    '''
    header = log_data[address]
    # correct header offset as needed to prevent errors
    header_bad = header != 0xb2
    while header_bad:
        address += 1
        header = log_data[address]
        header_bad = header != 0xb2
    length = log_data[address + 1]

    unescaped_block = BinaryTools.unescape_block(log_data[address + 0x2:address + length])

    message_type = BinaryTools.unpack('uint8', unescaped_block, 0x00)
    timestamp = BinaryTools.unpack('uint32', unescaped_block, 0x01)
    message = unescaped_block[0x05:]
    
    def bms_discharge_level(x):
        bike = {
            0x01 : 'Bike On',
            0x02 : 'Charge',
            0x03 : 'Idle'
        }
        fields = {
            'AH' : trunc(BinaryTools.unpack('uint32', x, 0x06)/1000000.0),
            'B': BinaryTools.unpack('uint16', x, 0x02) - BinaryTools.unpack('uint16', x, 0x0),
            'I': trunc(BinaryTools.unpack('int32', x, 0x10)/1000000.0),
            'L': BinaryTools.unpack('uint16', x, 0x0),
            'H': BinaryTools.unpack('uint16', x, 0x02),
            'PT': BinaryTools.unpack('uint8', x, 0x04),
            'BT': BinaryTools.unpack('uint8', x, 0x05),
            'SOC': BinaryTools.unpack('uint8', x, 0x0a),            
            'PV': BinaryTools.unpack('uint32', x, 0x0b),            
            'l': BinaryTools.unpack('uint16', x, 0x14),            
            'M': bike.get(BinaryTools.unpack('uint8', x, 0x0f)),
            'X': BinaryTools.unpack('uint16', x, 0x16) # not included in log, contactor voltage?
        }
        return {
            'event': 'Discharge level',
            'conditions': ('{AH:03.0f} AH,'            
                           ' SOC:{SOC:3d}%,'
                           ' I:{I:3.0f}A,'
                           ' L:{L},'
                           ' l:{l},'
                           ' H:{H},'
                           ' B:{B:03d},'
                           ' PT:{PT:03d}C,'
                           ' BT:{BT:03d}C,'
                           ' PV:{PV:6d},'
                           ' M:{M}'                         
                           ).format(**fields)
        }        
       
    def bms_charge_full(x):
        fields = {
            'AH' : trunc(BinaryTools.unpack('uint32', x, 0x06)/1000000.0),
            'B': BinaryTools.unpack('uint16', x, 0x02) - BinaryTools.unpack('uint16', x, 0x0),            
            'L': BinaryTools.unpack('uint16', x, 0x00),
            'H': BinaryTools.unpack('uint16', x, 0x02),            
            'PT': BinaryTools.unpack('uint8', x, 0x04),
            'BT': BinaryTools.unpack('uint8', x, 0x05),            
            'SOC': BinaryTools.unpack('uint8', x, 0x0a),            
            'PV': BinaryTools.unpack('uint32', x, 0x0b)            
        }                    
        return {
            'event': 'Charged To Full',
            'conditions': ('{AH:03.0f} AH,'                          
                           ' SOC: {SOC}%,'
                           '         L:{L},'
                           '         H:{H},'
                           ' B:{B:03d},'                           
                           ' PT:{PT:03d}C,'
                           ' BT:{BT:03d}C,'
                           ' PV:{PV:6d}'
                           ).format(**fields)
        }        
       
    def bms_discharge_low(x):
        fields = {
            'AH' : trunc(BinaryTools.unpack('uint32', x, 0x06)/1000000.0),
            'B': BinaryTools.unpack('uint16', x, 0x02) - BinaryTools.unpack('uint16', x, 0x0),
            'L': BinaryTools.unpack('uint16', x, 0x00),
            'H': BinaryTools.unpack('uint16', x, 0x02),            
            'PT': BinaryTools.unpack('uint8', x, 0x04),
            'BT': BinaryTools.unpack('uint8', x, 0x05),            
            'SOC': BinaryTools.unpack('uint8', x, 0x0a),            
            'PV': BinaryTools.unpack('uint32', x, 0x0b)            
        }                    
        return {
            'event': 'Discharged To Low',
            'conditions': ('{AH:03.0f} AH,'
                           ' SOC:{SOC:3d}%,'
                           '         L:{L},'
                           '         H:{H},'
                           ' B:{B:03d},'
                           ' PT:{PT:03d}C,'
                           ' BT:{BT:03d}C,'
                           ' PV:{PV:6d}'
                           ).format(**fields)
        }        

    def bms_system_state(x):
        fields = {
            'state': 'On' if BinaryTools.unpack('bool', x, 0x0) else 'Off'
        }

        return {
            'event': 'System Turned {state}'.format(**fields),
            'conditions': ''
        }
        
    def bms_soc_adj_voltage(x):
        fields = {
            'old': BinaryTools.unpack('uint32', x, 0x00),
            'old_soc': BinaryTools.unpack('uint8', x, 0x04),            
            'new': BinaryTools.unpack('uint32', x, 0x05),
            'new_soc': BinaryTools.unpack('uint8', x, 0x09),            
            'low': BinaryTools.unpack('uint16', x, 0x0a)            
        }                
        return {
            'event': 'SOC adjusted for voltage',
            'conditions': ('old:   {old}uAH (soc:{old_soc}%), '
                           'new:   {new}uAH (soc:{new_soc}%), '
                           'low cell: {low} mV'
                           ).format(**fields)
        }        
    
    def bms_curr_sens_zero(x):
        fields = {
            'old': BinaryTools.unpack('uint16', x, 0x00),
            'new': BinaryTools.unpack('uint16', x, 0x02),
            'corrfact': BinaryTools.unpack('uint8', x, 0x04)
        }        
        return {
            'event': 'Current Sensor Zeroed',
            'conditions': ('old: {old}mV, '
                           'new: {new}mV, '
                           'corrfact: {corrfact}'
                           ).format(**fields)
            } 

    def bms_state(x):
        fields = {
            'state': 'Entering Hibernate' if BinaryTools.unpack('bool', x, 0x0) else 'Exiting Hibernate'
        }

        return {
            'event': '{state}'.format(**fields),
            'conditions': ''
        }
            
    def bms_isolation_fault(x):
        fields = {
            'ohms': BinaryTools.unpack('uint32', x, 0x00),
            'cell': BinaryTools.unpack('uint8', x, 0x04)
        }                
        return {
            'event': 'Chassis Isolation Fault',
            'conditions': ('{ohms} ohms to cell {cell}'
                           ).format(**fields)
        }
        
    def bms_reflash(x):
        fields = {
            'rev': BinaryTools.unpack('uint8', x, 0x00),
            'build': BinaryTools.unpack('char', x, 0x01, 20).decode('utf-8', 'ignore')
        }                

        return {
                'event': 'BMS Reflash',
                'conditions': ('Revision {rev}, ' 'Built {build}').format(**fields)
            }
               
    def bms_change_can_id(x):
        fields = {
            'old': BinaryTools.unpack('uint8', x, 0x00),
            'new': BinaryTools.unpack('uint8', x, 0x01)
        }                
        return {
            'event': 'Changed CAN Node ID',
            'conditions': ('old: {old:02d}, new: {new:02d}'
                           ).format(**fields)
        }

    def bms_contactor_state(x):
        if (BinaryTools.unpack('uint32', x, 0x01)):
            prechg = trunc((BinaryTools.unpack('uint32', x, 0x05) * 1.0 )/ (BinaryTools.unpack('uint32', x, 0x01) * 1.0) * 100)
        else:
            prechg = 0x0
        fields = {
            'state': 'Contactor was Closed' if BinaryTools.unpack('bool', x, 0x0) else 'Contactor was Opened',
            'pv': BinaryTools.unpack('uint32', x, 0x01),
            'sv': BinaryTools.unpack('uint32', x, 0x05),
            'pc': prechg,
            'dc': BinaryTools.unpack('int32', x, 0x09)            
        }                        
        return {
            'event': '{state}'.format(**fields),
            'conditions': ('Pack V: {pv}mV, Switched V: {sv}mV, Prechg Pct: {pc:2.0f}%, Dischg Cur: {dc}mA').format(**fields)
        }
        
    def bms_discharge_cut(x):
        fields = {
            'cut' : BinaryTools.unpack('uint8', x, 0x00)/255.0*100
        }
        return {
            'event': 'Discharge cutback',
            'conditions': ('{cut:2.0f}%').format(**fields)
        }
 
    def bms_contactor_drive(x):  
        fields = {
            'pv': BinaryTools.unpack('uint32', x, 0x01),
            'sv': BinaryTools.unpack('uint32', x, 0x05),
            'dc': BinaryTools.unpack('uint8', x, 0x09)            
        }                       
        return {
            'event': 'Contactor drive turned on',
            'conditions': ('Pack V: {pv}mV, Switched V: {sv}mV, Duty Cycle: {dc}%').format(**fields)
        }

    def debug_message(x):
        return {
            'event': BinaryTools.unpack('char', x, 0x0, count=len(x) - 1).decode('utf-8', 'ignore'),
            'conditions': ''
        }

    def board_status(x):
        causes = {
            0x04: 'Software',
        }

        fields = {
            'cause': causes.get(BinaryTools.unpack('uint8', x, 0x00),
                                'Unknown')
        }

        return {
            'event': 'BMS Reset',
            'conditions': '{cause}'.format(**fields)
        }

    def key_state(x):
        fields = {
            'state': 'On ' if BinaryTools.unpack('bool', x, 0x0) else 'Off'
        }

        return {
            'event': 'Key {state}'.format(**fields),
            'conditions': ''
        }

    def battery_can_link_up(x):
        fields = {
            'module': BinaryTools.unpack('uint8', x, 0x0)
        }

        return {
            'event': 'Module {module:02} CAN Link Up'.format(**fields),
            'conditions': ''
        }

    def battery_can_link_down(x):
        fields = {
            'module': BinaryTools.unpack('uint8', x, 0x0)
        }

        return {
            'event': 'Module {module:02} CAN Link Down'.format(**fields),
            'conditions': ''
        }

    def sevcon_can_link_up(x):
        return {
            'event': 'Sevcon CAN Link Up',
            'conditions': ''
        }

    def sevcon_can_link_down(x):
        return {
            'event': 'Sevcon CAN Link Down',
            'conditions': ''
        }

    def run_status(x):
        global csv_entry
        mod_translate = {
            0x00: '00',
            0x01: '10',
            0x02: '01',
            0x03: '11',
        }

        fields = {
            'pack_temp_hi': BinaryTools.unpack('uint8', x, 0x0),
            'pack_temp_low': BinaryTools.unpack('uint8', x, 0x1),
            'soc': BinaryTools.unpack('uint16', x, 0x2),
            'pack_voltage': BinaryTools.unpack('uint32', x, 0x4) / 1000.0,
            'motor_temp': BinaryTools.unpack('int16', x, 0x8),
            'controller_temp': BinaryTools.unpack('int16', x, 0xa),
            'rpm': BinaryTools.unpack('uint16', x, 0xc),
            'battery_current': BinaryTools.unpack('int16', x, 0x10),
            'mods': mod_translate.get(BinaryTools.unpack('uint8', x, 0x12),
                                      'Unknown'),
            'motor_current': BinaryTools.unpack('int16', x, 0x13),
            'ambient_temp': BinaryTools.unpack('int16', x, 0x15),
            'odometer': BinaryTools.unpack('uint32', x, 0x17),
        }

        if (log_type == 'MBB') and ('c' in globals()):
#            c.write(strftime(CSV_TIME_FORMAT, localtime(timestamp)))
            csv_entry += 1
            c.write('{:5d}'.format(csv_entry))
            c.write((';Riding  ;{battery_current:4d};{soc:3d};{pack_temp_hi:4d};{pack_temp_low:4d};{ambient_temp:4d};{pack_voltage:7.3f};{rpm:5d};{odometer:5d}\n').format(**fields))

        return {
            'event': 'Riding',
            'conditions': ('PackTemp: h {pack_temp_hi}C, l {pack_temp_low}C, '
                           'PackSOC:{soc:3d}%, '
                           'Vpack:{pack_voltage:7.3f}V, '
                           'MotAmps:{motor_current:4d}, '
                           'BattAmps:{battery_current:4d}, '
                           'Mods: {mods}, '
                           'MotTemp:{motor_temp:4d}C, '
                           'CtrlTemp:{controller_temp:4d}C, '
                           'AmbTemp:{ambient_temp:4d}C, '
                           'MotRPM:{rpm:4d}, '
                           'Odo:{odometer:5d}km'
                           ).format(**fields)
        }

    def charging_status(x):
        global csv_entry
        fields = {
            'pack_temp_hi': BinaryTools.unpack('uint8', x, 0x00),
            'pack_temp_low': BinaryTools.unpack('uint8', x, 0x01),
            'soc': BinaryTools.unpack('uint16', x, 0x02),
            'pack_voltage': BinaryTools.unpack('uint32', x, 0x4) / 1000.0,
            'battery_current': BinaryTools.unpack('int8', x, 0x08),
            'mods': BinaryTools.unpack('uint8', x, 0x0c),
            'ambient_temp': BinaryTools.unpack('int8', x, 0x0d),
        }
        if (log_type == 'MBB') and ('c' in globals()):
#            c.write(strftime(CSV_TIME_FORMAT, localtime(timestamp)))
            csv_entry += 1
            c.write('{:5d}'.format(csv_entry))
            c.write((';Charging;{battery_current:4d};{soc:3d};{pack_temp_hi:4d};{pack_temp_low:4d};{ambient_temp:4d};{pack_voltage:7.3f};;\n').format(**fields))

        return {
            'event': 'Charging',
            'conditions': ('PackTemp: h {pack_temp_hi}C, l {pack_temp_low}C, '
                           'AmbTemp: {ambient_temp}C, '
                           'PackSOC:{soc:3d}%, '
                           'Vpack:{pack_voltage:7.3f}V, '
                           'BattAmps: {battery_current:3d}, '
                           'Mods: {mods:02b}, '
                           'MbbChgEn: Yes, '
                           'BmsChgEn: No'
                           ).format(**fields)
        }

    def sevcon_status(x):
        cause = {
            0x4681: 'Preop',
            0x4884: 'Sequence Fault',
            0x4981: 'Throttle Fault',
        }

        fields = {
            'code': BinaryTools.unpack('uint16', x, 0x00),
            'reg': BinaryTools.unpack('uint8', x, 0x04),
            'sevcon_code': BinaryTools.unpack('uint16', x, 0x02),
            'data': ' '.join(['{:02X}'.format(c) for c in x[5:]]),
            'cause': cause.get(BinaryTools.unpack('uint16', x, 0x02),
                               'Unknown')
        }

        return {
            'event': 'SEVCON CAN EMCY Frame',
            'conditions': ('Error Code: 0x{code:04X}, '
                           'Error Reg: 0x{reg:02X}, '
                           'Sevcon Error Code: 0x{sevcon_code:04X}, '
                           'Data: {data}, '
                           '{cause}'
                           ).format(**fields)
        }

    def charger_status(x):
        states = {
            0x00: 'Disconnected',
            0x01: 'Connected',
        }

        name = {
            0x00: 'Calex 720W',
            0x01: 'Calex 1200W',
            0x02: 'External Chg 0',
            0x03: 'External Chg 1',
        }

        fields = {
            'module': BinaryTools.unpack('uint8', x, 0x0),
            'state': states.get(BinaryTools.unpack('uint8', x, 0x1)),
            'name': name.get(BinaryTools.unpack('uint8', x, 0x0),
                             'Unknown')
        }

        return {
            'event': '{name} Charger {module} {state:13s}'.format(**fields),
            'conditions': ''
        }

    def battery_status(x):
        events = {
            0x00: 'Opening Contactor',
            0x01: 'Closing Contactor',
            0x02: 'Registered',
        }

        event = BinaryTools.unpack('uint8', x, 0x0)

        fields = {
            'event': events.get(event, 'Unknown (0x{:02x})'.format(event)),
            'module': BinaryTools.unpack('uint8', x, 0x1),
            'modvolt': BinaryTools.unpack('uint32', x, 0x2) / 1000.0,
            'sysmax': BinaryTools.unpack('uint32', x, 0x6) / 1000.0,
            'sysmin': BinaryTools.unpack('uint32', x, 0xa) / 1000.0,
            'vcap': BinaryTools.unpack('uint32', x, 0x0e) / 1000.0,
            'batcurr': BinaryTools.unpack('int16', x, 0x12),
            'serial': BinaryTools.unpack('char', x, 0x14, count=len(x[0x14:])),
        }
        fields['diff'] = fields['sysmax'] - fields['sysmin']
        try:
            fields['prechg'] = int(fields['vcap'] * 100 / fields['modvolt'])
        except:
            fields['prechg'] = 0

        # Ensure the serial is printable
        fields['serial'] = filter(lambda x: x in string.printable,
                                  fields['serial'])

        return {
            'event': 'Module {module:02} {event}'.format(**fields),
            'conditions': {
                0x00: 'vmod: {modvolt:7.3f}V, batt curr: {batcurr:3.0f}A',
                0x01: ('vmod: {modvolt:7.3f}V, '
                       'maxsys: {sysmax:7.3f}V, '
                       'minsys: {sysmin:7.3f}V, '
                       'diff: {diff:0.03f}V, '
                       'vcap: {vcap:6.3f}V, '
                       'prechg: {prechg}%'
                       ),
                0x02: 'serial: {serial},  vmod: {modvolt:3.3f}V'
            }.get(event, '').format(**fields)
        }

    def power_state(x):
        sources = {
            0x01: 'Key Switch',
            0x03: 'Ext Charger 1',
            0x04: 'Onboard Charger',
        }

        fields = {
            'state': 'On' if BinaryTools.unpack('bool', x, 0x0) else 'Off',
            'source': sources.get(BinaryTools.unpack('uint8', x, 0x1),
                                  'Unknown')
        }

        return {
            'event': 'Power {state}'.format(**fields),
            'conditions': '{source}'.format(**fields)
        }

    def sevcon_power_state(x):
        fields = {
            'state': 'On' if BinaryTools.unpack('bool', x, 0x0) else 'Off'
        }

        return {
            'event': 'Sevcon Turned {state}'.format(**fields),
            'conditions': ''
        }

    def show_bluetooth_state(x):
        return {
            'event': 'BT RX buffer reset',
            'conditions': ''
        }

    def battery_discharge_current_limited(x):
        fields = {
            'limit': BinaryTools.unpack('uint16', x, 0x00),
            'min_cell': BinaryTools.unpack('uint16', x, 0x02),
            'temp': BinaryTools.unpack('uint8', x, 0x04),
            'max_amp': BinaryTools.unpack('uint16', x, 0x05),
        }
        fields['percent'] = fields['limit'] * 100 / fields['max_amp']

        return {
            'event': 'Batt Dischg Cur Limited',
            'conditions': ('{limit} A ({percent}%), '
                           'MinCell: {min_cell}mV, '
                           'MaxPackTemp: {temp}C'
                           ).format(**fields)
        }

    def low_chassis_isolation(x):
        fields = {
            'kohms': BinaryTools.unpack('uint32', x, 0x00),
            'cell': BinaryTools.unpack('uint8', x, 0x04),
        }

        return {
            'event': 'Low Chassis Isolation',
            'conditions': '{kohms} KOhms to cell {cell}'.format(**fields)
        }

    def precharge_decay_too_steep(x):
        return {
            'event': 'Precharge Decay Too Steep. Restarting Sevcon.',
            'conditions': ''
        }

    def disarmed_status(x):
        fields = {
            'pack_temp_hi': BinaryTools.unpack('uint8', x, 0x0),
            'pack_temp_low': BinaryTools.unpack('uint8', x, 0x1),
            'soc': BinaryTools.unpack('uint16', x, 0x2),
            'pack_voltage': BinaryTools.unpack('uint32', x, 0x4) / 1000.0,
            'motor_temp': BinaryTools.unpack('int16', x, 0x8),
            'controller_temp': BinaryTools.unpack('int16', x, 0xa),
            'rpm': BinaryTools.unpack('uint16', x, 0xc),
            'battery_current': BinaryTools.unpack('uint8', x, 0x10),
            'mods': BinaryTools.unpack('uint8', x, 0x12),
            'motor_current': BinaryTools.unpack('int8', x, 0x13),
            'ambient_temp': BinaryTools.unpack('int16', x, 0x15),
            'odometer': BinaryTools.unpack('uint32', x, 0x17),
        }

        return {
            'event': 'Disarmed',
            'conditions': ('PackTemp: h {pack_temp_hi}C, l {pack_temp_low}C, '
                           'PackSOC:{soc:3d}%, '
                           'Vpack:{pack_voltage:03.3f}V, '
                           'MotAmps:{motor_current:4d}, '
                           'BattAmps:{battery_current:4d}, '
                           'Mods: {mods:02b}, '
                           'MotTemp:{motor_temp:4d}C, '
                           'CtrlTemp:{controller_temp:4d}C, '
                           'AmbTemp:{ambient_temp:4d}C, '
                           'MotRPM:{rpm:4d}, '
                           'Odo:{odometer:5d}km'
                           ).format(**fields)
        }

    def battery_contactor_closed(x):
        fields = {
            'module': BinaryTools.unpack('uint8', x, 0x0)
        }

        return {
            'event': 'Battery module {module:02} contactor closed'.format(**fields),
            'conditions': ''
        }

    def unhandled_entry_format(x):
        fields = {
            'message_type': '0x{:02x}'.format(message_type),
            'message': ' '.join(['0x{:02x}'.format(c) for c in x])
        }

        return {
            'event': '{message_type} {message}'.format(**fields),
            'conditions': chr(message_type) + '???'
        }
     
    parsers = {
        # Unknown entry types to be added when defined: type, length, source, example
        0x01: board_status,
        #0x02: unknown, 2, 6350_MBB_2016-04-12, 0x02 0x2e 0x11 ???
        0x03: bms_discharge_level,
        0x04: bms_charge_full,
        #0x05: unknown, 17, 6890_BMS0_2016-07-03, 0x05 0x34 0x0b 0xe0 0x0c 0x35 0x2a 0x89 0x71 0xb5 0x01 0x00 0xa5 0x62 0x01 0x00 0x20 0x90 ???
        0x06: bms_discharge_low,
        0x08: bms_system_state,
        0x09: key_state,
        0x0b: bms_soc_adj_voltage,
        0x0d: bms_curr_sens_zero,
        #0x0e: unknown, 3, 6350_BMS0_2017-01-30 0x0e 0x05 0x00 0xff ???
        0x10: bms_state,
        0x11: bms_isolation_fault,
        0x12: bms_reflash,
        0x13: bms_change_can_id,
        0x15: bms_contactor_state,
        0x16: bms_discharge_cut,
        0x18: bms_contactor_drive,
        #0x1c: unknown, 8, 3455_MBB_2016-09-11, 0x1c 0xdf 0x56 0x01 0x00 0x00 0x00 0x30 0x02 ???
        #0x1e: unknown, 4, 6472_MBB_2016-12-12, 0x1e 0x32 0x00 0x06 0x23 ???
        #0x1f: unknown, 4, 5078_MBB_2017-01-20, 0x1f 0x00 0x00 0x08 0x43 ???
        #0x20: unknown, 3, 6472_MBB_2016-12-12, 0x20 0x02 0x32 0x00 ???
        #0x26: unknown, 6, 3455_MBB_2016-09-11, 0x26 0x72 0x00 0x40 0x00 0x80 0x00 ???
        0x28: battery_can_link_up,
        0x29: battery_can_link_down,
        0x2a: sevcon_can_link_up,
        0x2b: sevcon_can_link_down,
        0x2c: run_status,
        0x2d: charging_status,
        0x2f: sevcon_status,
        0x30: charger_status,
        #0x31: unknown, 1, 6350_MBB_2016-04-12, 0x31 0x00 ???
        0x33: battery_status,
        0x34: power_state,
        #0x35: unknown, 5, 6472_MBB_2016-12-12, 0x35 0x00 0x46 0x01 0xcb 0xff ???
        0x36: sevcon_power_state,
        #0x37: unknown, 0, 3558_MBB_2016-12-25, 0x37  ???
        0x38: show_bluetooth_state,
        0x39: battery_discharge_current_limited,
        0x3a: low_chassis_isolation,
        0x3b: precharge_decay_too_steep,
        0x3c: disarmed_status,
        0x3d: battery_contactor_closed,
        0xfd: debug_message
    }
    entry_parser = parsers.get(message_type, unhandled_entry_format)
    
    try:
        entry = entry_parser(message)
    except:
        entry = unhandled_entry_format(message)
        entry['event'] = 'Exception caught: ' + entry['event']
        unhandled += 1

    if timestamp > 0xfff:
        if USE_MBB_TIME:
            # The output from the MBB (via serial port) lists time as GMT-7
            entry['time'] = strftime(TIME_FORMAT,
                                     gmtime(timestamp - 7 * 60 * 60))
        else:
            entry['time'] = strftime(TIME_FORMAT, localtime(timestamp))
    else:
        entry['time'] = str(timestamp)

    return (length, entry, unhandled)


def parse_log(bin_file, output_file):
    '''
    Parse a Zero binary log file into a human readable text file
    '''
    print('Parsing {}...'.format(bin_file))
    global log_type

    log = LogFile(bin_file)
    log_type = log.unpack('char', 0x0, count=3).decode('utf-8', 'ignore')
    if log_type not in ['MBB', 'BMS']:
        log_type = 'Unknown Type'
    sys_info = OrderedDict()
    if log_type == 'MBB':
        # ignore decode errors, static addresses may be incorrect 
        sys_info['Serial number'] = log.unpack('char', 0x200, count=21).decode('utf-8', 'ignore')
        sys_info['VIN'] = log.unpack('char', 0x240, count=17).decode('utf-8', 'ignore')
        sys_info['Firmware rev.'] = log.unpack('uint16', 0x27b)
        sys_info['Board rev.'] = log.unpack('uint16', 0x27d)
        sys_info['Model'] = log.unpack('char', 0x27f, count=3).partition(b'\0')[0].decode('utf-8', 'ignore')
        if 'c' in globals():
            c.write('Entry;Rid/Char; AMP;SOC;pthi;ptlo;ambi;PacVolt; RPM ; ODO\n')
    if log_type == 'BMS':
        sys_info['Initial date'] = log.unpack('char', 0x12, count=20).decode('utf-8', 'ignore')    
        sys_info['BMS serial number'] = log.unpack('char', 0x300, count=21).decode('utf-8', 'ignore')
        sys_info['Pack serial number'] = log.unpack('char', 0x320, count=8).decode('utf-8', 'ignore')        
    if log_type == 'Unknown Type':
        sys_info['System info'] = 'unknown'
        
    # handle missing header index
    try:
        entries_header_idx = log.index(b'\xa2\xa2\xa2\xa2')
        entries_end = log.unpack('uint32', 0x4, offset=entries_header_idx)
        entries_start = log.unpack('uint32', 0x8, offset=entries_header_idx)
        claimed_entries_count = log.unpack('uint32', 0xc, offset=entries_header_idx)
        entries_data_begin = entries_header_idx + 0x10
    except:
        entries_end = len(log.raw())
        entries_start = log.index(b'\xb2')
        claimed_entries_count = 0

    # Handle data wrapping across the upper bound of the ring buffer
    if entries_start >= entries_end:
        event_log = log.raw()[entries_start:] + \
            log.raw()[entries_data_begin:entries_end]
    else:
        event_log = log.raw()[entries_start:entries_end]
	
    # count entry headers
    entries_count = event_log.count(b'\xb2')

    print('{} entries found ({} claimed)'.format(entries_count, claimed_entries_count))

    with codecs.open(output_file, 'w', 'utf-8-sig') as f:
        f.write('Zero ' + log_type + ' log\n')
        f.write('\n')

        for k, v in sys_info.items():
            f.write('{0:18} {1}\n'.format(k, v))
        f.write('\n')

        f.write('Printing {0} of {0} log entries..\n'.format(entries_count))
        f.write('\n')
        f.write(' Entry    Time of Log            Event                      Conditions\n')
        f.write('+--------+----------------------+--------------------------+----------------------------------\n')

        read_pos = 0
        unhandled = 0
        global csv_entry
        csv_entry = 0
        unknown_entries = 0
        unknown = []
        for entry_num in range(entries_count):
            (length, entry, unhandled) = parse_entry(event_log, read_pos, unhandled)

            entry['line'] = entry_num + 1

            if entry['conditions']:
                if '???' in entry['conditions']:
                    u = entry['conditions'][0]
                    unknown_entries += 1
                    if u not in unknown:
                        unknown.append(u)
                    entry['conditions'] = '???'
                    f.write(' {line:05d}     {time:>19s}   {event} {conditions}\n'.format(**entry))
                else:
                    f.write(' {line:05d}     {time:>19s}   {event:25}  {conditions}\n'.format(**entry))
            else:
                f.write(' {line:05d}     {time:>19s}   {event}\n'.format(**entry))

            read_pos += length

        f.write('\n')

    if unhandled > 0:
        print('{} unknown entries were not decoded'.format(unhandled))
    if unknown:
        print('{} unknown entries of types {}'.format(unknown_entries,', '.join(hex(ord(x)) for x in unknown),'02x'))

    print('Saved to {}'.format(output_file))

    if (log_type == 'MBB') and ('c' in globals()):
        print('Saved CSV to {}'.format(csv_file))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('bin_file', help='Zero *.bin log to decode')
    parser.add_argument('-o', '--output', help='decoded log filename')
    parser.add_argument('-c', '--csv', help='CSV Filename')
    args = parser.parse_args()

    log_file = args.bin_file
    if args.output:
        output_file = args.output
    else:
        output_file = os.path.splitext(args.bin_file)[0] + '.txt'

    out_files = os.path.splitext(args.bin_file)[0]
#    print(md5.new(out_files).hexdigest())

    if args.csv:
        csv_file = args.csv
        with codecs.open(csv_file, 'w') as c:
            parse_log(log_file, output_file)
    else:
        parse_log(log_file, output_file)


