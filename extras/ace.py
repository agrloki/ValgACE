# File: ace.py — ValgAce module for Klipper

import os
import serial
import serial.tools.list_ports
import json
import struct
import queue
import traceback
from typing import Optional, Dict, Any, Callable
from serial import SerialException
from contextlib import contextmanager


class ValgAce:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.toolhead = None
        self.reactor = self.printer.get_reactor()
        self.gcode = self.printer.lookup_object('gcode')
        self._name = 'ace'
        self.variables = self.printer.lookup_object('save_variables').allVariables
        self.read_buffer = bytearray()
        self.send_time = 0
        self._last_status_request = 0

        # Параметры таймаутов
        self._response_timeout = config.getfloat('response_timeout', 2.0)
        self._read_timeout = config.getfloat('read_timeout', 0.1)
        self._write_timeout = config.getfloat('write_timeout', 0.5)
        self._max_queue_size = config.getint('max_queue_size', 20)

        # Автопоиск устройства
        default_serial = self._find_ace_device()
        self.serial_name = config.get('serial', default_serial or '/dev/ttyACM0')
        self.baud = config.getint('baud', 115200)

        # Параметры конфигурации
        self.feed_speed = config.getint('feed_speed', 50)
        self.retract_speed = config.getint('retract_speed', 50)
        self.retract_mode = config.getint('retract_mode', 0)
        self.toolchange_retract_length = config.getint('toolchange_retract_length', 100)
        self.park_hit_count = config.getint('park_hit_count', 5)
        self.max_dryer_temperature = config.getint('max_dryer_temperature', 55)
        self.disable_assist_after_toolchange = config.getboolean('disable_assist_after_toolchange', True)
        self.infinity_spool_mode = config.getboolean ('infinity_spool_mode', False)

        # Состояние устройства
        self._info = self._get_default_info()
        self._callback_map = {}
        self._request_id = 0
        self._connected = False
        self._connection_attempts = 0
        self._max_connection_attempts = 5

        # Работа
        self._feed_assist_index = -1
        self._last_assist_count = 0
        self._assist_hit_count = 0
        self._park_in_progress = False
        self._park_is_toolchange = False
        self._park_previous_tool = -1
        self._park_index = -1

        # Очереди
        self._queue = queue.Queue(maxsize=self._max_queue_size)
        self._main_queue = queue.Queue()

        # Порты и реактор
        self._serial = None
        self._reader_timer = None
        self._writer_timer = None

        # Регистрация событий
        self._register_handlers()
        self._register_gcode_commands()

        # Подключение при запуске
        self.reactor.register_timer(self._connect_check, self.reactor.NOW)

    def _get_default_info(self) -> Dict[str, Any]:
        return {
            'status': 'disconnected',
            'dryer': {
                'status': 'stop',
                'target_temp': 0,
                'duration': 0,
                'remain_time': 0
            },
            'temp': 0,
            'enable_rfid': 1,
            'fan_speed': 7000,
            'feed_assist_count': 0,
            'cont_assist_time': 0.0,
            'slots': [{
                'index': i,
                'status': 'empty',
                'sku': '',
                'type': '',
                'color': [0, 0, 0]
            } for i in range(4)]
        }

    def _register_handlers(self):
        self.printer.register_event_handler('klippy:ready', self._handle_ready)
        self.printer.register_event_handler('klippy:disconnect', self._handle_disconnect)

    def _register_gcode_commands(self):
        commands = [
            ('ACE_DEBUG', self.cmd_ACE_DEBUG, "Debug connection"),
            ('ACE_STATUS', self.cmd_ACE_STATUS, "Get device status"),
            ('ACE_START_DRYING', self.cmd_ACE_START_DRYING, "Start drying"),
            ('ACE_STOP_DRYING', self.cmd_ACE_STOP_DRYING, "Stop drying"),
            ('ACE_ENABLE_FEED_ASSIST', self.cmd_ACE_ENABLE_FEED_ASSIST, "Enable feed assist"),
            ('ACE_DISABLE_FEED_ASSIST', self.cmd_ACE_DISABLE_FEED_ASSIST, "Disable feed assist"),
            ('ACE_PARK_TO_TOOLHEAD', self.cmd_ACE_PARK_TO_TOOLHEAD, "Park filament to toolhead"),
            ('ACE_FEED', self.cmd_ACE_FEED, "Feed filament"),
            ('ACE_UPDATE_FEEDING_SPEED', self.cmd_ACE_UPDATE_FEEDING_SPEED, "Update feeding speed"),
            ('ACE_STOP_FEED', self.cmd_ACE_STOP_FEED, "Stop feed filament"),
            ('ACE_RETRACT', self.cmd_ACE_RETRACT, "Retract filament"),
            ('ACE_UPDATE_RETRACT_SPEED', self.cmd_ACE_UPDATE_RETRACT_SPEED, "Update retracting speed"),
            ('ACE_STOP_RETRACT', self.cmd_ACE_STOP_RETRACT, "Stop retract filament"),
            ('ACE_CHANGE_TOOL', self.cmd_ACE_CHANGE_TOOL, "Change tool"),
            ('ACE_INFINITY_SPOOL', self.cmd_ACE_INFINITY_SPOOL, "Change tool whel current spool is empty"),
            ('ACE_FILAMENT_INFO', self.cmd_ACE_FILAMENT_INFO, "Show filament info"),
        ]
        for name, func, desc in commands:
            self.gcode.register_command(name, func, desc=desc)

    def _find_ace_device(self) -> Optional[str]:
        ACE_IDS = {
            'VID:PID': [(0x28e9, 0x018a)],
            'DESCRIPTION': ['ACE', 'BunnyAce', 'DuckAce']
        }
        for port in serial.tools.list_ports.comports():
            if hasattr(port, 'vid') and hasattr(port, 'pid'):
                if (port.vid, port.pid) in ACE_IDS['VID:PID']:
                    print(f"Found ACE device by VID/PID at {port.device}")
                    return port.device
            if any(name in (port.description or '') for name in ACE_IDS['DESCRIPTION']):
                print(f"Found ACE device by description at {port.device}")
                return port.device
        print("No ACE device found by auto-detection")
        return None

    def _connect_check(self, eventtime):
        if not self._connected:
            self._connect()
        return eventtime + 1.0

    def _connect(self) -> bool:
        if self._connected:
            return True
        for attempt in range(self._max_connection_attempts):
            try:
                self._serial = serial.Serial(
                    port=self.serial_name,
                    baudrate=self.baud,
                    timeout=0,
                    write_timeout=self._write_timeout
                )
                if self._serial.is_open:
                    self._connected = True
                    self._info['status'] = 'ready'
                    print(f"Connected to ACE at {self.serial_name}")

                    def info_callback(response):
                        res = response['result']
                        print(f"Device info: {res.get('model', 'Unknown')} {res.get('firmware', 'Unknown')}")
                        self.gcode.respond_info(f"Connected {res.get('model', 'Unknown')} {res.get('firmware', 'Unknown')}")

                    self.send_request({"method": "get_info"}, info_callback)

                    if self._reader_timer is None:
                        self._reader_timer = self.reactor.register_timer(self._reader_loop, self.reactor.NOW)
                    if self._writer_timer is None:
                        self._writer_timer = self.reactor.register_timer(self._writer_loop, self.reactor.NOW)
                    return True
            except SerialException as e:
                print(f"Connection attempt {attempt + 1} failed: {str(e)}")
                self.dwell(1.0, lambda: None)
        print("Failed to connect to ACE device")
        return False

    def _disconnect(self):
        if not self._connected:
            return
        self._connected = False
        if self._reader_timer:
            self.reactor.unregister_timer(self._reader_timer)
            self._reader_timer = None
        if self._writer_timer:
            self.reactor.unregister_timer(self._writer_timer)
            self._writer_timer = None
        try:
            if self._serial and self._serial.is_open:
                self._serial.close()
        except Exception as e:
            print(f"Disconnect error: {str(e)}")

    def _handle_ready(self):
        self.toolhead = self.printer.lookup_object('toolhead')
        if self.toolhead is None:
            raise self.printer.config_error("Toolhead not found in ValgAce module")

    def _handle_disconnect(self):
        self._disconnect()

    def _calc_crc(self, buffer: bytes) -> int:
        crc = 0xffff
        for byte in buffer:
            data = byte ^ (crc & 0xff)
            data ^= (data & 0x0f) << 4
            crc = ((data << 8) | (crc >> 8)) ^ (data >> 4) ^ (data << 3)
        return crc & 0xffff

    def send_request(self, request: Dict[str, Any], callback: Callable):
        if self._queue.qsize() >= self._max_queue_size:
            print("Request queue overflow, clearing...")
            while not self._queue.empty():
                _, cb = self._queue.get_nowait()
                if cb:
                    try:
                        cb({'error': 'Queue overflow'})
                    except:
                        pass
        request['id'] = self._get_next_request_id()
        self._queue.put((request, callback))

    def _get_next_request_id(self) -> int:
        self._request_id += 1
        if self._request_id >= 300000:
            self._request_id = 0
        return self._request_id

    def _send_request(self, request: Dict[str, Any]) -> bool:
        try:
            payload = json.dumps(request).encode('utf-8')
        except Exception as e:
            print(f"JSON encoding error: {str(e)}")
            return False

        crc = self._calc_crc(payload)
        packet = (
            bytes([0xFF, 0xAA]) +
            struct.pack('<H', len(payload)) +
            payload +
            struct.pack('<H', crc) +
            bytes([0xFE])
        )

        try:
            if self._serial and self._serial.is_open:
                self._serial.write(packet)
                return True
            else:
                raise SerialException("Serial port closed")
        except SerialException as e:
            print(f"Send error: {str(e)}")
            self._reconnect()
            return False

    def _reader_loop(self, eventtime):
        if not self._connected or not self._serial or not self._serial.is_open:
            return eventtime + 0.01
        try:
            raw_bytes = self._serial.read(16)
            if raw_bytes:
                self.read_buffer.extend(raw_bytes)
                self._process_messages()
        except SerialException as e:
            print(f"Read error: {str(e)}")
            self._reconnect()
        return eventtime + 0.01

    def _process_messages(self):
        incomplete_message_count = 0
        max_incomplete_messages_before_reset = 10
        while self.read_buffer:
            end_idx = self.read_buffer.find(b'\xfe')
            if end_idx == -1:
                break
            msg = self.read_buffer[:end_idx+1]
            self.read_buffer = self.read_buffer[end_idx+1:]
            if len(msg) < 7 or msg[0:2] != bytes([0xFF, 0xAA]):
                continue
            payload_len = struct.unpack('<H', msg[2:4])[0]
            expected_length = 4 + payload_len + 3
            if len(msg) < expected_length:
                print(f"Incomplete message received (expected {expected_length}, got {len(msg)})")
                incomplete_message_count += 1
                if incomplete_message_count > max_incomplete_messages_before_reset:
                    print("Too many incomplete messages, resetting connection")
                    self._reset_connection()
                    incomplete_message_count = 0
                continue
            incomplete_message_count = 0
            payload = msg[4:4+payload_len]
            crc = struct.unpack('<H', msg[4+payload_len:4+payload_len+2])[0]
            if crc != self._calc_crc(payload):
                return
            try:
                response = json.loads(payload.decode('utf-8'))
                self._handle_response(response)
            except json.JSONDecodeError as je:
                print(f"JSON decode error: {str(je)} Data: {msg}")
            except Exception as e:
                print(f"Message processing error: {str(e)} Data: {msg}")

    def _writer_loop(self, eventtime):
        if not self._connected:
            return eventtime + 0.05
        now = eventtime
        if now - self._last_status_request > (0.2 if self._park_in_progress else 1.0):
            self._request_status()
            self._last_status_request = now
        if not self._queue.empty():
            task = self._queue.get_nowait()
            if task:
                request, callback = task
                self._callback_map[request['id']] = callback
                if not self._send_request(request):
                    print("Failed to send request, requeuing...")
                    self._queue.put(task)
        return eventtime + 0.05

    def _request_status(self):
        def status_callback(response):
            if 'result' in response:
                self._info.update(response['result'])
        if self.reactor.monotonic() - self._last_status_request > (0.2 if self._park_in_progress else 1.0):
            try:
                self.send_request({
                    "id": self._get_next_request_id(),
                    "method": "get_status"
                }, status_callback)
                self._last_status_request = self.reactor.monotonic()
            except Exception as e:
                print(f"Status request error: {str(e)}")

    def _handle_response(self, response: dict):
        if 'id' in response:
            callback = self._callback_map.pop(response['id'], None)
            if callback:
                try:
                    callback(response)
                except Exception as e:
                    print(f"Callback error: {str(e)}")
        if 'result' in response and isinstance(response['result'], dict):
            result = response['result']
            self._info.update(result)
            if self._park_in_progress:
                current_status = result.get('status', 'unknown')
                current_assist_count = result.get('feed_assist_count', 0)
                if current_status == 'ready':
                    if current_assist_count != self._last_assist_count:
                        self._last_assist_count = current_assist_count
                        self._assist_hit_count = 0
                    else:
                        self._assist_hit_count += 1
                        if self._assist_hit_count >= self.park_hit_count:
                            self._complete_parking()
                            return
                    self.dwell(0.7, lambda: None)

    def _complete_parking(self):
        if not self._park_in_progress:
            return
        print(f"Parking completed for slot {self._park_index}")
        try:
            self.send_request({
                "method": "stop_feed_assist",
                "params": {"index": self._park_index}
            }, lambda r: None)
            # if self._park_is_toolchange:
            #     self.gcode.run_script_from_command(
            #         f'_ACE_POST_TOOLCHANGE FROM={self._park_previous_tool} TO={self._park_index}'
            #     )
        except Exception as e:
            print(f"Parking completion error: {str(e)}")
        finally:
            self._park_in_progress = False
            self._park_is_toolchange = False
            self._park_previous_tool = -1
            self._park_index = -1
            if self.disable_assist_after_toolchange:
                self._feed_assist_index = -1

    def dwell(self, delay: float = 1.0, callback: Optional[Callable] = None):
        """Асинхронная пауза через reactor"""
        if delay <= 0:
            if callback:
                self._main_queue.put(callback)
            return

        def timer_handler(event_time):
            if callback:
                self._main_queue.put(callback)
            return self.reactor.NEVER

        self.reactor.register_timer(timer_handler, self.reactor.monotonic() + delay)

    def pdwell(self, delay = 1.):
        currTs = self.reactor.monotonic()
        self.reactor.pause(currTs + delay)
        
    def _main_eval(self, eventtime):
        while not self._main_queue.empty():
            try:
                task = self._main_queue.get_nowait()
                if task:
                    task()
            except Exception as e:
                print(f"Main eval error: {str(e)}")
        return eventtime + 0.1

    def _reconnect(self):
        self._disconnect()
        self.dwell(1.0, lambda: None)
        self._connect()

    def _reset_connection(self):
        self._disconnect()
        self.dwell(1.0, lambda: None)
        self._connect()

    def cmd_ACE_STATUS(self, gcmd):
        try:
            status = json.dumps(self._info, indent=2)
            gcmd.respond_info(f"ACE Status:\n{status}")
        except Exception as e:
            print(f"Status command error: {str(e)}")
            gcmd.respond_raw("Error retrieving status")

    def cmd_ACE_DEBUG(self, gcmd):
        method = gcmd.get('METHOD')
        params = gcmd.get('PARAMS', '{}')
        try:
            request = {"method": method}
            if params.strip():
                request["params"] = json.loads(params)
            def callback(response):
                if 'result' in response:
                    result = response['result']
                    output = []
                    if method == "get_info":
                        output.append("=== Device Info ===")
                        output.append(f"Model: {result.get('model', 'Unknown')}")
                        output.append(f"Firmware: {result.get('firmware', 'Unknown')}")
                        output.append(f"Hardware: {result.get('hardware', 'Unknown')}")
                        output.append(f"Serial: {result.get('serial', 'Unknown')}")
                    else:
                        output.append("=== Status ===")
                        output.append(f"State: {result.get('status', 'Unknown')}")
                        output.append(f"Temperature: {result.get('temp', 'Unknown')}")
                        output.append(f"Fan Speed: {result.get('fan_speed', 'Unknown')}")
                        for slot in result.get('slots', []):
                            output.append(f"\nSlot {slot.get('index', '?')}:")
                            output.append(f"  Status: {slot.get('status', 'Unknown')}")
                            output.append(f"  Type: {slot.get('type', 'Unknown')}")
                            output.append(f"  Color: {slot.get('color', 'Unknown')}")
                    gcmd.respond_info("\n".join(output))
                else:
                    gcmd.respond_info(json.dumps(response, indent=2))
        except Exception as e:
            print(f"Debug command error: {str(e)}")
            gcmd.respond_raw(f"Error: {str(e)}")
        self.send_request(request, callback)

    def cmd_ACE_FILAMENT_INFO(self, gcmd):
        index = gcmd.get_int('INDEX', minval=0, maxval=3)
        try:
            def callback(response):
                if 'result' in response:
                    slot_info = response['result']
                    self.gcode.respond_info(str(slot_info))
                else:
                    self.gcode.respond_info('Error: No result in response')
            self.send_request({"method": "get_filament_info", "params": {"index": index}}, callback)
        except Exception as e:
            print(f"Filament info error: {str(e)}")
            self.gcode.respond_info('Error: ' + str(e))

    def cmd_ACE_START_DRYING(self, gcmd):
        temperature = gcmd.get_int('TEMP', minval=20, maxval=self.max_dryer_temperature)
        duration = gcmd.get_int('DURATION', 240, minval=1)
        def callback(response):
            if response.get('code', 0) != 0:
                gcmd.respond_raw(f"ACE Error: {response.get('msg', 'Unknown error')}")
            else:
                gcmd.respond_info(f"Drying started at {temperature}°C for {duration} minutes")
        self.send_request({
            "method": "drying",
            "params": {
                "temp": temperature,
                "fan_speed": 7000,
                "duration": duration * 60
            }
        }, callback)

    def cmd_ACE_STOP_DRYING(self, gcmd):
        def callback(response):
            if response.get('code', 0) != 0:
                gcmd.respond_raw(f"ACE Error: {response.get('msg', 'Unknown error')}")
            else:
                gcmd.respond_info("Drying stopped")
        self.send_request({"method": "drying_stop"}, callback)

    def cmd_ACE_ENABLE_FEED_ASSIST(self, gcmd):
        index = gcmd.get_int('INDEX', minval=0, maxval=3)
        def callback(response):
            if response.get('code', 0) != 0:
                gcmd.respond_raw(f"ACE Error: {response.get('msg', 'Unknown error')}")
            else:
                self._feed_assist_index = index
                gcmd.respond_info(f"Feed assist enabled for slot {index}")
                self.dwell(0.3, lambda: None)
        self.send_request({"method": "start_feed_assist", "params": {"index": index}}, callback)

    def cmd_ACE_DISABLE_FEED_ASSIST(self, gcmd):
        index = gcmd.get_int('INDEX', self._feed_assist_index, minval=0, maxval=3)
        def callback(response):
            if response.get('code', 0) != 0:
                gcmd.respond_raw(f"ACE Error: {response.get('msg', 'Unknown error')}")
            else:
                self._feed_assist_index = -1
                gcmd.respond_info(f"Feed assist disabled for slot {index}")
                self.dwell(0.3, lambda: None)
        self.send_request({"method": "stop_feed_assist", "params": {"index": index}}, callback)

    def _park_to_toolhead(self, index: int):
        def callback(response):
            if response.get('code', 0) != 0:
                raise ValueError(f"ACE Error: {response.get('msg', 'Unknown error')}")
            self._assist_hit_count = 0
            self._last_assist_count = response.get('result', {}).get('feed_assist_count', 0)
            self._park_in_progress = True
            self._park_index = index
            self.dwell(0.3, lambda: None)
        self.send_request({"method": "start_feed_assist", "params": {"index": index}}, callback)

    def cmd_ACE_PARK_TO_TOOLHEAD(self, gcmd):
        if self._park_in_progress:
            gcmd.respond_raw("Already parking to toolhead")
            return
        index = gcmd.get_int('INDEX', minval=0, maxval=3)
        if self._info['slots'][index]['status'] != 'ready':
            self.gcode.run_script_from_command(f"_ACE_ON_EMPTY_ERROR INDEX={index}")
            return
        self._park_to_toolhead(index)

    def cmd_ACE_FEED(self, gcmd):
        index = gcmd.get_int('INDEX', minval=0, maxval=3)
        length = gcmd.get_int('LENGTH', minval=1)
        speed = gcmd.get_int('SPEED', self.feed_speed, minval=1)
        def callback(response):
            if response.get('code', 0) != 0:
                gcmd.respond_raw(f"ACE Error: {response.get('msg', 'Unknown error')}")
        self.send_request({
            "method": "feed_filament",
            "params": {"index": index, "length": length, "speed": speed}
        }, callback)
        self.dwell((length / speed) + 0.1, lambda: None)

    def cmd_ACE_UPDATE_FEEDING_SPEED(self, gcmd):
        index = gcmd.get_int('INDEX', minval=0, maxval=3)
        speed = gcmd.get_int('SPEED', self.feed_speed, minval=1)
        def callback(response):
            if response.get('code', 0) != 0:
                gcmd.respond_raw(f"ACE Error: {response.get('msg', 'Unknown error')}")
        self.send_request({
            "method": "update_feeding_speed",
            "params": {"index": index, "speed": speed}
        }, callback)
        self.dwell(0.5, lambda: None)

    def cmd_ACE_STOP_FEED(self, gcmd):
        index = gcmd.get_int('INDEX', minval=0, maxval=3)
        def callback(response):
            if response.get('code', 0) != 0:
                gcmd.respond_raw(f"ACE Error: {response.get('msg', 'Unknown error')}")
            else:
                gcmd.respond_info("Feed stopped")
        self.send_request({
            "method": "stop_feed_filament",
            "params": {"index": index},
            },callback)
        self.dwell(0.5, lambda: None)

    def cmd_ACE_RETRACT(self, gcmd):
        index = gcmd.get_int('INDEX', minval=0, maxval=3)
        length = gcmd.get_int('LENGTH', minval=1)
        speed = gcmd.get_int('SPEED', self.retract_speed, minval=1)
        mode = gcmd.get_int('MODE', self.retract_mode, minval=0, maxval=1)
        def callback(response):
            if response.get('code', 0) != 0:
                gcmd.respond_raw(f"ACE Error: {response.get('msg', 'Unknown error')}")
        self.send_request({
            "method": "unwind_filament",
            "params": {"index": index, "length": length, "speed": speed, "mode": mode}
        }, callback)
        self.pdwell((length / speed) + 0.1)

    def cmd_ACE_UPDATE_RETRACT_SPEED(self, gcmd):
        index = gcmd.get_int('INDEX', minval=0, maxval=3)
        speed = gcmd.get_int('SPEED', self.feed_speed, minval=1)
        def callback(response):
            if response.get('code', 0) != 0:
                gcmd.respond_raw(f"ACE Error: {response.get('msg', 'Unknown error')}")
        self.send_request({
            "method": "update_unwinding_speed",
            "params": {"index": index, "speed": speed}
        }, callback)
        self.dwell(0.5, lambda: None)

    def cmd_ACE_STOP_RETRACT(self, gcmd):
        index = gcmd.get_int('INDEX', minval=0, maxval=3)
        def callback(response):
            if response.get('code', 0) != 0:
                gcmd.respond_raw(f"ACE Error: {response.get('msg', 'Unknown error')}")
            else:
                gcmd.respond_info("Feed stopped")
        self.send_request({
            "method": "stop_unwind_filament",
            "params": {"index": index},
            },callback)
        self.dwell(0.5, lambda: None)

    def cmd_ACE_CHANGE_TOOL(self, gcmd):
        tool = gcmd.get_int('TOOL', minval=-1, maxval=3)
        was = self.variables.get('ace_current_index', -1)

        if was == tool:
            gcmd.respond_info(f"Tool already set to {tool}")
            return

        if tool != -1 and self._info['slots'][tool]['status'] != 'ready':
            self.gcode.run_script_from_command(f"_ACE_ON_EMPTY_ERROR INDEX={tool}")
            return

        self.gcode.run_script_from_command(f"_ACE_PRE_TOOLCHANGE FROM={was} TO={tool}")
        self._park_is_toolchange = True
        self._park_previous_tool = was
        self.toolhead.wait_moves()
        self.variables['ace_current_index'] = tool
        self.gcode.run_script_from_command(f'SAVE_VARIABLE VARIABLE=ace_current_index VALUE={tool}')

        def callback(response):
            if response.get('code', 0) != 0:
                gcmd.respond_raw(f"ACE Error: {response.get('msg', 'Unknown error')}")
        if was != -1:
            self.send_request({
                "method": "unwind_filament",
                "params": {
                    "index": was,
                    "length": self.toolchange_retract_length,
                    "speed": self.retract_speed
                }
            }, callback)
            self.pdwell((self.toolchange_retract_length / self.retract_speed) + 0.1)
            self.pdwell(1.0)
            if tool != -1:
                while self._info['slots'][was]['status'] != 'ready':
                    self.pdwell(1.0)
                self.gcode.run_script_from_command(f'ACE_PARK_TO_TOOLHEAD INDEX={tool}')
                self.toolhead.wait_moves()
                self.pdwell(10.0)
                # while not self._park_in_progress :
                #     gcmd.respond_info(f"Park in progress")
                #     self.pdwell(5.0)
                self.gcode.run_script_from_command(f'_ACE_POST_TOOLCHANGE FROM={was} TO={tool}')
                self.toolhead.wait_moves()
                gcmd.respond_info(f"Tool changed from {was} to {tool}")
            else:
                self.gcode.run_script_from_command(f'_ACE_POST_TOOLCHANGE FROM={was} TO={tool}')
                self.toolhead.wait_moves()
                gcmd.respond_info(f"Tool changed from {was} to {tool}")
        else:
            self._park_to_toolhead(tool)
            self.pdwell(15.0)
            self.gcode.run_script_from_command(f'_ACE_POST_TOOLCHANGE FROM={was} TO={tool}')
            self.toolhead.wait_moves()
            self.variables['ace_current_index'] = tool
            gcmd.respond_info(f"Tool changed from {was} to {tool}")
            
    def _wait_for_slot_ready(self, index, on_ready, event_time):
        if self._info['slots'][index]['status'] == 'ready':
            on_ready()
            return self.reactor.NEVER
        return event_time + 0.5

    def cmd_ACE_INFINITY_SPOOL(self, gcmd):
        was = self.variables.get('ace_current_index', -1)
        infsp_status = self.infinity_spool_mode
        infsp_count = self.variables.get('ace_infsp_counter', 1)
        
        if infsp_status != True :
            gcmd.respond_info(f"ACE_INFINITY_SPOOL disabled")
            gcmd.respond_info(f"ACE_INFINITY_SPOOL dstatus {infsp_status}")
            return
        if was == -1:
            gcmd.respond_info(f"Tool is not set")
            return
        if infsp_count >= 4:
            gcmd.respond_info(f"No more ready spoll")
            return
        
        self.gcode.run_script_from_command(f"_ACE_PRE_INFINITYSPOOL")
        self.toolhead.wait_moves()
        
        if infsp_count == 1:
                tool = infsp_count
                self.gcode.run_script_from_command(f'ACE_PARK_TO_TOOLHEAD INDEX={tool}')
                self.pdwell(15.0)
                self.gcode.run_script_from_command(f'__ACE_POST_INFINITYSPOOL')
                self.toolhead.wait_moves()
                self.variables['ace_current_index'] = tool
                self.gcode.run_script_from_command(f'SAVE_VARIABLE VARIABLE=ace_current_index VALUE={tool}')
                self.variables['ace_infsp_counter'] = 2
                self.gcode.run_script_from_command(f'SAVE_VARIABLE VARIABLE=ace_infsp_counter VALUE=2')
                gcmd.respond_info(f"Tool changed from {was} to {tool}")
        elif infsp_count == 2:
                tool = infsp_count
                self.gcode.run_script_from_command(f'ACE_PARK_TO_TOOLHEAD INDEX={tool}')
                self.pdwell(15.0)
                self.gcode.run_script_from_command(f'__ACE_POST_INFINITYSPOOL')
                self.toolhead.wait_moves()
                self.variables['ace_current_index'] = tool
                self.gcode.run_script_from_command(f'SAVE_VARIABLE VARIABLE=ace_current_index VALUE={tool}')
                self.variables['ace_infsp_counter'] = 3
                self.gcode.run_script_from_command(f'SAVE_VARIABLE VARIABLE=ace_infsp_counter VALUE=3')                                
                gcmd.respond_info(f"Tool changed from {was} to {tool}")
        elif infsp_count == 3:
                tool = infsp_count
                self.gcode.run_script_from_command(f'ACE_PARK_TO_TOOLHEAD INDEX={tool}')
                self.pdwell(15.0)
                self.gcode.run_script_from_command(f'__ACE_POST_INFINITYSPOOL')
                self.toolhead.wait_moves()
                self.variables['ace_current_index'] = tool
                self.gcode.run_script_from_command(f'SAVE_VARIABLE VARIABLE=ace_current_index VALUE={tool}')
                self.variables['ace_infsp_counter'] = 4
                self.gcode.run_script_from_command(f'SAVE_VARIABLE VARIABLE=ace_infsp_counter VALUE=4')                  
                gcmd.respond_info(f"Tool changed from {was} to {tool}")


def load_config(config):
    return ValgAce(config)