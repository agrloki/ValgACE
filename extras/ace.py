import os
import time
import serial
import serial.tools.list_ports
import threading
import logging
import logging.handlers
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
        self.reactor = self.printer.get_reactor()
        self.gcode = self.printer.lookup_object('gcode')
        self._name = config.get_name()
        if self._name.startswith('ace '):
            self._name = self._name[4:]
        self.variables = self.printer.lookup_object('save_variables').allVariables
        self.lock = threading.Lock()  # Lock object
        self.read_buffer = bytearray()
        self.send_time = 0
        self._last_status_request = 0
        
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
        
        # Очереди и потоки
        self._queue = queue.Queue(maxsize=self._max_queue_size)
        self._main_queue = queue.Queue()
        
        # Инициализация
        self._register_handlers()
        self._register_gcode_commands()

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
        logging.info("ACE internal loggign engine initialized")

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
                    logging.info(f"Found ACE device by VID/PID at {port.device}")
                    return port.device
            if any(name in (port.description or '') for name in ACE_IDS['DESCRIPTION']):
                self.logger.info(f"Found ACE device by description at {port.device}")
                logging.info(f"Found ACE device by description at {port.device}")
                return port.device
        self.logger.warning("No ACE device found by auto-detection")
        logging.warning("No ACE device found by auto-detection")
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
        with self.lock:  # Использование lock как объекта Lock()
            try:
                yield
            except Exception as e:
                self.logger.error(f"Serial lock error: {str(e)}", exc_info=True)
                logging.error(f"Serial lock error: {str(e)}", exc_info=True)

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
                        
                    def info_callback(response):
                        res = response['result']
                        self.logger.info(f"Device info: {res.get('model', 'Unknown')} {res.get('firmware', 'Unknown')}")
                        logging.info(f"Device info: {res.get('model', 'Unknown')} {res.get('firmware', 'Unknown')}")
                        self.gcode.respond_info(f"Connected {res.get('model', 'Unknown')} {res.get('firmware', 'Unknown')}")
                        
                    self.send_request({"method": "get_info"}, info_callback)
                    return True
            except SerialException as e:
                self.logger.warning(f"Connection attempt {attempt + 1} failed: {str(e)}")
                logging.warning(f"Connection attempt {attempt + 1} failed: {str(e)}")
                time.sleep(1)
        self.logger.error("Failed to connect to ACE device")
        logging.error("Failed to connect to ACE device")
        return False

    def _writer_loop(self):
        """Цикл записи с использованием time.sleep для фонового потока"""
        while getattr(self, '_connected', False):
            try:
                now = time.time()
                if now - self._last_status_request > 1.0:
                    self._request_status()
                    self._last_status_request = now
                    
                if not self._queue.empty():
                    task = self._queue.get_nowait()
                    if task:
                        request, callback = task
                        self._callback_map[request['id']] = callback
                        
                        if not self._send_request(request):
                            self.logger.warning("Failed to send request, requeuing...")
                            logging.warning("Failed to send request, requeuing...")
                            self._queue.put(task)  # Возвращаем задачу в очередь
                            time.sleep(0.1)
                            continue
                
                time.sleep(0.05)
            except SerialException:
                self.logger.error("Serial write error")
                logging.error("Serial write error")
                if self._connected:
                    self._reconnect()
            except Exception as e:
                self.logger.error(f"Writer loop error: {str(e)}")
                logging.error(f"Writer loop error: {str(e)}")
                time.sleep(0.5)

    def _reader_loop(self):
        """Цикл чтения с использованием time.sleep для фонового потока"""
        incomplete_message_count = 0
        max_incomplete_messages_before_reset = 10
        
        while getattr(self, '_connected', False):
            try:
                bytes_to_read = self._serial.in_waiting or 16
                raw_bytes = self._serial.read(bytes_to_read)
                if not raw_bytes:
                    time.sleep(0.01)
                    continue
                    
                self.read_buffer.extend(raw_bytes)
                
                while True:
                    end_idx = self.read_buffer.find(b'\xfe')
                    if end_idx == -1:
                        break
                        
                    msg = self.read_buffer[:end_idx+1]
                    self.read_buffer = self.read_buffer[end_idx+1:]
                    
                    if len(msg) < 7 or msg[0:2] != bytes([0xFF, 0xAA]):
                        self.logger.debug(f"Invalid message header: {msg}")
                        logging.debug(f"Invalid message header: {msg}")
                        continue
                        
                    payload_len = struct.unpack('<H', msg[2:4])[0]
                    expected_length = 4 + payload_len + 3
                    
                    if len(msg) < expected_length:
                        self.logger.warning(f"Incomplete message received (expected {expected_length}, got {len(msg)})")
                        logging.warning(f"Incomplete message received (expected {expected_length}, got {len(msg)})")
                        incomplete_message_count += 1
                        
                        if incomplete_message_count > max_incomplete_messages_before_reset:
                            self.logger.error("Too many incomplete messages, resetting connection")
                            logging.error("Too many incomplete messages, resetting connection")
                            self._reset_connection()
                            incomplete_message_count = 0
                        continue
                        
                    incomplete_message_count = 0
                    self._process_message(msg)
                    
            except SerialException as e:
                self.logger.error(f"Read error: {str(e)}")
                logging.error(f"Read error: {str(e)}")
                self._reset_connection()
                time.sleep(1.0)
            except Exception as e:
                self.logger.error(f"Reader loop error: {str(e)}")
                logging.error(f"Reader loop error: {str(e)}")
                time.sleep(1.0)

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
                    old_writer.join(timeout=2.0)
                    if old_writer.is_alive():
                        self.logger.warning("Old writer thread still alive after join")
                        logging.warning("Old writer thread still alive after join")
                        
                if old_reader and old_reader.is_alive() and old_reader != threading.current_thread():
                    old_reader.join(timeout=2.0)
                    if old_reader.is_alive():
                        self.logger.warning("Old reader thread still alive after join")
                        logging.warning("Old reader thread still alive after join")
                        
                return True
            return False
        except Exception as e:
            self.logger.error(f"Reconnect error: {str(e)}")
            logging.warning(f"Reconnect error: {str(e)}")
            return False

    def _reset_connection(self):
        """Сброс соединения"""
        self._disconnect()
        time.sleep(1)
        self._connect()

    def _disconnect(self):
        """Безопасное отключение"""
        if not self._connected:
            return
        self._connected = False
        try:
            if hasattr(self, '_serial'):
                self._serial.close()
        except Exception as e:
            self.logger.error(f"Disconnect error: {str(e)}")
            logging.error(f"Disconnect error: {str(e)}")
            
        current_thread = threading.current_thread()
        
        if hasattr(self, '_writer_thread') and self._writer_thread != current_thread:
            try:
                self._writer_thread.join(timeout=2.0)
                if self._writer_thread.is_alive():
                    self.logger.warning("Writer thread still alive after join")
                    logging.warning("Writer thread still alive after join")
            except Exception as e:
                self.logger.error(f"Writer thread join error: {str(e)}")
                logging.warning(f"Writer thread join error: {str(e)}")
                
        if hasattr(self, '_reader_thread') and self._reader_thread != current_thread:
            try:
                self._reader_thread.join(timeout=2.0)
                if self._reader_thread.is_alive():
                    self.logger.warning("Reader thread still alive after join")
                    logging.warning("Reader thread still alive after join")
            except Exception as e:
                self.logger.error(f"Reader thread join error: {str(e)}")
                logging.error(f"Reader thread join error: {str(e)}")
                
        if hasattr(self, 'main_timer'):
            try:
                self.reactor.unregister_timer(self.main_timer)
            except Exception as e:
                self.logger.error(f"Timer unregister error: {str(e)}")
                logging.error(f"Timer unregister error: {str(e)}")

    def _calc_crc(self, buffer: bytes) -> int:
        """Оптимизированный расчет CRC"""
        crc = 0xffff
        for byte in buffer:
            data = byte ^ (crc & 0xff)
            data ^= (data & 0x0f) << 4
            crc = ((data << 8) | (crc >> 8)) ^ (data >> 4) ^ (data << 3)
        return crc & 0xffff

    def _send_request(self, request: Dict[str, Any]) -> bool:
        """Отправка запроса с оптимизациями"""
        start_time = time.time()
        with self._serial_lock():
            try:
                if not self._connected and not self._reconnect():
                    raise SerialException("Device not connected")
                    
                if 'id' not in request:
                    request['id'] = self._get_next_request_id()
                    
                try:
                    payload = json.dumps(request).encode('utf-8')
                except Exception as e:
                    self.logger.error(f"JSON encoding error: {str(e)}")
                    logging.error(f"JSON encoding error: {str(e)}")
                    return False
                    
                crc = self._calc_crc(payload)
                packet = (
                    bytes([0xFF, 0xAA]) +
                    struct.pack('<H', len(payload)) +
                    payload +
                    struct.pack('<H', crc) +
                    bytes([0xFE]))
                
                if not hasattr(self, '_serial') or not self._serial.is_open:
                    if not self._reconnect():
                        return False
                        
                try:
                    self._serial.write(packet)
                    self.send_time = time.time()
                    self.logger.debug(f"Request {request['id']} sent in {(time.time()-start_time)*1000:.1f}ms")
                    logging.debug(f"Request {request['id']} sent in {(time.time()-start_time)*1000:.1f}ms")
                    return True
                except Exception as e:
                    self.logger.error(f"Serial write error during send: {str(e)}")
                    logging.error(f"Serial write error during send: {str(e)}")
                    self._reset_connection()
                    return False
                    
            except SerialException as e:
                self.logger.error(f"Send error: {str(e)}")
                logging.error(f"Send error: {str(e)}")
                self._reset_connection()
                return False
            except Exception as e:
                self.logger.error(f"Unexpected send error: {str(e)}")
                logging.error(f"Unexpected send error: {str(e)}")
                return False

    def _get_next_request_id(self) -> int:
        """Генерация ID запроса с защитой от переполнения"""
        self._request_id += 1
        if self._request_id >= 300000:
            self._request_id = 0
        return self._request_id

    def _process_message(self, msg: bytes):
        """Оптимизированная обработка сообщений"""
        try:
            if len(msg) < 7 or msg[0:2] != bytes([0xFF, 0xAA]):
                self.logger.debug(f"Invalid message header: {msg}")
                logging.debug(f"Invalid message header: {msg}")
                return
                
            payload_len = struct.unpack('<H', msg[2:4])[0]
            expected_length = 4 + payload_len + 3
            
            if len(msg) < expected_length:
                self.logger.warning(f"Incomplete message received (expected {expected_length}, got {len(msg)})")
                logging.warning(f"Incomplete message received (expected {expected_length}, got {len(msg)})")
                return
                
            payload = msg[4:4+payload_len]
            crc = struct.unpack('<H', msg[4+payload_len:4+payload_len+2])[0]
            
            if crc != self._calc_crc(payload):
                self.logger.warning("CRC mismatch")
                logging.warning("CRC mismatch")
                return
                
            try:
                response = json.loads(payload.decode('utf-8'))
                self._handle_response(response)
            except json.JSONDecodeError as je:
                self.logger.error(f"JSON decode error: {str(je)} Data: {msg}")
                logging.error(f"JSON decode error: {str(je)} Data: {msg}")
            except Exception as e:
                self.logger.error(f"Message processing error: {str(e)} Data: {msg}", exc_info=True)
                logging.error(f"Message processing error: {str(e)} Data: {msg}", exc_info=True)
                
        except struct.error as se:
            self.logger.error(f"Struct unpack error: {str(se)} Data: {msg}")
            logging.error(f"Struct unpack error: {str(se)} Data: {msg}")
        except Exception as e:
            self.logger.error(f"General message processing error: {str(e)} Data: {msg}", exc_info=True)
            logging.error(f"General message processing error: {str(e)} Data: {msg}", exc_info=True)

    def _handle_response(self, response: dict):
        """Централизованная обработка ответов"""
        if 'id' in response:
            callback = self._callback_map.pop(response['id'], None)
            if callback:
                try:
                    callback(response)
                except Exception as e:
                    self.logger.error(f"Callback error: {str(e)}")
                    logging.error(f"Callback error: {str(e)}")
                    
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
                            
                    self.dwell(0.7, on_main=True)

    def _complete_parking(self):
       """Завершение процесса парковки"""
       if not self._park_in_progress:
           return
       self.logger.info(f"Parking completed for slot {self._park_index}")
       logging.info(f"Parking completed for slot {self._park_index}")
       try:
           # Остановка feed assist
           self.send_request({
               "method": "stop_feed_assist",
               "params": {"index": self._park_index}
           }, lambda r: None)

           # Выполнение G-code команды в основном потоке
           if self._park_is_toolchange:
               def run_gcode():
                   self.gcode.run_script_from_command(
                       f'_ACE_POST_TOOLCHANGE FROM={self._park_previous_tool} TO={self._park_index}'
                   )
               self._main_queue.put(run_gcode)
       except Exception as e:
           self.logger.error(f"Parking completion error: {str(e)}", exc_info=True)
           logging.error(f"Parking completion error: {str(e)}", exc_info=True)
       finally:
           # Сброс состояния парковки
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
                
        if time.time() - self._last_status_request > (0.2 if self._park_in_progress else 1.0):
            try:
                self.send_request({
                    "id": self._get_next_request_id(),
                    "method": "get_status"
                }, status_callback)
                self._last_status_request = time.time()
            except Exception as e:
                self.logger.error(f"Status request error: {str(e)}", exc_info=True)
                logging.error(f"Status request error: {str(e)}", exc_info=True)

    def _main_eval(self, eventtime):
        """Обработка задач в основном потоке"""
        while not self._main_queue.empty():
            try:
                task = self._main_queue.get_nowait()
                if task:
                    task()
            except Exception as e:
                self.logger.error(f"Main eval error: {str(e)}", exc_info=True)
                logging.error(f"Main eval error: {str(e)}", exc_info=True)
                
        return eventtime + 0.1

    def _handle_ready(self):
        """Обработчик готовности Klipper"""
        if not self._connect():
            self.logger.error("Failed to connect to ACE on startup")
            logging.error("Failed to connect to ACE on startup")

    def _handle_disconnect(self):
        """Обработчик отключения Klipper"""
        self._disconnect()

    def send_request(self, request: Dict[str, Any], callback: Callable):
        """Отправка запроса с контролем очереди"""
        if self._queue.qsize() >= self._max_queue_size:
            self.logger.warning("Request queue overflow, clearing...")
            logging.warning("Request queue overflow, clearing...")
            try:
                while not self._queue.empty():
                    _, cb = self._queue.get_nowait()
                    if cb:
                        try:
                            cb({'error': 'Queue overflow'})
                        except Exception as e:
                            self.logger.error(f"Queue overflow callback error: {str(e)}", exc_info=True)
                            logging.error(f"Queue overflow callback error: {str(e)}", exc_info=True)
            except Exception as e:
                self.logger.error(f"Queue clear error: {str(e)}", exc_info=True)
                logging.error(f"Queue clear error: {str(e)}", exc_info=True)
                
        request['id'] = self._get_next_request_id()
        self._queue.put((request, callback))

    def dwell(self, delay: float = 1.0, on_main: bool = False):
       """Пауза с возможностью выполнения в основном потоке"""
       toolhead = self.printer.lookup_object('toolhead')

       def main_callback():
           try:
               toolhead.dwell(delay)
           except Exception as e:
               self.logger.error(f"Dwell error: {str(e)}", exc_info=True)
               logging.error(f"Dwell error: {str(e)}", exc_info=True)

       if on_main:
           # Перенос выполнения в основной поток через _main_queue
           self._main_queue.put(main_callback)
       else:
           # Выполнение напрямую (только если мы уверены, что это основной поток)
           if threading.current_thread() != self.reactor.thread:
               raise RuntimeError("Dwell must be called in the main thread or with on_main=True")
           main_callback()
        
    # ==================== G-CODE COMMANDS ====================
    cmd_ACE_STATUS_help = "Get current device status"
    def cmd_ACE_STATUS(self, gcmd):
        """Обработчик команды ACE_STATUS"""
        try:
            status = json.dumps(self._info, indent=2)
            gcmd.respond_info(f"ACE Status:\n{status}")
        except Exception as e:
            self.logger.error(f"Status command error: {str(e)}", exc_info=True)
            logging.error(f"Status command error: {str(e)}", exc_info=True)
            gcmd.respond_raw("Error retrieving status")

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
                except json.JSONDecodeError:
                    gcmd.respond_raw("Invalid PARAMS format")
                    return
                    
            self.send_request(request, callback)
            if not response_event.wait(self._response_timeout):
                gcmd.respond_raw("Timeout waiting for response")
                return
                
            response = response_data[0]
            if response is None:
                gcmd.respond_raw("No response received")
                return
                
            if method in ["get_info", "get_status"] and 'result' in response:
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
            self.logger.error(f"Debug command error: {str(e)}", exc_info=True)
            logging.error(f"Debug command error: {str(e)}", exc_info=True)
            gcmd.respond_raw(f"Error: {str(e)}")

    cmd_ACE_FILAMENT_INFO_help = 'ACE_FILAMENT_INFO INDEX='
    def cmd_ACE_FILAMENT_INFO(self, gcmd):
        """Handler for ACE_FILAMENT_INFO command"""
        index = gcmd.get_int('INDEX', minval=0, maxval=3)
        try:
            def callback(response):
                if 'result' in response:
                    slot_info = response['result']
                    self.gcode.respond_info(str(slot_info))
                else:
                    self.gcode.respond_info('Error: No result in response')
                    
            self.send_request(
                request={"method": "get_filament_info", "params": {"index": index}},
                callback=callback
            )
        except Exception as e:
            self.logger.error(f"Filament info error: {str(e)}", exc_info=True)
            logging.error(f"Filament info error: {str(e)}", exc_info=True)
            self.gcode.respond_info('Error: ' + str(e))

    cmd_ACE_START_DRYING_help = "Start filament drying"
    def cmd_ACE_START_DRYING(self, gcmd):
        """Обработчик команды ACE_START_DRYING"""
        try:
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
        except Exception as e:
            self.logger.error(f"Start drying error: {str(e)}", exc_info=True)
            logging.error(f"Start drying error: {str(e)}", exc_info=True)
            gcmd.respond_raw(f"Error: {str(e)}")

    cmd_ACE_STOP_DRYING_help = "Stop filament drying"
    def cmd_ACE_STOP_DRYING(self, gcmd):
        """Обработчик команды ACE_STOP_DRYING"""
        try:
            def callback(response):
                if response.get('code', 0) != 0:
                    gcmd.respond_raw(f"ACE Error: {response.get('msg', 'Unknown error')}")
                else:
                    gcmd.respond_info("Drying stopped")
                    
            self.send_request({"method": "drying_stop"}, callback)
        except Exception as e:
            self.logger.error(f"Stop drying error: {str(e)}", exc_info=True)
            logging.error(f"Stop drying error: {str(e)}", exc_info=True)
            gcmd.respond_raw(f"Error: {str(e)}")

    cmd_ACE_ENABLE_FEED_ASSIST_help = "Enable feed assist"
    def cmd_ACE_ENABLE_FEED_ASSIST(self, gcmd):
        """Обработчик команды ACE_ENABLE_FEED_ASSIST"""
        try:
            index = gcmd.get_int('INDEX', minval=0, maxval=3)
            
            def callback(response):
                if response.get('code', 0) != 0:
                    gcmd.respond_raw(f"ACE Error: {response.get('msg', 'Unknown error')}")
                else:
                    self._feed_assist_index = index
                    gcmd.respond_info(f"Feed assist enabled for slot {index}")
                    self.dwell(0.3, on_main=True)
                    
            self.send_request({
                "method": "start_feed_assist",
                "params": {"index": index}
            }, callback)
        except Exception as e:
            self.logger.error(f"Enable feed assist error: {str(e)}", exc_info=True)
            logging.error(f"Enable feed assist error: {str(e)}", exc_info=True)
            gcmd.respond_raw(f"Error: {str(e)}")

    cmd_ACE_DISABLE_FEED_ASSIST_help = "Disable feed assist"
    def cmd_ACE_DISABLE_FEED_ASSIST(self, gcmd):
        """Обработчик команды ACE_DISABLE_FEED_ASSIST"""
        try:
            index = gcmd.get_int('INDEX', self._feed_assist_index, minval=0, maxval=3)
            
            def callback(response):
                if response.get('code', 0) != 0:
                    gcmd.respond_raw(f"ACE Error: {response.get('msg', 'Unknown error')}")
                else:
                    self._feed_assist_index = -1
                    gcmd.respond_info(f"Feed assist disabled for slot {index}")
                    self.dwell(0.3, on_main=True)
                    
            self.send_request({
                "method": "stop_feed_assist",
                "params": {"index": index}
            }, callback)
        except Exception as e:
            self.logger.error(f"Disable feed assist error: {str(e)}", exc_info=True)
            logging.error(f"Disable feed assist error: {str(e)}", exc_info=True)
            gcmd.respond_raw(f"Error: {str(e)}")

    def _park_to_toolhead(self, index: int):
        """Внутренний метод парковки филамента"""
        try:
            def callback(response):
                if response.get('code', 0) != 0:
                    raise ValueError(f"ACE Error: {response.get('msg', 'Unknown error')}")
                    
                self._assist_hit_count = 0
                self._last_assist_count = response.get('result', {}).get('feed_assist_count', 0)
                self._park_in_progress = True
                self._park_index = index
                self.dwell(0.3, on_main=True)
                
            self.send_request({
                "method": "start_feed_assist",
                "params": {"index": index}
            }, callback)
        except Exception as e:
            self.logger.error(f"Park to toolhead error: {str(e)}", exc_info=True)
            logging.error(f"Park to toolhead error: {str(e)}", exc_info=True)

    cmd_ACE_PARK_TO_TOOLHEAD_help = "Park filament to toolhead"
    def cmd_ACE_PARK_TO_TOOLHEAD(self, gcmd):
       """Обработчик команды ACE_PARK_TO_TOOLHEAD"""
       try:
           if self._park_in_progress:
               gcmd.respond_raw("Already parking to toolhead")
               return
           index = gcmd.get_int('INDEX', minval=0, maxval=3)
           if self._info['slots'][index]['status'] != 'ready':
               def run_gcode():
                   self.gcode.run_script_from_command(f"_ACE_ON_EMPTY_ERROR INDEX={index}")
               self._main_queue.put(run_gcode)
               return
           self._park_to_toolhead(index)
       except Exception as e:
           self.logger.error(f"Park to toolhead command error: {str(e)}", exc_info=True)
           logging.error(f"Park to toolhead command error: {str(e)}", exc_info=True)
           gcmd.respond_raw(f"Error: {str(e)}")

    cmd_ACE_FEED_help = "Feed filament"
    def cmd_ACE_FEED(self, gcmd):
        """Обработчик команды ACE_FEED"""
        try:
            index = gcmd.get_int('INDEX', minval=0, maxval=3)
            length = gcmd.get_int('LENGTH', minval=1)
            speed = gcmd.get_int('SPEED', self.feed_speed, minval=1)
            
            def callback(response):
                if response.get('code', 0) != 0:
                    gcmd.respond_raw(f"ACE Error: {response.get('msg', 'Unknown error')}")
                    
            self.send_request({
                "method": "feed_filament",
                "params": {
                    "index": index,
                    "length": length,
                    "speed": speed
                }
            }, callback)
            self.dwell((length / speed) + 0.1, on_main=True)
        except Exception as e:
            self.logger.error(f"Feed command error: {str(e)}", exc_info=True)
            logging.error(f"Feed command error: {str(e)}", exc_info=True)
            gcmd.respond_raw(f"Error: {str(e)}")

    cmd_ACE_RETRACT_help = "Retract filament"
    def cmd_ACE_RETRACT(self, gcmd):
        """Обработчик команды ACE_RETRACT"""
        try:
            index = gcmd.get_int('INDEX', minval=0, maxval=3)
            length = gcmd.get_int('LENGTH', minval=1)
            speed = gcmd.get_int('SPEED', self.retract_speed, minval=1)
            
            def callback(response):
                if response.get('code', 0) != 0:
                    gcmd.respond_raw(f"ACE Error: {response.get('msg', 'Unknown error')}")
                    
            self.send_request({
                "method": "unwind_filament",
                "params": {
                    "index": index,
                    "length": length,
                    "speed": speed
                }
            }, callback)
            self.dwell((length / speed) + 0.1, on_main=True)
        except Exception as e:
            self.logger.error(f"Retract command error: {str(e)}", exc_info=True)
            logging.error(f"Retract command error: {str(e)}", exc_info=True)
            gcmd.respond_raw(f"Error: {str(e)}")

    cmd_ACE_CHANGE_TOOL_help = "Change tool"
    def cmd_ACE_CHANGE_TOOL(self, gcmd):
      """Обработчик команды ACE_CHANGE_TOOL"""
      try:
          tool = gcmd.get_int('TOOL', minval=-1, maxval=3)
          was = self.variables.get('ace_current_index', -1)
          if was == tool:
              gcmd.respond_info(f"Tool already set to {tool}")
              return
          if tool != -1 and self._info['slots'][tool]['status'] != 'ready':
              def run_gcode():
                  self.gcode.run_script_from_command(f"_ACE_ON_EMPTY_ERROR INDEX={tool}")
              self._main_queue.put(run_gcode)
              return
          def pre_toolchange():
              self.gcode.run_script_from_command(f"_ACE_PRE_TOOLCHANGE FROM={was} TO={tool}")
          self._main_queue.put(pre_toolchange)
          self._park_is_toolchange = True
          self._park_previous_tool = was
          self.variables['ace_current_index'] = tool
          def save_variable():
              self.gcode.run_script_from_command(f'SAVE_VARIABLE VARIABLE=ace_current_index VALUE={tool}')
          self._main_queue.put(save_variable)
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
              self.dwell((self.toolchange_retract_length / self.retract_speed) + 0.1, on_main=True)
              while self._info['status'] != 'ready':
                  self.dwell(1.0, on_main=True)
              self.dwell(0.25, on_main=True)
              if tool != -1:
                  self.gcode.run_script_from_command(f'ACE_PARK_TO_TOOLHEAD INDEX={tool}')
              else:
                  def post_toolchange():
                      self.gcode.run_script_from_command(f'_ACE_POST_TOOLCHANGE FROM={was} TO={tool}')
                  self._main_queue.put(post_toolchange)
          else:
              self._park_to_toolhead(tool)
      except Exception as e:
          self.logger.error(f"Change tool error: {str(e)}", exc_info=True)
          logging.error(f"Change tool error: {str(e)}", exc_info=True)
          gcmd.respond_raw(f"Error: {str(e)}")
        
def load_config(config):
    return ValgAce(config)