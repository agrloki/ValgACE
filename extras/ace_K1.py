import os
import serial
import serial.tools.list_ports
import logging
import logging.handlers
import json
import struct
import queue
import threading
import time
from typing import Optional, Dict, Any, Callable
from serial import SerialException
from contextlib import contextmanager

class ValgAce:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.gcode = self.printer.lookup_object('gcode')
        self._name = config.get_name()
        self.error_prefix = "ACE Error: "
        
        if self._name.startswith('ace '):
            self._name = self._name[4:]
        
        self.variables = self.printer.lookup_object('save_variables').allVariables
        self.read_buffer = bytearray()
        self.send_time = 0
        self._last_status_request = 0
        self._serial = None
        self._connection_mutex = threading.Lock()
        self._callback_lock = threading.Lock()

        # Инициализация логирования
        self._init_logging(config)
        
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
        
        # Очереди
        self._queue = queue.Queue(maxsize=self._max_queue_size)
        
        # Инициализация
        self._register_handlers()
        self._register_gcode_commands()
        self.reactor.register_timer(self._status_update_event, self.reactor.NOW)

    def _init_logging(self, config):
        """Инициализация системы логирования"""
        disable_logging = config.getboolean('disable_logging', False)
        if disable_logging:
            self.logger = logging.getLogger('ace')
            self.logger.addHandler(logging.NullHandler())
            return
           
        log_dir = config.get('log_dir', '/var/log/ace')
        log_level = config.get('log_level', 'INFO').upper()
        max_log_size = config.getint('max_log_size', 10) * 1024 * 1024
        log_backup_count = config.getint('log_backup_count', 3)
        
        try:
            os.makedirs(log_dir, exist_ok=True)
        except OSError as e:
            print(f"{self.error_prefix}Error creating log directory: {e}")
            log_dir = '/tmp'
    
        log_file = os.path.join(log_dir, 'ace.log')
       
        log_format = '%(asctime)s [%(levelname)s] %(message)s'
        date_format = '%Y-%m-%d %H:%M:%S'
       
        self.logger = logging.getLogger('ace')
        self.logger.setLevel(getattr(logging, log_level, logging.INFO))
       
        for handler in self.logger.handlers[:]:
            self.logger.removeHandler(handler)
       
        file_handler = logging.handlers.RotatingFileHandler(
            log_file,
            maxBytes=max_log_size,
            backupCount=log_backup_count
        )
        file_handler.setFormatter(logging.Formatter(log_format, date_format))
        self.logger.addHandler(file_handler)
       
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(logging.Formatter(log_format, date_format))
        self.logger.addHandler(console_handler)
       
        self.logger.propagate = False
        self.logger.info("ACE logging initialized")

    def _find_ace_device(self) -> Optional[str]:
        """Поиск устройства ACE по VID/PID или описанию"""
        ACE_IDS = {
            'VID:PID': [(0x28e9, 0x018a)],
            'DESCRIPTION': ['ACE', 'BunnyAce', 'DuckAce']
        }
        
        for port in serial.tools.list_ports.comports():
            if hasattr(port, 'vid') and hasattr(port, 'pid'):
                if (port.vid, port.pid) in ACE_IDS['VID:PID']:
                    self.logger.info(f"Found ACE device by VID/PID at {port.device}")
                    return port.device
            
            if any(name in (port.description or '') for name in ACE_IDS['DESCRIPTION']):
                self.logger.info(f"Found ACE device by description at {port.device}")
                return port.device
        
        self.logger.warning("No ACE device found by auto-detection")
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

    def _status_update_event(self, eventtime):
        """Периодический запрос статуса устройства"""
        try:
            if not self._connected:
                return eventtime + 5.0
                
            if eventtime - self._last_status_request > 1.0:
                self._request_status()
                self._last_status_request = eventtime
                
            return eventtime + 0.5
        except Exception as e:
            self.logger.error(f"{self.error_prefix}Status update error: {str(e)}")
            return eventtime + 5.0

    def _connect(self):
        """Подключение к устройству"""
        def connect_task():
            with self._connection_mutex:
                if self._connected:
                    return
                    
                for attempt in range(self._max_connection_attempts):
                    try:
                        self._serial = serial.Serial(
                            port=self.serial_name,
                            baudrate=self.baud,
                            timeout=self._read_timeout,
                            write_timeout=self._write_timeout)
                        
                        if self._serial.is_open:
                            self._connected = True
                            self._info['status'] = 'ready'
                            self.logger.info(f"Connected to ACE at {self.serial_name}")
                            
                            def info_callback(response):
                                res = response['result']
                                self.logger.info(f"Device info: {res.get('model', 'Unknown')} {res.get('firmware', 'Unknown')}")
                            self.send_request({"method": "get_info"}, info_callback)
                            
                            return
                            
                    except SerialException as e:
                        msg = f"{self.error_prefix}Connection attempt {attempt + 1} failed: {str(e)}"
                        self.logger.warning(msg)
                        time.sleep(1)
                
                msg = f"{self.error_prefix}Failed to connect to ACE device"
                self.logger.error(msg)

        threading.Thread(target=connect_task, daemon=True).start()

    def _disconnect(self):
        """Отключение от устройства"""
        def disconnect_task():
            with self._connection_mutex:
                if not self._connected:
                    return
                
                self._connected = False
                
                try:
                    if self._serial:
                        self._serial.close()
                except:
                    pass
                
                self._info['status'] = 'disconnected'
                self.logger.info("Disconnected from ACE")

        threading.Thread(target=disconnect_task, daemon=True).start()

    def _reconnect(self):
        """Переподключение к устройству"""
        def reconnect_task():
            self._disconnect()
            time.sleep(1)
            self._connect()

        threading.Thread(target=reconnect_task, daemon=True).start()

    def _calc_crc(self, buffer: bytes) -> int:
        """Расчет CRC для сообщения"""
        crc = 0xffff
        for byte in buffer:
            data = byte ^ (crc & 0xff)
            data ^= (data & 0x0f) << 4
            crc = ((data << 8) | (crc >> 8)) ^ (data >> 4) ^ (data << 3)
        return crc & 0xffff

    def _send_request(self, request: Dict[str, Any]) -> bool:
        """Отправка запроса на устройство"""
        with self._connection_mutex:
            try:
                if not self._connected:
                    raise SerialException(f"{self.error_prefix}Device not connected")

                if 'id' not in request:
                    request['id'] = self._get_next_request_id()

                payload = json.dumps(request).encode('utf-8')
                crc = self._calc_crc(payload)
                
                packet = (
                    bytes([0xFF, 0xAA]) +
                    struct.pack('<H', len(payload)) +
                    payload +
                    struct.pack('<H', crc) +
                    bytes([0xFE]))
                
                self._serial.write(packet)
                self.send_time = time.time()
                self.logger.debug(f"Request {request['id']} sent")
                return True
            except SerialException as e:
                msg = f"{self.error_prefix}Send error: {str(e)}"
                self.logger.error(msg)
                self._reconnect()
                return False
            except Exception as e:
                msg = f"{self.error_prefix}Unexpected error: {str(e)}"
                self.logger.error(msg)
                return False

    def _get_next_request_id(self) -> int:
        """Генерация ID запроса"""
        self._request_id += 1
        if self._request_id >= 300000:
            self._request_id = 0
        return self._request_id

    def _process_message(self, msg: bytes):
        """Обработка входящих сообщений"""
        if len(msg) < 7 or msg[0:2] != bytes([0xFF, 0xAA]):
            return
            
        try:
            payload_len = struct.unpack('<H', msg[2:4])[0]
            if len(msg) < 4 + payload_len + 3:
                return
                
            payload = msg[4:4+payload_len]
            crc = struct.unpack('<H', msg[4+payload_len:4+payload_len+2])[0]
            
            if crc != self._calc_crc(payload):
                self.logger.warning(f"{self.error_prefix}CRC mismatch")
                return
                
            response = json.loads(payload.decode('utf-8'))
            self._handle_response(response)
            
        except Exception as e:
            self.logger.error(f"{self.error_prefix}Message processing error: {str(e)}")

    def _handle_response(self, response: dict):
        """Обработка ответа от устройства"""
        if 'id' in response:
            callback = self._callback_map.pop(response['id'], None)
            if callback:
                try:
                    callback(response)
                except Exception as e:
                    self.logger.error(f"{self.error_prefix}Callback error: {str(e)}")
        
        if 'result' in response and isinstance(response['result'], dict):
            result = response['result']
            self._info.update(result)
            
            if self._park_in_progress:
                self._process_parking_status(result)

    def _process_parking_status(self, result: dict):
        """Обработка статуса парковки"""
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
            
            self.dwell(0.7)

    def _complete_parking(self):
        """Завершение процесса парковки"""
        def parking_task():
            if not self._park_in_progress:
                return
                
            self.logger.info(f"Parking completed for slot {self._park_index}")
            
            self.send_request({
                "method": "stop_feed_assist",
                "params": {"index": self._park_index}
            }, lambda r: None)
            
            if self._park_is_toolchange:
                self.gcode.run_script_from_command(
                    f'_ACE_POST_TOOLCHANGE FROM={self._park_previous_tool} TO={self._park_index}'
                )
            
            self._park_in_progress = False
            self._park_is_toolchange = False
            self._park_previous_tool = -1
            self._park_index = -1
            
            if self.disable_assist_after_toolchange:
                self._feed_assist_index = -1

        threading.Thread(target=parking_task, daemon=True).start()

    def _request_status(self):
        """Запрос статуса устройства"""
        def status_callback(response):
            if 'result' in response:
                self._info.update(response['result'])

        self.send_request({
            "method": "get_status"
        }, status_callback)

    def send_request(self, request: Dict[str, Any], callback: Callable = None):
        """Добавление запроса в очередь"""
        if self._queue.qsize() >= self._max_queue_size:
            self.logger.warning(f"{self.error_prefix}Request queue overflow, clearing...")
            with self._queue.mutex:
                self._queue.queue.clear()
            
        request['id'] = self._get_next_request_id()
        if callback:
            with self._callback_lock:
                self._callback_map[request['id']] = callback
        self._queue.put(request)

    def dwell(self, delay: float):
        """Пауза через toolhead.dwell()"""
        def dwell_task():
            toolhead = self.printer.lookup_object('toolhead')
            toolhead.dwell(delay)
        self.reactor.register_callback(dwell_task)

    def _handle_ready(self):
        """Обработчик готовности Klipper"""
        self._connect()

    def _handle_disconnect(self):
        """Обработчик отключения Klipper"""
        self._disconnect()

    # ==================== G-CODE COMMANDS ====================

    cmd_ACE_STATUS_help = "Get current device status"
    def cmd_ACE_STATUS(self, gcmd):
        """Обработчик команды ACE_STATUS"""
        status = json.dumps(self._info, indent=2)
        gcmd.respond_raw(f"ACE Status:\n{status}")

    cmd_ACE_DEBUG_help = "Debug ACE connection"
    def cmd_ACE_DEBUG(self, gcmd):
        """Обработчик команды ACE_DEBUG"""
        method = gcmd.get('METHOD')
        params = gcmd.get('PARAMS', '{}')
        
        response_event = threading.Event()
        response_data = [None]

        def callback(response):
            response_data[0] = response
            response_event.set()

        try:
            request = {"method": method}
            if params.strip():
                try:
                    request["params"] = json.loads(params)
                except json.JSONDecodeError as e:
                    gcmd.respond_raw(f"{self.error_prefix}Invalid PARAMS format: {str(e)}")
                    return

            self.send_request(request, callback)
            
            if not response_event.wait(self._response_timeout):
                gcmd.respond_raw(f"{self.error_prefix}Timeout waiting for response")
                return

            response = response_data[0]
            if response is None:
                gcmd.respond_raw(f"{self.error_prefix}No response received")
                return

            gcmd.respond_raw(json.dumps(response, indent=2))

        except Exception as e:
            gcmd.respond_raw(f"{self.error_prefix}{str(e)}")

    cmd_ACE_FILAMENT_INFO_help = 'ACE_FILAMENT_INFO INDEX='
    def cmd_ACE_FILAMENT_INFO(self, gcmd):
        """Handler for ACE_FILAMENT_INFO command"""
        index = gcmd.get_int('INDEX', minval=0, maxval=3)
        
        response_event = threading.Event()
        response_data = [None]

        def callback(response):
            response_data[0] = response
            response_event.set()

        self.send_request(
            {"method": "get_filament_info", "params": {"index": index}},
            callback
        )

        if not response_event.wait(self._response_timeout):
            gcmd.respond_raw(f"{self.error_prefix}Timeout waiting for response")
            return

        response = response_data[0]
        if response and 'result' in response:
            gcmd.respond_raw(str(response['result']))
        else:
            gcmd.respond_raw(f"{self.error_prefix}Invalid response")

    cmd_ACE_START_DRYING_help = "Start filament drying"
    def cmd_ACE_START_DRYING(self, gcmd):
        """Обработчик команды ACE_START_DRYING"""
        temperature = gcmd.get_int('TEMP', minval=20, maxval=self.max_dryer_temperature)
        duration = gcmd.get_int('DURATION', 240, minval=1)

        def callback(response):
            if response.get('code', 0) != 0:
                gcmd.respond_raw(f"{self.error_prefix}{response.get('msg', 'Unknown error')}")
            else:
                gcmd.respond_raw(f"Drying started at {temperature}°C for {duration} minutes")

        self.send_request({
            "method": "drying",
            "params": {
                "temp": temperature,
                "fan_speed": 7000,
                "duration": duration * 60
            }
        }, callback)

    cmd_ACE_STOP_DRYING_help = "Stop filament drying"
    def cmd_ACE_STOP_DRYING(self, gcmd):
        """Обработчик команды ACE_STOP_DRYING"""
        def callback(response):
            if response.get('code', 0) != 0:
                gcmd.respond_raw(f"{self.error_prefix}{response.get('msg', 'Unknown error')}")
            else:
                gcmd.respond_raw("Drying stopped")

        self.send_request({"method": "drying_stop"}, callback)

    cmd_ACE_ENABLE_FEED_ASSIST_help = "Enable feed assist"
    def cmd_ACE_ENABLE_FEED_ASSIST(self, gcmd):
        """Обработчик команды ACE_ENABLE_FEED_ASSIST"""
        index = gcmd.get_int('INDEX', minval=0, maxval=3)

        def callback(response):
            if response.get('code', 0) != 0:
                gcmd.respond_raw(f"{self.error_prefix}{response.get('msg', 'Unknown error')}")
            else:
                self._feed_assist_index = index
                gcmd.respond_raw(f"Feed assist enabled for slot {index}")
                self.dwell(0.3)

        self.send_request({
            "method": "start_feed_assist",
            "params": {"index": index}
        }, callback)

    cmd_ACE_DISABLE_FEED_ASSIST_help = "Disable feed assist"
    def cmd_ACE_DISABLE_FEED_ASSIST(self, gcmd):
        """Обработчик команды ACE_DISABLE_FEED_ASSIST"""
        index = gcmd.get_int('INDEX', self._feed_assist_index, minval=0, maxval=3)

        def callback(response):
            if response.get('code', 0) != 0:
                gcmd.respond_raw(f"{self.error_prefix}{response.get('msg', 'Unknown error')}")
            else:
                self._feed_assist_index = -1
                gcmd.respond_raw(f"Feed assist disabled for slot {index}")
                self.dwell(0.3)

        self.send_request({
            "method": "stop_feed_assist",
            "params": {"index": index}
        }, callback)

    def _park_to_toolhead(self, index: int):
        """Внутренний метод парковки филамента"""
        def callback(response):
            if response.get('code', 0) != 0:
                self.logger.error(f"{self.error_prefix}Failed to park to toolhead: {response.get('msg', 'Unknown error')}")
                return
            
            self._assist_hit_count = 0
            self._last_assist_count = response.get('result', {}).get('feed_assist_count', 0)
            self._park_in_progress = True
            self._park_index = index
            self.logger.info(f"Parking to toolhead started for slot {index}")
            self.dwell(0.3)

        self.send_request({
            "method": "start_feed_assist",
            "params": {"index": index}
        }, callback)

    cmd_ACE_PARK_TO_TOOLHEAD_help = "Park filament to toolhead"
    def cmd_ACE_PARK_TO_TOOLHEAD(self, gcmd):
        """Обработчик команды ACE_PARK_TO_TOOLHEAD"""
        if self._park_in_progress:
            gcmd.respond_raw(f"{self.error_prefix}Already parking to toolhead")
            return

        index = gcmd.get_int('INDEX', minval=0, maxval=3)
        
        if self._info['slots'][index]['status'] != 'ready':
            gcmd.respond_raw(f"{self.error_prefix}Slot {index} is empty, cannot park")
            return

        self._park_to_toolhead(index)
        gcmd.respond_raw(f"Parking to toolhead initiated for slot {index}")

    cmd_ACE_FEED_help = "Feed filament"
    def cmd_ACE_FEED(self, gcmd):
        """Обработчик команды ACE_FEED"""
        index = gcmd.get_int('INDEX', minval=0, maxval=3)
        length = gcmd.get_int('LENGTH', minval=1)
        speed = gcmd.get_int('SPEED', self.feed_speed, minval=1)

        def callback(response):
            if response.get('code', 0) != 0:
                gcmd.respond_raw(f"{self.error_prefix}{response.get('msg', 'Unknown error')}")

        self.send_request({
            "method": "feed_filament",
            "params": {
                "index": index,
                "length": length,
                "speed": speed
            }
        }, callback)
        self.dwell((length / speed) + 0.1)

    cmd_ACE_RETRACT_help = "Retract filament"
    def cmd_ACE_RETRACT(self, gcmd):
        """Обработчик команды ACE_RETRACT"""
        index = gcmd.get_int('INDEX', minval=0, maxval=3)
        length = gcmd.get_int('LENGTH', minval=1)
        speed = gcmd.get_int('SPEED', self.retract_speed, minval=1)

        def callback(response):
            if response.get('code', 0) != 0:
                gcmd.respond_raw(f"{self.error_prefix}{response.get('msg', 'Unknown error')}")

        self.send_request({
            "method": "unwind_filament",
            "params": {
                "index": index,
                "length": length,
                "speed": speed
            }
        }, callback)
        self.dwell((length / speed) + 0.1)

    cmd_ACE_CHANGE_TOOL_help = "Change tool"
    def cmd_ACE_CHANGE_TOOL(self, gcmd):
        """Обработчик команды ACE_CHANGE_TOOL"""
        tool = gcmd.get_int('TOOL', minval=-1, maxval=3)
        was = self.variables.get('ace_current_index', -1)
        
        if was == tool:
            gcmd.respond_raw(f"Tool already set to {tool}")
            return
        
        if tool != -1 and self._info['slots'][tool]['status'] != 'ready':
            gcmd.respond_raw(f"{self.error_prefix}Slot {tool} is empty, cannot change tool")
            return

        self._park_is_toolchange = True
        self._park_previous_tool = was
        self.variables['ace_current_index'] = tool
        self.gcode.run_script_from_command(f'SAVE_VARIABLE VARIABLE=ace_current_index VALUE={tool}')

        def callback(response):
            if response.get('code', 0) != 0:
                gcmd.respond_raw(f"{self.error_prefix}{response.get('msg', 'Unknown error')}")

        if was != -1:
            self.send_request({
                "method": "unwind_filament",
                "params": {
                    "index": was,
                    "length": self.toolchange_retract_length,
                    "speed": self.retract_speed
                }
            }, callback)
            self.dwell((self.toolchange_retract_length / self.retract_speed) + 0.1)

            if tool != -1:
                self._park_to_toolhead(tool)
        else:
            self._park_to_toolhead(tool)

def load_config(config):
    return ValgAce(config)