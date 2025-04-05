import os
import time
import serial
import serial.tools.list_ports
import logging
import logging.handlers
import json
import struct
from typing import Optional, Dict, Any, Callable
from serial import SerialException
from contextlib import contextmanager

class ValgAce:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.gcode = self.printer.lookup_object('gcode')
        self._name = config.get_name()
        
        if self._name.startswith('ace '):
            self._name = self._name[4:]
        
        self.variables = self.printer.lookup_object('save_variables').allVariables
        self.mutex = self.reactor.mutex()
        self.read_buffer = bytearray()
        self.send_time = 0
        self._last_status_request = 0

        # Инициализация логирования
        self._init_logging(config)
        
        # Параметры таймаутов
        self._response_timeout = config.getfloat('response_timeout', 2.0)
        self._read_timeout = config.getfloat('read_timeout', 0.1)
        self._write_timeout = config.getfloat('write_timeout', 0.5)

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
        
        # Инициализация
        self._register_handlers()
        self._register_gcode_commands()

        # Подключение устройства
        self.reactor.register_callback(self._handle_ready)

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
            print(f"Error creating log directory: {e}")
            log_dir = '/tmp'

        log_file = os.path.join(log_dir, 'ace.log')
        
        log_format = '%(asctime)s [%(levelname)s] %(message)s'
        date_format = '%Y-%m-%d %H:%M:%S'
        
        file_handler = logging.handlers.RotatingFileHandler(
            log_file,
            maxBytes=max_log_size,
            backupCount=log_backup_count
        )
        file_handler.setFormatter(logging.Formatter(log_format, date_format))
        
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(logging.Formatter(log_format, date_format))
        
        logger = logging.getLogger()
        logger.setLevel(getattr(logging, log_level, logging.INFO))
        logger.addHandler(file_handler)
        logger.addHandler(console_handler)
        
        self.logger = logging.getLogger('ace')
        self.logger.info("ACE logging initialized")

    def _find_ace_device(self) -> Optional[str]:
        """Поиск устройства ACE по VID/PID или описанию"""
        ACE_IDS = {
            'VID:PID': [(0x0483, 0x5740)],
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

    @contextmanager
    def _serial_lock(self):
        """Потокобезопасная блокировка для работы с портом"""
        with self.mutex:
            yield

    def _connect(self) -> bool:
        """Попытка подключения к устройству"""
        if self._connected:
            return True
            
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
                    
                    # Запускаем обработчик чтения
                    self.reactor.register_async_callback(self._reader_loop)
                    
                    # Запрашиваем информацию об устройстве
                    def info_callback(response):
                        res = response['result']
                        self.logger.info(f"Device info: {res.get('model', 'Unknown')} {res.get('firmware', 'Unknown')}")
                        self.gcode.respond_info(f"Connected {res.get('model', 'Unknown')} {res.get('firmware', 'Unknown')}")
                    
                    self.send_request({"method": "get_info"}, info_callback)
                    
                    return True
                    
            except SerialException as e:
                self.logger.warning(f"Connection attempt {attempt + 1} failed: {str(e)}")
                self.reactor.pause(self.reactor.monotonic() + 1.0)
        
        self.logger.error("Failed to connect to ACE device")
        return False

    def _reader_loop(self, eventtime):
        """Цикл чтения данных с устройства"""
        try:
            if len(self.read_buffer) > 4096:
                self.read_buffer = bytearray()

            start_time = self.reactor.monotonic()
            while self.reactor.monotonic() - start_time < self._read_timeout:
                bytes_to_read = self._serial.in_waiting or 1
                raw_bytes = self._serial.read(bytes_to_read)
                
                if not raw_bytes:
                    break
                    
                self.read_buffer.extend(raw_bytes)
                
                end_idx = self.read_buffer.find(b'\xfe')
                if end_idx >= 0:
                    msg = self.read_buffer[:end_idx+1]
                    self.read_buffer = self.read_buffer[end_idx+1:]
                    self._process_message(msg)
                    
            return eventtime + 0.01
            
        except SerialException as e:
            self.logger.error(f"Read error: {str(e)}")
            self._reset_connection()
            return eventtime + 1.0

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
                self.logger.warning("CRC mismatch")
                return
                
            response = json.loads(payload.decode('utf-8'))
            self._handle_response(response)
            
        except Exception as e:
            self.logger.error(f"Message processing error: {str(e)}")

    def _handle_response(self, response: dict):
        """Обработка ответов от устройства"""
        if 'id' in response:
            callback = self._callback_map.pop(response['id'], None)
            if callback:
                try:
                    callback(response)
                except Exception as e:
                    self.logger.error(f"Callback error: {str(e)}")
        
        if 'result' in response and isinstance(response['result'], dict):
            result = response['result']
            self._info.update(result)
            
            # Обработка парковки филамента
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
                    
                    self.reactor.pause(self.reactor.monotonic() + 0.7)

    def _complete_parking(self):
        """Завершение процесса парковки"""
        if not self._park_in_progress:
            return
            
        self.logger.info(f"Parking completed for slot {self._park_index}")
        
        # Отправляем команду остановки
        self.send_request({
            "method": "stop_feed_assist",
            "params": {"index": self._park_index}
        }, lambda r: None)
        
        # Выполняем пост-обработку
        if self._park_is_toolchange:
            self.gcode.run_script_from_command(
                f'_ACE_POST_TOOLCHANGE FROM={self._park_previous_tool} TO={self._park_index}'
            )
        
        # Сбрасываем состояние
        self._park_in_progress = False
        self._park_is_toolchange = False
        self._park_previous_tool = -1
        self._park_index = -1
        
        if self.disable_assist_after_toolchange:
            self._feed_assist_index = -1

    def _request_status(self):
        """Запрос статуса устройства"""
        def status_callback(response):
            if 'result' in response:
                self._info.update(response['result'])
        
        interval = 0.2 if self._park_in_progress else 1.0
        if self.reactor.monotonic() - self._last_status_request > interval:
            self.send_request({
                "id": self._get_next_request_id(),
                "method": "get_status"
            }, status_callback)
            self._last_status_request = self.reactor.monotonic()

    def _handle_ready(self):
        """Обработчик готовности Klipper"""
        if not self._connect():
            self.logger.error("Failed to connect to ACE on startup")
            return

    def _handle_disconnect(self):
        """Обработчик отключения Klipper"""
        self._disconnect()

    def send_request(self, request: Dict[str, Any], callback: Callable):
        """Отправка запроса к устройству"""
        with self._serial_lock():
            try:
                if not self._connected and not self._reconnect():
                    raise SerialException("Device not connected")

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
                
                if not hasattr(self, '_serial') or not self._serial.is_open:
                    if not self._reconnect():
                        return
                
                self._serial.write(packet)
                self.send_time = self.reactor.monotonic()
                self._callback_map[request['id']] = callback
                self.logger.debug(f"Request {request['id']} sent")
                
            except SerialException as e:
                self.logger.error(f"Send error: {str(e)}")
                self._reset_connection()
            except Exception as e:
                self.logger.error(f"Unexpected send error: {str(e)}")

    def _get_next_request_id(self) -> int:
        """Генерация ID запроса"""
        self._request_id += 1
        if self._request_id >= 300000:
            self._request_id = 0
        return self._request_id

    def _calc_crc(self, buffer: bytes) -> int:
        """Расчет CRC для сообщения"""
        crc = 0xffff
        for byte in buffer:
            data = byte ^ (crc & 0xff)
            data ^= (data & 0x0f) << 4
            crc = ((data << 8) | (crc >> 8)) ^ (data >> 4) ^ (data << 3)
        return crc & 0xffff

    def _reconnect(self) -> bool:
        """Переподключение к устройству"""
        self._disconnect()
        self.reactor.pause(self.reactor.monotonic() + 1.0)
        return self._connect()

    def _reset_connection(self):
        """Сброс соединения"""
        self._disconnect()
        self.reactor.pause(self.reactor.monotonic() + 1.0)
        self._connect()

    def _disconnect(self):
        """Отключение от устройства"""
        if not self._connected:
            return
        
        self._connected = False
        
        try:
            if hasattr(self, '_serial'):
                self._serial.close()
        except:
            pass

    # ==================== G-CODE COMMANDS ====================

    cmd_ACE_STATUS_help = "Get current device status"
    def cmd_ACE_STATUS(self, gcmd):
        """Обработчик команды ACE_STATUS"""
        status = json.dumps(self._info, indent=2)
        gcmd.respond_info(f"ACE Status:\n{status}")

    cmd_ACE_DEBUG_help = "Debug ACE connection"
    def cmd_ACE_DEBUG(self, gcmd):
        """Обработчик команды ACE_DEBUG"""
        method = gcmd.get('METHOD')
        params = gcmd.get('PARAMS', '{}')
        
        response_event = self.reactor.event()
        response_data = [None]

        def callback(response):
            response_data[0] = response
            response_event.set()

        try:
            request = {"method": method}
            if params.strip():
                try:
                    request["params"] = json.loads(params)
                except json.JSONDecodeError:
                    gcmd.respond_error("Invalid PARAMS format")
                    return

            self.send_request(request, callback)
            if not response_event.wait(self._response_timeout):
                gcmd.respond_error("Timeout waiting for response")
                return

            response = response_data[0]
            if response is None:
                gcmd.respond_error("No response received")
                return

            gcmd.respond_info(json.dumps(response, indent=2))

        except Exception as e:
            gcmd.respond_error(f"Error: {str(e)}")

    cmd_ACE_FILAMENT_INFO_help = 'ACE_FILAMENT_INFO INDEX='
    def cmd_ACE_FILAMENT_INFO(self, gcmd):
        """Handler for ACE_FILAMENT_INFO command"""
        index = gcmd.get_int('INDEX', minval=0, maxval=3)
        
        if not self._connected:
            gcmd.respond_error("ACE device not connected")
            return

        def callback(response):
            if 'result' in response:
                slot_info = response['result']
                gcmd.respond_info(str(slot_info))
            else:
                gcmd.respond_error('Error: No result in response')

        self.send_request(
            request={"method": "get_filament_info", "params": {"index": index}},
            callback=callback
        )

    cmd_ACE_START_DRYING_help = "Start filament drying"
    def cmd_ACE_START_DRYING(self, gcmd):
        """Обработчик команды ACE_START_DRYING"""
        if not self._connected:
            gcmd.respond_error("ACE device not connected")
            return

        temperature = gcmd.get_int('TEMP', minval=20, maxval=self.max_dryer_temperature)
        duration = gcmd.get_int('DURATION', 240, minval=1)

        def callback(response):
            if response.get('code', 0) != 0:
                gcmd.respond_error(f"ACE Error: {response.get('msg', 'Unknown error')}")
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

    cmd_ACE_STOP_DRYING_help = "Stop filament drying"
    def cmd_ACE_STOP_DRYING(self, gcmd):
        """Обработчик команды ACE_STOP_DRYING"""
        if not self._connected:
            gcmd.respond_error("ACE device not connected")
            return

        def callback(response):
            if response.get('code', 0) != 0:
                gcmd.respond_error(f"ACE Error: {response.get('msg', 'Unknown error')}")
            else:
                gcmd.respond_info("Drying stopped")

        self.send_request({"method": "drying_stop"}, callback)

    cmd_ACE_ENABLE_FEED_ASSIST_help = "Enable feed assist"
    def cmd_ACE_ENABLE_FEED_ASSIST(self, gcmd):
        """Обработчик команды ACE_ENABLE_FEED_ASSIST"""
        if not self._connected:
            gcmd.respond_error("ACE device not connected")
            return

        index = gcmd.get_int('INDEX', minval=0, maxval=3)

        def callback(response):
            if response.get('code', 0) != 0:
                gcmd.respond_error(f"ACE Error: {response.get('msg', 'Unknown error')}")
            else:
                self._feed_assist_index = index
                gcmd.respond_info(f"Feed assist enabled for slot {index}")

        self.send_request({
            "method": "start_feed_assist",
            "params": {"index": index}
        }, callback)

    cmd_ACE_DISABLE_FEED_ASSIST_help = "Disable feed assist"
    def cmd_ACE_DISABLE_FEED_ASSIST(self, gcmd):
        """Обработчик команды ACE_DISABLE_FEED_ASSIST"""
        if not self._connected:
            gcmd.respond_error("ACE device not connected")
            return

        index = gcmd.get_int('INDEX', self._feed_assist_index, minval=0, maxval=3)

        def callback(response):
            if response.get('code', 0) != 0:
                gcmd.respond_error(f"ACE Error: {response.get('msg', 'Unknown error')}")
            else:
                self._feed_assist_index = -1
                gcmd.respond_info(f"Feed assist disabled for slot {index}")

        self.send_request({
            "method": "stop_feed_assist",
            "params": {"index": index}
        }, callback)

    def _park_to_toolhead(self, index: int):
        """Внутренний метод парковки филамента"""
        if not self._connected:
            return False

        def callback(response):
            if response.get('code', 0) != 0:
                self.logger.error(f"Failed to park to toolhead: {response.get('msg', 'Unknown error')}")
                return False
            
            self._assist_hit_count = 0
            self._last_assist_count = response.get('result', {}).get('feed_assist_count', 0)
            self._park_in_progress = True
            self._park_index = index
            return True

        return self.send_request({
            "method": "start_feed_assist",
            "params": {"index": index}
        }, callback)

    cmd_ACE_PARK_TO_TOOLHEAD_help = "Park filament to toolhead"
    def cmd_ACE_PARK_TO_TOOLHEAD(self, gcmd):
        """Обработчик команды ACE_PARK_TO_TOOLHEAD"""
        if not self._connected:
            gcmd.respond_error("ACE device not connected")
            return

        if self._park_in_progress:
            gcmd.respond_error("Already parking to toolhead")
            return

        index = gcmd.get_int('INDEX', minval=0, maxval=3)
        
        if self._info['slots'][index]['status'] != 'ready':
            gcmd.respond_error(f"Slot {index} is empty, cannot park")
            return

        if self._park_to_toolhead(index):
            gcmd.respond_info(f"Parking to toolhead initiated for slot {index}")

    cmd_ACE_FEED_help = "Feed filament"
    def cmd_ACE_FEED(self, gcmd):
        """Обработчик команды ACE_FEED"""
        if not self._connected:
            gcmd.respond_error("ACE device not connected")
            return

        if self._park_in_progress:
            gcmd.respond_error("Cannot feed while parking in progress")
            return

        index = gcmd.get_int('INDEX', minval=0, maxval=3)
        length = gcmd.get_int('LENGTH', minval=1)
        speed = gcmd.get_int('SPEED', self.feed_speed, minval=1)

        def callback(response):
            if response.get('code', 0) != 0:
                gcmd.respond_error(f"ACE Error: {response.get('msg', 'Unknown error')}")

        self.send_request({
            "method": "feed_filament",
            "params": {
                "index": index,
                "length": length,
                "speed": speed
            }
        }, callback)
        gcmd.respond_info(f"Feeding {length}mm from slot {index} at {speed}mm/s")

    cmd_ACE_RETRACT_help = "Retract filament"
    def cmd_ACE_RETRACT(self, gcmd):
        """Обработчик команды ACE_RETRACT"""
        if not self._connected:
            gcmd.respond_error("ACE device not connected")
            return

        if self._park_in_progress:
            gcmd.respond_error("Cannot retract while parking in progress")
            return

        index = gcmd.get_int('INDEX', minval=0, maxval=3)
        length = gcmd.get_int('LENGTH', minval=1)
        speed = gcmd.get_int('SPEED', self.retract_speed, minval=1)

        def callback(response):
            if response.get('code', 0) != 0:
                gcmd.respond_error(f"ACE Error: {response.get('msg', 'Unknown error')}")

        self.send_request({
            "method": "unwind_filament",
            "params": {
                "index": index,
                "length": length,
                "speed": speed
            }
        }, callback)
        gcmd.respond_info(f"Retracting {length}mm from slot {index} at {speed}mm/s")

    cmd_ACE_CHANGE_TOOL_help = "Change tool"
    def cmd_ACE_CHANGE_TOOL(self, gcmd):
        """Обработчик команды ACE_CHANGE_TOOL"""
        if not self._connected:
            gcmd.respond_error("ACE device not connected")
            return

        tool = gcmd.get_int('TOOL', minval=-1, maxval=3)
        was = self.variables.get('ace_current_index', -1)
        
        if was == tool:
            gcmd.respond_info(f"Tool already set to {tool}")
            return
        
        if tool != -1 and self._info['slots'][tool]['status'] != 'ready':
            gcmd.respond_error(f"Slot {tool} is empty, cannot change tool")
            return

        gcmd.respond_info(f"Starting tool change from {was} to {tool}")
        self._park_is_toolchange = True
        self._park_previous_tool = was
        self.variables['ace_current_index'] = tool
        self.gcode.run_script_from_command(f'SAVE_VARIABLE VARIABLE=ace_current_index VALUE={tool}')

        def callback(response):
            if response.get('code', 0) != 0:
                gcmd.respond_error(f"ACE Error: {response.get('msg', 'Unknown error')}")

        if was != -1:
            self.send_request({
                "method": "unwind_filament",
                "params": {
                    "index": was,
                    "length": self.toolchange_retract_length,
                    "speed": self.retract_speed
                }
            }, callback)
            gcmd.respond_info(f"Retracting old tool {was}")

            if tool != -1:
                gcmd.respond_info(f"Loading new tool {tool}")
                self.cmd_ACE_PARK_TO_TOOLHEAD(gcmd.create_gcode_command(
                    "ACE_PARK_TO_TOOLHEAD", "ACE_PARK_TO_TOOLHEAD", {"INDEX": str(tool)}))
        else:
            gcmd.respond_info(f"Loading new tool {tool}")
            self._park_to_toolhead(tool)

def load_config(config):
    return ValgAce(config)