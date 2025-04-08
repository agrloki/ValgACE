import serial
import serial.tools.list_ports
import threading
import time
import logging
import json
import struct
import queue
import traceback
import select
from typing import Optional, Dict, Any, Callable
from serial import SerialException

class ValgAce:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.gcode = self.printer.lookup_object('gcode')
        self._name = config.get_name()
        
        if self._name.startswith('ace '):
            self._name = self._name[4:]
        
        self.variables = self.printer.lookup_object('save_variables').allVariables
        self.lock = threading.Lock()  # Changed to proper threading lock
        self.read_buffer = bytearray()
        self.send_time = 0
        self.last_status_time = 0

        # Автопоиск устройства
        default_serial = self._find_ace_device()
        self.serial_name = config.get('serial', default_serial or '/dev/ttyACM0')
        self.baud = config.getint('baud', 115200)
        
        # Параметры конфигурации
        self.feed_speed = config.getint('feed_speed', 50)
        self.retract_speed = config.getint('retract_speed', 50)
        self.toolchange_retract_length = config.getint('toolchange_retract_length', 100)
        self.park_hit_count = config.getint('park_hit_count', 5)
        self.max_dryer_temperature = config.getint('max_dryer_temperature', 55)
        self.disable_assist_after_toolchange = config.getboolean('disable_assist_after_toolchange', True)

        # Состояние устройства
        self._info = self._get_default_info()
        self._callback_map = {}
        self._request_id = 0
        self._connected = False
        self._connection_attempts = 0
        self._max_connection_attempts = 5
        
        # Параметры работы
        self._feed_assist_index = -1
        self._last_assist_count = 0
        self._assist_hit_count = 0
        self._park_in_progress = False
        self._park_is_toolchange = False
        self._park_previous_tool = -1
        self._park_index = -1
        
        # Очереди и потоки
        self._queue = queue.Queue(maxsize=20)  # Limited queue size
        self._main_queue = queue.Queue()
        
        # Инициализация
        self._register_handlers()
        self._register_gcode_commands()

    def _find_ace_device(self) -> Optional[str]:
        """Поиск устройства ACE по VID/PID или описанию"""
        ACE_IDS = {
            'VID:PID': [(0x0483, 0x5740)],
            'DESCRIPTION': ['ACE', 'BunnyAce', 'DuckAce']
        }
        
        for port in serial.tools.list_ports.comports():
            if hasattr(port, 'vid') and hasattr(port, 'pid'):
                if (port.vid, port.pid) in ACE_IDS['VID:PID']:
                    return port.device
            
            if any(name in (port.description or '') for name in ACE_IDS['DESCRIPTION']):
                return port.device
        
        return None

    def _get_default_info(self) -> Dict[str, Any]:
        """Возвращает дефолтное состояние устройства"""
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
        """Регистрация системных обработчиков"""
        self.printer.register_event_handler('klippy:ready', self._handle_ready)
        self.printer.register_event_handler('klippy:disconnect', self._handle_disconnect)

    def _register_gcode_commands(self):
        """Регистрация всех команд G-Code"""
        commands = [
            ('ACE_DEBUG', self.cmd_ACE_DEBUG, "Debug connection"),
            ('ACE_STATUS', self.cmd_ACE_STATUS, "Get device status"),
            ('ACE_START_DRYING', self.cmd_ACE_START_DRYING, "Start drying"),
            ('ACE_STOP_DRYING', self.cmd_ACE_STOP_DRYING, "Stop drying"),
            ('ACE_ENABLE_FEED_ASSIST', self.cmd_ACE_ENABLE_FEED_ASSIST, "Enable feed assist"),
            ('ACE_DISABLE_FEED_ASSIST', self.cmd_ACE_DISABLE_FEED_ASSIST, "Disable feed assist"),
            ('ACE_PARK_TO_TOOLHEAD', self.cmd_ACE_PARK_TO_TOOLHEAD, "Park filament to toolhead"),
            ('ACE_FEED', self.cmd_ACE_FEED, "Feed filament"),
            ('ACE_RETRACT', self.cmd_ACE_RETRACT, "Retract filament"),
            ('ACE_CHANGE_TOOL', self.cmd_ACE_CHANGE_TOOL, "Change tool"),
            ('ACE_FILAMENT_INFO', self.cmd_ACE_FILAMENT_INFO, "Show filament info"),
        ]
        
        for name, func, desc in commands:
            self.gcode.register_command(name, func, desc=desc)

    def _connect(self) -> bool:
        """Попытка подключения к устройству"""
        if self._connected:
            return True
            
        for attempt in range(self._max_connection_attempts):
            try:
                self._serial = serial.Serial(
                    port=self.serial_name,
                    baudrate=self.baud,
                    timeout=0.1,
                    write_timeout=0.1)
                
                if self._serial.isOpen():
                    self._connected = True
                    self._info['status'] = 'ready'
                    logging.info(f"Connected to ACE at {self.serial_name}")
                    
                    if not hasattr(self, '_writer_thread') or not self._writer_thread.is_alive():
                        self._writer_thread = threading.Thread(target=self._writer_loop)
                        self._writer_thread.daemon = True
                        self._writer_thread.start()

                    if not hasattr(self, '_reader_thread') or not self._reader_thread.is_alive():
                        self._reader_thread = threading.Thread(target=self._reader_loop)
                        self._reader_thread.daemon = True
                        self._reader_thread.start()

                    if not hasattr(self, 'main_timer'):
                        self.main_timer = self.reactor.register_timer(self._main_eval, self.reactor.NOW)
                    
                    # Initial status request
                    self._request_device_info()
                    return True
                    
            except SerialException as e:
                logging.warning(f"Connection attempt {attempt + 1} failed: {str(e)}")
                time.sleep(1)
        
        logging.error("Failed to connect to ACE device")
        return False

    def _request_device_info(self):
        """Request device info with callback"""
        def info_callback(response):
            res = response.get('result', {})
            self.gcode.respond_info(f"Connected {res.get('model', 'Unknown')} {res.get('firmware', 'Unknown')}")
        
        self.send_request({"method": "get_info"}, info_callback, timeout=3.0)

    def _reconnect(self) -> bool:
        """Безопасное переподключение"""
        if not self._connected:
            return self._connect()
        
        try:
            old_writer = getattr(self, '_writer_thread', None)
            old_reader = getattr(self, '_reader_thread', None)
            
            self._connected = False
            
            if self._connect():
                if old_writer and old_writer.is_alive() and old_writer != threading.current_thread():
                    old_writer.join(timeout=0.5)
                
                if old_reader and old_reader.is_alive() and old_reader != threading.current_thread():
                    old_reader.join(timeout=0.5)
                
                return True
            return False
        except Exception as e:
            logging.error(f"Reconnect error: {str(e)}")
            return False

    def _disconnect(self):
        """Безопасное отключение"""
        if not self._connected:
            return
        
        self._connected = False
        
        try:
            if hasattr(self, '_serial'):
                self._serial.close()
        except:
            pass
        
        current_thread = threading.current_thread()
        if hasattr(self, '_writer_thread') and self._writer_thread != current_thread:
            self._writer_thread.join(timeout=1)
        
        if hasattr(self, '_reader_thread') and self._reader_thread != current_thread:
            self._reader_thread.join(timeout=1)
        
        if hasattr(self, 'main_timer'):
            self.reactor.unregister_timer(self.main_timer)

    def _send_request(self, request: Dict[str, Any]) -> bool:
        """Отправка запроса с CRC проверкой"""
        if not self._connected and not self._reconnect():
            raise SerialException("Device not connected")

        if 'id' not in request:
            request['id'] = self._request_id
            self._request_id += 1
            if self._request_id >= 300000:
                self._request_id = 0

        payload = json.dumps(request).encode('utf-8')
        crc = self._calc_crc(payload)
        
        packet = (
            bytes([0xFF, 0xAA]) +
            struct.pack('<H', len(payload)) +
            payload +
            struct.pack('<H', crc) +
            bytes([0xFE]))
        
        try:
            if not hasattr(self, '_serial') or not self._serial.is_open:
                if not self._reconnect():
                    return False
            
            with self.lock:  # Используем блокировку
                self._serial.write(packet)
                self.send_time = time.time()
                return True
        except SerialException:
            logging.error("Write error, attempting reconnect")
            self._reconnect()
            return False
        except Exception as e:
            logging.error(f"Unexpected write error: {str(e)}")
            return False

    def _calc_crc(self, buffer: bytes) -> int:
        """Вычисление CRC для пакета"""
        crc = 0xffff
        for byte in buffer:
            data = byte
            data ^= crc & 0xff
            data ^= (data & 0x0f) << 4
            crc = ((data << 8) | (crc >> 8)) ^ (data >> 4) ^ (data << 3)
        return crc

    def _reader_loop(self):
        """Основной цикл чтения"""
        while getattr(self, '_connected', False):
            try:
                eventtime = self.reactor.monotonic()
                next_eventtime = self._reader(eventtime)
                time.sleep(max(0, min(0.05, next_eventtime - self.reactor.monotonic())))  # Max 50ms delay
            except Exception as e:
                logging.error(f"Reader loop error: {str(e)}")
                time.sleep(0.5)

    def _reader(self, eventtime):
        """Обработчик чтения данных"""
        try:
            # Используем select для проверки доступности данных
            if select.select([self._serial], [], [], 0.05)[0]:
                raw_bytes = self._serial.read(self._serial.in_waiting or 1)
            else:
                return eventtime + 0.05
        except SerialException:
            self.gcode.respond_info("Serial read error: " + traceback.format_exc())
            return eventtime + 0.5

        if raw_bytes:
            self.read_buffer.extend(raw_bytes)

        # Поиск завершающего байта
        end_pos = self.read_buffer.find(b'\xfe')
        if end_pos < 0:
            return eventtime + 0.05

        packet = self.read_buffer[:end_pos + 1]
        self.read_buffer = self.read_buffer[end_pos + 1:]

        if len(packet) < 7:
            return eventtime + 0.05

        if packet[0:2] != bytes([0xFF, 0xAA]):
            self.gcode.respond_info("Invalid packet header")
            return eventtime + 0.05

        payload_len = struct.unpack('<H', packet[2:4])[0]
        if len(packet) < 4 + payload_len + 3:  # header + len + payload + crc + footer
            return eventtime + 0.05

        payload = packet[4:4 + payload_len]
        crc_data = packet[4 + payload_len:4 + payload_len + 2]
        crc = struct.pack('@H', self._calc_crc(payload))

        if crc_data != crc:
            self.gcode.respond_info('Invalid CRC')
            return eventtime + 0.05

        try:
            response = json.loads(payload.decode('utf-8'))

            # Обработка парковки филамента
            if self._park_in_progress and 'result' in response:
                self._info = response['result']
                if self._info['status'] == 'ready':
                    new_assist_count = self._info.get('feed_assist_count', 0)

                    if new_assist_count > self._last_assist_count:
                        self._last_assist_count = new_assist_count
                        self._assist_hit_count = 0
                        self.dwell(0.7, True)
                    elif self._assist_hit_count < self.park_hit_count:
                        self._assist_hit_count += 1
                        self.dwell(0.7, True)
                    else:
                        self._complete_parking()

            # Вызываем callback
            if 'id' in response and response['id'] in self._callback_map:
                callback = self._callback_map.pop(response['id'])
                try:
                    callback(response)
                except Exception as e:
                    logging.error(f"Callback error: {str(e)}")

        except json.JSONDecodeError:
            self.gcode.respond_info("Invalid JSON from ACE")
        except Exception as e:
            self.gcode.respond_info(f"Error processing response: {str(e)}")

        return eventtime + 0.05

    def _complete_parking(self):
        """Завершение процесса парковки"""
        self._park_in_progress = False
        logging.info(f'ACE: Parked to toolhead with assist count: {self._last_assist_count}')

        def stop_callback(response):
            if response.get('code', 0) != 0:
                logging.error(f"Failed to stop feed assist: {response.get('msg', 'Unknown error')}")
            
            if self._park_is_toolchange:
                self._park_is_toolchange = False
                def post_toolchange():
                    self.gcode.run_script_from_command(
                        f'_ACE_POST_TOOLCHANGE FROM={self._park_previous_tool} TO={self._park_index}')
                self._main_queue.put(post_toolchange)
                
                if self.disable_assist_after_toolchange:
                    self.send_request({
                        "method": "stop_feed_assist",
                        "params": {"index": self._park_index}
                    }, lambda x: None)

        self.send_request({
            "method": "stop_feed_assist",
            "params": {"index": self._park_index}
        }, stop_callback)

    def _writer_loop(self):
        """Безопасный цикл записи"""
        while getattr(self, '_connected', False):
            try:
                # Обработка задач с приоритетом
                if not self._queue.empty():
                    task = self._queue.get_nowait()
                    if task:
                        request, callback, timeout = task
                        self._callback_map[request['id']] = callback
                        if not self._send_request(request):
                            continue
                
                # Периодический запрос статуса (не чаще чем раз в 2 секунды)
                current_time = time.time()
                if current_time - self.last_status_time > 2.0:
                    def status_callback(response):
                        if 'result' in response:
                            self._info = response['result']
                    
                    self.send_request({
                        "id": self._request_id,
                        "method": "get_status"
                    }, status_callback, timeout=1.0)
                    self.last_status_time = current_time
                
                # Динамическая задержка
                delay = 0.05 if not self._queue.empty() else (0.1 if not self._park_in_progress else 0.3)
                time.sleep(delay)

            except SerialException:
                logging.error("Serial write error")
                if self._connected:
                    self._reconnect()
                time.sleep(1)
            except Exception as e:
                logging.error(f"Writer loop error: {str(e)}")
                time.sleep(1)

    def _main_eval(self, eventtime):
        """Обработка задач в основном потоке"""
        while not self._main_queue.empty():
            try:
                task = self._main_queue.get_nowait()
                if task:
                    task()
            except:
                pass
        return eventtime + 0.1

    def _handle_ready(self):
        """Обработчик готовности Klipper"""
        if not self._connect():
            logging.error("Failed to connect to ACE on startup")

    def _handle_disconnect(self):
        """Обработчик отключения Klipper"""
        self._disconnect()

    def send_request(self, request: Dict[str, Any], callback: Callable, timeout: float = 5.0):
        """Добавление запроса в очередь с таймаутом"""
        if not self._connected and not self._reconnect():
            raise SerialException("Device not connected")
        
        if 'id' not in request:
            request['id'] = self._request_id
            self._request_id += 1
        
        response_event = threading.Event()
        response_data = [None]
        
        def wrapped_callback(response):
            response_data[0] = response
            callback(response)
            response_event.set()
        
        try:
            self._queue.put((request, wrapped_callback, timeout), timeout=1.0)
        except queue.Full:
            raise SerialException("Request queue is full")
        
        if not response_event.wait(timeout):
            logging.warning(f"Timeout waiting for response to request {request['id']}")
            raise SerialException("Response timeout")
        
        return response_data[0]

    def dwell(self, delay: float = 1.0, on_main: bool = False):
        """Пауза с возможностью выполнения в основном потоке"""
        toolhead = self.printer.lookup_object('toolhead')
        def main_callback():
            toolhead.dwell(delay)
        
        if on_main:
            self._main_queue.put(main_callback)
        else:
            main_callback()

    # ==================== G-CODE COMMANDS ====================

    cmd_ACE_STATUS_help = "Get current device status"
    def cmd_ACE_STATUS(self, gcmd):
        status = json.dumps(self._info, indent=2)
        gcmd.respond_info(f"ACE Status:\n{status}")

    cmd_ACE_DEBUG_help = "Debug ACE connection"
    def cmd_ACE_DEBUG(self, gcmd):
        method = gcmd.get('METHOD')
        params = gcmd.get('PARAMS', '{}')
        
        try:
            request = {"method": method}
            if params.strip():
                request["params"] = json.loads(params)

            response = self.send_request(request, lambda x: None, timeout=3.0)
            gcmd.respond_info(json.dumps(response, indent=2))
        except Exception as e:
            gcmd.respond_raw(f"Error: {str(e)}")

    cmd_ACE_FILAMENT_INFO_help = 'ACE_FILAMENT_INFO INDEX='
    def cmd_ACE_FILAMENT_INFO(self, gcmd):
        index = gcmd.get_int('INDEX', minval=0, maxval=3)
        try:
            response = self.send_request(
                {"method": "get_filament_info", "params": {"index": index}},
                lambda x: None,
                timeout=3.0
            )
            if 'result' in response:
                self.gcode.respond_info(str(response['result']))
            else:
                self.gcode.respond_info('Error: No result in response')
        except Exception as e:
            self.gcode.respond_info('Error: ' + str(e))

    cmd_ACE_START_DRYING_help = "Start filament drying"
    def cmd_ACE_START_DRYING(self, gcmd):
        temperature = gcmd.get_int('TEMP', minval=20, maxval=self.max_dryer_temperature)
        duration = gcmd.get_int('DURATION', 240, minval=1)

        try:
            response = self.send_request({
                "method": "drying",
                "params": {
                    "temp": temperature,
                    "fan_speed": 7000,
                    "duration": duration * 60
                }
            }, lambda x: None, timeout=3.0)
            
            if response.get('code', 0) != 0:
                gcmd.respond_raw(f"ACE Error: {response.get('msg', 'Unknown error')}")
            else:
                gcmd.respond_info(f"Drying started at {temperature}°C for {duration} minutes")
        except Exception as e:
            gcmd.respond_raw(f"Error: {str(e)}")

    cmd_ACE_STOP_DRYING_help = "Stop filament drying"
    def cmd_ACE_STOP_DRYING(self, gcmd):
        try:
            response = self.send_request({"method": "drying_stop"}, lambda x: None, timeout=3.0)
            if response.get('code', 0) != 0:
                gcmd.respond_raw(f"ACE Error: {response.get('msg', 'Unknown error')}")
            else:
                gcmd.respond_info("Drying stopped")
        except Exception as e:
            gcmd.respond_raw(f"Error: {str(e)}")

    cmd_ACE_ENABLE_FEED_ASSIST_help = "Enable feed assist"
    def cmd_ACE_ENABLE_FEED_ASSIST(self, gcmd):
        index = gcmd.get_int('INDEX', minval=0, maxval=3)

        try:
            response = self.send_request({
                "method": "start_feed_assist",
                "params": {"index": index}
            }, lambda x: None, timeout=3.0)
            
            if response.get('code', 0) != 0:
                gcmd.respond_raw(f"ACE Error: {response.get('msg', 'Unknown error')}")
            else:
                self._feed_assist_index = index
                gcmd.respond_info(f"Feed assist enabled for slot {index}")
                self.dwell(0.3)
        except Exception as e:
            gcmd.respond_raw(f"Error: {str(e)}")

    cmd_ACE_DISABLE_FEED_ASSIST_help = "Disable feed assist"
    def cmd_ACE_DISABLE_FEED_ASSIST(self, gcmd):
        index = gcmd.get_int('INDEX', self._feed_assist_index, minval=0, maxval=3)

        try:
            response = self.send_request({
                "method": "stop_feed_assist",
                "params": {"index": index}
            }, lambda x: None, timeout=3.0)
            
            if response.get('code', 0) != 0:
                gcmd.respond_raw(f"ACE Error: {response.get('msg', 'Unknown error')}")
            else:
                self._feed_assist_index = -1
                gcmd.respond_info(f"Feed assist disabled for slot {index}")
                self.dwell(0.3)
        except Exception as e:
            gcmd.respond_raw(f"Error: {str(e)}")

    def _park_to_toolhead(self, index: int):
        """Внутренний метод парковки филамента"""
        try:
            response = self.send_request({
                "method": "start_feed_assist",
                "params": {"index": index}
            }, lambda x: None, timeout=3.0)
            
            if response.get('code', 0) != 0:
                raise ValueError(f"ACE Error: {response.get('msg', 'Unknown error')}")
            
            self._assist_hit_count = 0
            self._last_assist_count = 0
            self._park_in_progress = True
            self._park_index = index
            self.dwell(0.3)
        except Exception as e:
            logging.error(f"Parking error: {str(e)}")

    cmd_ACE_PARK_TO_TOOLHEAD_help = "Park filament to toolhead"
    def cmd_ACE_PARK_TO_TOOLHEAD(self, gcmd):
        if self._park_in_progress:
            gcmd.respond_raw("Already parking to toolhead")
            return

        index = gcmd.get_int('INDEX', minval=0, maxval=3)
        
        if self._info['slots'][index]['status'] != 'ready':
            self.gcode.run_script_from_command(f"_ACE_ON_EMPTY_ERROR INDEX={index}")
            return

        self._park_to_toolhead(index)

    cmd_ACE_FEED_help = "Feed filament"
    def cmd_ACE_FEED(self, gcmd):
        index = gcmd.get_int('INDEX', minval=0, maxval=3)
        length = gcmd.get_int('LENGTH', minval=1)
        speed = gcmd.get_int('SPEED', self.feed_speed, minval=1)

        try:
            response = self.send_request({
                "method": "feed_filament",
                "params": {
                    "index": index,
                    "length": length,
                    "speed": speed
                }
            }, lambda x: None, timeout=max(5.0, (length / speed) + 1.0))
            
            if response.get('code', 0) != 0:
                gcmd.rrespond_raw(f"ACE Error: {response.get('msg', 'Unknown error')}")
            
            self.dwell((length / speed) + 0.1)
        except Exception as e:
            gcmd.respond_raw(f"Error: {str(e)}")

    cmd_ACE_RETRACT_help = "Retract filament"
    def cmd_ACE_RETRACT(self, gcmd):
        index = gcmd.get_int('INDEX', minval=0, maxval=3)
        length = gcmd.get_int('LENGTH', minval=1)
        speed = gcmd.get_int('SPEED', self.retract_speed, minval=1)

        try:
            response = self.send_request({
                "method": "unwind_filament",
                "params": {
                    "index": index,
                    "length": length,
                    "speed": speed
                }
            }, lambda x: None, timeout=max(5.0, (length / speed) + 1.0))
            
            if response.get('code', 0) != 0:
                gcmd.respond_raw(f"ACE Error: {response.get('msg', 'Unknown error')}")
            
            self.dwell((length / speed) + 0.1)
        except Exception as e:
            gcmd.respond_raw(f"Error: {str(e)}")

    cmd_ACE_CHANGE_TOOL_help = "Change tool"
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
        self.variables['ace_current_index'] = tool
        self.gcode.run_script_from_command(f'SAVE_VARIABLE VARIABLE=ace_current_index VALUE={tool}')

        try:
            if was != -1:
                response = self.send_request({
                    "method": "unwind_filament",
                    "params": {
                        "index": was,
                        "length": self.toolchange_retract_length,
                        "speed": self.retract_speed
                    }
                }, lambda x: None, timeout=5.0)
                
                if response.get('code', 0) != 0:
                    gcmd.respond_raw(f"ACE Error: {response.get('msg', 'Unknown error')}")
                
                self.dwell((self.toolchange_retract_length / self.retract_speed) + 0.1)

                while self._info['status'] != 'ready':
                    self.dwell(0.5)
                
                self.dwell(0.25)

                if tool != -1:
                    self.gcode.run_script_from_command(f'ACE_PARK_TO_TOOLHEAD INDEX={tool}')
                else:
                    self.gcode.run_script_from_command(f'_ACE_POST_TOOLCHANGE FROM={was} TO={tool}')
            else:
                self._park_to_toolhead(tool)
        except Exception as e:
            gcmd.respond_raw(f"Error: {str(e)}")

def load_config(config):
    return ValgAce(config)