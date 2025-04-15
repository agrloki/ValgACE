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
        
        # Потокобезопасные структуры
        self._serial_lock = threading.RLock()
        self._data_lock = threading.RLock()
        self._callback_lock = threading.RLock()
        
        self.read_buffer = bytearray()
        self.send_time = 0
        self._last_status_request = 0
        self.reader_timer = None
        self.writer_timer = None
        self._connection_attempts = 0
        self.connection_timer = self.reactor.register_timer(self._connection_handler, self.reactor.NOW)
        
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
        
        # Состояние устройства (защищено lock)
        self._info = self._get_default_info()
        self._callback_map = {}
        self._request_id = 0
        self._connected = False
        self._connection_attempts = 0
        self._max_connection_attempts = 5
        
        # Параметры работы (защищено lock)
        self._feed_assist_index = -1
        self._last_assist_count = 0
        self._assist_hit_count = 0
        self._park_in_progress = False
        self._park_is_toolchange = False
        self._park_previous_tool = -1
        self._park_index = -1
        self._retract_done = False
        self._retract_start_time = 0
        
        # Очереди (потокобезопасные по умолчанию)
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
        logging.info("ACE internal logging engine initialized")

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
    def _serial_operation(self):
        """Потокобезопасная блокировка для работы с портом"""
        with self._serial_lock:
            try:
                yield
            except Exception as e:
                self.logger.error(f"Serial operation error: {str(e)}", exc_info=True)
                logging.error(f"Serial operation error: {str(e)}", exc_info=True)

    def _connection_handler(self, eventtime=None):
        """Унифицированный обработчик подключения"""
        with self._serial_lock:
            # Если уже подключены, прекращаем попытки
            if self._get_connected_state():
                return self.reactor.NEVER

            try:
                # Проверяем максимальное количество попыток
                if self._connection_attempts >= self._max_connection_attempts:
                    self.logger.error("Max connection attempts reached")
                    logging.error("Max connection attempts reached")
                    return eventtime + 5.0  # Повторная проверка через 5 секунд

                self._connection_attempts += 1
                self.logger.info(f"Attempting connection ({self._connection_attempts}/{self._max_connection_attempts})")

                # Попытка открыть последовательный порт
                self._serial = serial.Serial(
                    port=self.serial_name,
                    baudrate=self.baud,
                    timeout=self._read_timeout,
                    write_timeout=self._write_timeout
                )

                if self._serial.is_open:
                    with self._data_lock:
                        self._connected = True
                        self._info['status'] = 'ready'

                    self.logger.info(f"Connected to ACE at {self.serial_name}")
                    logging.info(f"Connected to ACE at {self.serial_name}")

                    # Запуск необходимых компонентов
                    self._start_writer()
                    self._start_reader()

                    if not hasattr(self, 'main_timer'):
                        self.main_timer = self.reactor.register_timer(
                            self._main_eval, self.reactor.NOW)

                    # Запрос информации о устройстве
                    def info_callback(response):
                        res = response['result']
                        self.logger.info(f"Device info: {res.get('model', 'Unknown')} {res.get('firmware', 'Unknown')}")
                        self.gcode.respond_info(f"Connected {res.get('model', 'Unknown')} {res.get('firmware', 'Unknown')}")

                    self.send_request({"method": "get_info"}, info_callback)

                    # Сброс счетчика попыток после успешного подключения
                    self._connection_attempts = 0

                    return self.reactor.NEVER  # Прекращаем попытки после успешного подключения

            except SerialException as e:
                self.logger.warning(f"Connection attempt failed: {str(e)}")
                logging.warning(f"Connection attempt failed: {str(e)}")

            # Если подключение не удалось, планируем следующую попытку
            return eventtime + 1.0  # Повторить через 1 секунду

    def _start_writer(self):
        """Запуск цикла записи через реактор"""
        self.writer_timer = self.reactor.register_timer(self._writer_eval, self.reactor.NOW)

    def _writer_eval(self, eventtime):
       """Основная логика записи через реактор"""
       try:
           if not self._get_connected_state():
               return self.reactor.NEVER  # Прекращаем работу если отключены
           
           now = self.reactor.monotonic()
           if now - self._get_last_status_request() > 1.0:
               self._request_status()
               self._set_last_status_request(now)
           
           if not self._queue.empty():
               task = self._queue.get_nowait()
               if task:
                   request, callback = task
                   self._add_callback(request['id'], callback)
                   if not self._send_request(request):
                       self.logger.warning("Failed to send request, requeuing...")
                       logging.warning("Failed to send request, requeuing...")
                       self._queue.put(task)  # Возвращаем задачу в очередь
                       return eventtime + 0.1  # Повторить быстрее для повторной отправки
       
       except SerialException:
           self.logger.error("Serial write error")
           logging.error("Serial write error")
           if self._get_connected_state():
               self._reconnect()
           return eventtime + 1.0  # Повторить через 1 секунду
       
       except Exception as e:
           self.logger.error(f"Writer loop error: {str(e)}", exc_info=True)
           logging.error(f"Writer loop error: {str(e)}", exc_info=True)
           return eventtime + 0.5  # Повторить через 0.5 секунды
       
       # Запланировать следующую проверку
       return (eventtime + 0.05)

    def _start_reader(self):
        """Запуск цикла чтения через реактор"""
        self.reader_timer = self.reactor.register_timer(self._reader_eval, self.reactor.NOW)
    
    def _reader_eval(self, eventtime):
        """Основная логика чтения через реактор"""
        try:
            if not self._get_connected_state():
                return self.reactor.NEVER  # Прекращаем работу если отключены
    
            bytes_to_read = self._serial.in_waiting or 16
            if bytes_to_read > 0:
                raw_bytes = self._serial.read(bytes_to_read)
                if raw_bytes:
                    with self._serial_lock:
                        self.read_buffer.extend(raw_bytes)

            while True:
                with self._serial_lock:
                    end_idx = self.read_buffer.find(b'\xfe')
                    if end_idx == -1:
                        break
                    msg = self.read_buffer[:end_idx+1]
                    self.read_buffer = self.read_buffer[end_idx+1:]

                if len(msg) < 7 or msg[0:2] != bytes([0xFF, 0xAA]):
                    self.logger.debug(f"Invalid message header: {msg}")
                    logging.debug(f"Invalid message header: {msg}")
                    continue
                try: 
                    payload_len = struct.unpack('<H', msg[2:4])[0]
                except struct.error as se:
                    self.logger.error(f"Struct unpack error: {str(se)} Data: {msg}")
                    logging.error(f"Struct unpack error: {str(se)} Data: {msg}")
                    return    
                expected_length = 4 + payload_len + 3
                if len(msg) < expected_length:
                    self.logger.warning(f"Incomplete message received (expected {expected_length}, got {len(msg)})")
                    logging.warning(f"Incomplete message received (expected {expected_length}, got {len(msg)})")
                    continue
                 
                self._process_message(msg)

        except SerialException as e:
            self.logger.error(f"Read error: {str(e)}")
            logging.error(f"Read error: {str(e)}")
            self._reset_connection()
            return self.reactor.NEVER  # Прекращаем работу при ошибке

        except Exception as e:
            self.logger.error(f"Reader loop error: {str(e)}", exc_info=True)
            logging.error(f"Reader loop error: {str(e)}", exc_info=True)
            return self.reactor.monotonic() + 1.0  # Повторить через 1 секунду

        # Запланировать следующую проверку
        return eventtime + 0.05

    def _reconnect(self):
        """Планирование повторного подключения"""
        with self._data_lock:
            self._connected = False
        self.connection_timer = self.reactor.register_timer(
            self._connection_handler, self.reactor.NOW)

    def _reset_connection(self):
        """Сброс соединения"""
        self._disconnect()
        time.sleep(1)
        self._connect()

    def _disconnect(self):
        """Безопасное отключение"""
        if not self._get_connected_state():
            return
        with self._data_lock:
            self._connected = False
        try:
            with self._serial_lock:
                if hasattr(self, '_serial'):
                    self._serial.close()
        except Exception as e:
            self.logger.error(f"Disconnect error: {str(e)}")
            logging.error(f"Disconnect error: {str(e)}")
        
        # Отменяем таймеры
        if self.reader_timer is not None:
            try:
                self.reactor.unregister_timer(self.reader_timer)
            except Exception as e:
                self.logger.error(f"Reader timer unregister error: {str(e)}")
                logging.error(f"Reader timer unregister error: {str(e)}")
            self.reader_timer = None
        
        if self.writer_timer is not None:
            try:
                self.reactor.unregister_timer(self.writer_timer)
            except Exception as e:
                self.logger.error(f"Writer timer unregister error: {str(e)}")
                logging.error(f"Writer timer unregister error: {str(e)}")
            self.writer_timer = None
        
    def _calc_crc(self, buffer: bytes) -> int:
        """Оптимизированный расчет CRC"""
        crc = 0xffff
        for byte in buffer:
            data = byte ^ (crc & 0xff)
            data ^= (data & 0x0f) << 4
            crc = ((data << 8) | (crc >> 8)) ^ (data >> 4) ^ (data << 3)
        return crc & 0xffff

    def _send_request(self, request: Dict[str, Any], callback: Callable = None) -> bool:
        """Отправка запроса с контролем очереди и использованием реактора"""
        start_time = self.reactor.monotonic()

        with self._serial_operation():
            try:
                # Проверка подключения
                if not self._get_connected_state() and not self._reconnect():
                    raise SerialException("Device not connected")

                # Проверка переполнения очереди
                if self._queue.qsize() >= self._max_queue_size:
                    self.logger.warning("Request queue overflow, clearing...")
                    logging.warning("Request queue overflow, clearing...")

                    # Очистка очереди через реактор
                    def clear_queue(eventtime):
                        try:
                            while not self._queue.empty():
                                _, cb = self._queue.get_nowait()
                                if cb:
                                    try:
                                        cb({'error': 'Queue overflow'})
                                    except Exception as e:
                                        self.logger.error(f"Queue overflow callback error: {str(e)}", exc_info=True)
                                        logging.error(f"Queue overflow callback error: {str(e)}", exc_info=True)
                        except Exception as clear_error:
                            self.logger.error(f"Queue clear error: {str(clear_error)}", exc_info=True)
                            logging.error(f"Queue clear error: {str(clear_error)}", exc_info=True)

                        # После очистки очереди можно попробовать отправить запрос снова
                        return self._send_request(request, callback)

                    # Регистрация таймера для очистки очереди
                    self.reactor.register_timer(clear_queue, self.reactor.NOW)
                    return False

                # Генерация ID запроса
                if 'id' not in request:
                    request['id'] = self._get_next_request_id()

                # Кодирование данных в JSON
                try:
                    payload = json.dumps(request).encode('utf-8')
                except Exception as e:
                    self.logger.error(f"JSON encoding error: {str(e)}")
                    logging.error(f"JSON encoding error: {str(e)}")
                    return False

                # Расчет CRC
                crc = self._calc_crc(payload)

                # Формирование пакета
                packet = (
                    bytes([0xFF, 0xAA]) +
                    struct.pack('<H', len(payload)) +
                    payload +
                    struct.pack('<H', crc) +
                    bytes([0xFE])
                )

                # Проверка состояния порта перед записью
                if not hasattr(self, '_serial') or not self._serial.is_open:
                    if not self._reconnect():
                        return False

                # Отправка данных
                try:
                    self._serial.write(packet)
                    with self._data_lock:
                        self.send_time = self.reactor.monotonic()
                    self.logger.debug(f"Request {request['id']} sent in {(self.reactor.monotonic()-start_time)*1000:.1f}ms")
                    logging.debug(f"Request {request['id']} sent in {(self.reactor.monotonic()-start_time)*1000:.1f}ms")

                    # Добавление callback в карту обратных вызовов
                    if callback:
                        self._add_callback(request['id'], callback)

                    return True
                except SerialException as e:
                    self.logger.error(f"Serial write error during send: {str(e)}")
                    logging.error(f"Serial write error during send: {str(e)}")
                    self._handle_serial_error(e)
                    return False

            except SerialException as e:
                self.logger.error(f"Send error: {str(e)}")
                logging.error(f"Send error: {str(e)}")
                self._handle_serial_error(e)
                return False

            except Exception as e:
                self.logger.error(f"Unexpected send error: {str(e)}", exc_info=True)
                logging.error(f"Unexpected send error: {str(e)}", exc_info=True)
                return False

    def _get_next_request_id(self) -> int:
        """Генерация ID запроса с защитой от переполнения"""
        with self._data_lock:
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
            callback = self._get_callback(response['id'])
            if callback:
                try:
                    callback(response)
                except Exception as e:
                    self.logger.error(f"Callback error: {str(e)}")
                    logging.error(f"Callback error: {str(e)}")
                    
        if 'result' in response and isinstance(response['result'], dict):
            result = response['result']
            with self._data_lock:
                self._info.update(result)
            
            if self._get_park_in_progress():
                current_status = result.get('status', 'unknown')
                current_assist_count = result.get('feed_assist_count', 0)
                
                if current_status == 'ready':
                    if current_assist_count != self._get_last_assist_count():
                        self._set_last_assist_count(current_assist_count)
                        self._set_assist_hit_count(0)
                    else:
                        self._inc_assist_hit_count()
                        if self._get_assist_hit_count() >= self.park_hit_count:
                            self._complete_parking()
                            return
                            
                    self.dwell(0.7, True)

    def _complete_parking(self):
       """Завершение процесса парковки"""
       if not self._get_park_in_progress():
           return
           
       self.logger.info(f"Parking completed for slot {self._get_park_index()}")
       logging.info(f"Parking completed for slot {self._get_park_index()}")
       try:
           # Остановка feed assist
           self.send_request({
               "method": "stop_feed_assist",
               "params": {"index": self._get_park_index()}
           }, lambda r: None)

           # Выполнение G-code команды в основном потоке
           if self._get_park_is_toolchange():
               def run_gcode():
                   self.gcode.run_script_from_command(
                       f'_ACE_POST_TOOLCHANGE FROM={self._get_park_previous_tool()} TO={self._get_park_index()}'
                   )
               self._main_queue.put(run_gcode)
       except Exception as e:
           self.logger.error(f"Parking completion error: {str(e)}", exc_info=True)
           logging.error(f"Parking completion error: {str(e)}", exc_info=True)
       finally:
           # Сброс состояния парковки
           with self._data_lock:
               self._park_in_progress = False
               self._park_is_toolchange = False
               self._park_previous_tool = -1
               self._park_index = -1
               
           if self.disable_assist_after_toolchange:
               with self._data_lock:
                   self._feed_assist_index = -1

    def _request_status(self):
        """Запрос статуса устройства"""
        def status_callback(response):
            if 'result' in response:
                with self._data_lock:
                    self._info.update(response['result'])
                
        if time.time() - self._get_last_status_request() > (0.2 if self._get_park_in_progress() else 1.0):
            try:
                self.send_request({
                    "id": self._get_next_request_id(),
                    "method": "get_status"
                }, status_callback)
                self._set_last_status_request(time.time())
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

        # Запланировать следующую проверку
        return eventtime + (0.05 if not self._main_queue.empty() else 0.1)

    def _handle_ready(self):
        """Обработчик готовности Klipper"""
        self.connection_timer = self.reactor.register_timer(self._connection_handler, self.reactor.NOW)
        
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
           if not self.reactor.is_main_thread():
               raise RuntimeError("Dwell must be called in the main thread or with on_main=True")
           main_callback()

    # ==================== Thread-safe getters/setters ====================
    def _get_connected_state(self) -> bool:
        with self._data_lock:
            return self._connected

    def _get_last_status_request(self) -> float:
        with self._data_lock:
            return self._last_status_request

    def _set_last_status_request(self, value: float):
        with self._data_lock:
            self._last_status_request = value

    def _get_park_in_progress(self) -> bool:
        with self._data_lock:
            return self._park_in_progress

    def _get_park_is_toolchange(self) -> bool:
        with self._data_lock:
            return self._park_is_toolchange

    def _get_park_previous_tool(self) -> int:
        with self._data_lock:
            return self._park_previous_tool

    def _get_park_index(self) -> int:
        with self._data_lock:
            return self._park_index

    def _get_last_assist_count(self) -> int:
        with self._data_lock:
            return self._last_assist_count

    def _set_last_assist_count(self, value: int):
        with self._data_lock:
            self._last_assist_count = value

    def _get_assist_hit_count(self) -> int:
        with self._data_lock:
            return self._assist_hit_count

    def _set_assist_hit_count(self, value: int):
        with self._data_lock:
            self._assist_hit_count = value

    def _inc_assist_hit_count(self):
        with self._data_lock:
            self._assist_hit_count += 1

    def _add_callback(self, request_id: int, callback: Callable):
        with self._callback_lock:
            self._callback_map[request_id] = callback

    def _get_callback(self, request_id: int) -> Optional[Callable]:
        with self._callback_lock:
            return self._callback_map.pop(request_id, None)

    # ==================== G-CODE COMMANDS ====================
    cmd_ACE_STATUS_help = "Get current device status"
    def cmd_ACE_STATUS(self, gcmd):
        """Обработчик команды ACE_STATUS"""
        try:
            with self._data_lock:
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
                    with self._data_lock:
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
            index = gcmd.get_int('INDEX', self._get_feed_assist_index(), minval=0, maxval=3)
            
            def callback(response):
                if response.get('code', 0) != 0:
                    gcmd.respond_raw(f"ACE Error: {response.get('msg', 'Unknown error')}")
                else:
                    with self._data_lock:
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

    def _get_feed_assist_index(self) -> int:
        with self._data_lock:
            return self._feed_assist_index

    def _park_to_toolhead(self, index: int):
        """Внутренний метод парковки филамента"""
        try:
            def callback(response):
                if response.get('code', 0) != 0:
                    raise ValueError(f"ACE Error: {response.get('msg', 'Unknown error')}")
                    
                with self._data_lock:
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
           if self._get_park_in_progress():
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
                self.gcode.run_script_from_command(f"_ACE_ON_EMPTY_ERROR INDEX={tool}")
                return
            
            # Выполняем pre-toolchange в основном потоке
            self.gcode.run_script_from_command(f"_ACE_PRE_TOOLCHANGE FROM={was} TO={tool}")
            
            # Сохраняем состояние
            self.variables['ace_current_index'] = tool
            self.gcode.run_script_from_command(f'SAVE_VARIABLE VARIABLE=ace_current_index VALUE={tool}')
            
            if was != -1:
                # Выгружаем предыдущий филамент
                gcmd.respond_info(f"Unloading filament from T{was}...")
                self._execute_retract(was, gcmd)
                
                # Добавляем задержку для ожидания завершения через таймер
                def check_unload_complete(eventtime=None):
                    with self._data_lock:
                        if self._info['status'] == 'ready':
                            # Если выгрузка завершена, продолжаем
                            if tool != -1:
                                gcmd.respond_info(f"Loading filament to T{tool}...")
                                self.gcode.run_script_from_command(f'ACE_PARK_TO_TOOLHEAD INDEX={tool}')
                            else:
                                gcmd.respond_info(f"Tool T{was} unloaded, no tool selected")
                            
                            # Выполняем post-toolchange в основном потоке
                            self.gcode.run_script_from_command(f'_ACE_POST_TOOLCHANGE FROM={was} TO={tool}')
                            return self.reactor.NEVER  # Прекращаем таймер
                        
                        # Если выгрузка еще не завершена, проверяем снова через 0.5 секунды
                        return self.reactor.monotonic() + 0.5
                
                # Начинаем проверку завершения выгрузки
                self.reactor.register_timer(check_unload_complete, self.reactor.NOW)
            
            elif tool != -1:
                # Если просто загрузка нового инструмента
                gcmd.respond_info(f"Loading filament to T{tool}...")
                self.gcode.run_script_from_command(f'ACE_PARK_TO_TOOLHEAD INDEX={tool}')
                self.gcode.run_script_from_command(f'_ACE_POST_TOOLCHANGE FROM={was} TO={tool}')
        
        except Exception as e:
            self.logger.error(f"Change tool error: {str(e)}", exc_info=True)
            gcmd.respond_raw(f"Error: {str(e)}")
            
    def _execute_retract(self, tool_index, gcmd):
        """Выполняет выгрузку филамента через механизм таймеров реактора"""
        # Отправляем запрос на выгрузку филамента
        self.send_request({
            "method": "unwind_filament",
            "params": {
                "index": tool_index,
                "length": self.toolchange_retract_length,
                "speed": self.retract_speed
            }
        }, lambda response: None)  # Callback можно оставить пустым или обработать ошибки

        # Инициализация состояния для ожидания
        self._retract_done = False
        self._retract_start_time = time.time()

        # Регистрация таймера для проверки статуса
        self.retract_timer = self.reactor.register_timer(
            self._check_retract_status, self.reactor.NOW)

        gcmd.respond_info(f"Started filament retract from T{tool_index}")

    def _check_retract_status(self, eventtime):
        """Проверка статуса выгрузки филамента через реактор"""
        with self._data_lock:
            if self._info['status'] == 'ready':
                # Выгрузка завершена успешно
                self.logger.info("Filament retract completed successfully")
                logging.info("Filament retract completed successfully")
                return self.reactor.NEVER  # Прекращаем таймер

        if time.time() - self._retract_start_time >= 5.0:
            # Таймаут при ожидании выгрузки
            self.logger.error("Timeout waiting for filament retract completion")
            logging.error("Timeout waiting for filament retract completion")
            return self.reactor.NEVER  # Прекращаем таймер

        # Повторить проверку через 0.1 секунды
        return eventtime + 0.1
        
def load_config(config):
    return ValgAce(config)