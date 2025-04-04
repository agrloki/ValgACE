import os
import serial
import serial.tools.list_ports
import threading
import time
import logging
import json
import struct
import queue
import traceback
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
        self.lock = False
        self.read_buffer = bytearray()
        self.send_time = 0

        # Инициализация логирования
        self._init_logging(config)
        
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
        self._queue = queue.Queue()
        self._main_queue = queue.Queue()
        
        # Инициализация
        self._register_handlers()
        self._register_gcode_commands()

    def _init_logging(self, config):
        """Инициализация системы логирования"""
        log_dir = config.get('log_dir', '/var/log/ace')
        log_level = config.get('log_level', 'INFO').upper()
        max_log_size = config.getint('max_log_size', 10) * 1024 * 1024  # в мегабайтах
        log_backup_count = config.getint('log_backup_count', 3)
        
        # Создаем директорию для логов, если она не существует
        try:
            os.makedirs(log_dir, exist_ok=True)
        except OSError as e:
            print(f"Error creating log directory: {e}")
            log_dir = '/tmp'  # fallback directory

        log_file = os.path.join(log_dir, 'ace.log')
        
        # Настройка формата логов
        log_format = '%(asctime)s [%(levelname)s] %(message)s'
        date_format = '%Y-%m-%d %H:%M:%S'
        
        # Настройка обработчика для файла
        file_handler = logging.handlers.RotatingFileHandler(
            log_file,
            maxBytes=max_log_size,
            backupCount=log_backup_count
        )
        file_handler.setFormatter(logging.Formatter(log_format, date_format))
        
        # Настройка обработчика для консоли
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(logging.Formatter(log_format, date_format))
        
        # Получаем root логгер и настраиваем его
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
                    self.logger.info(f"Connected to ACE at {self.serial_name}")
                    
                    # Запускаем потоки только если они еще не запущены
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
                    
                    # Запрос информации об устройстве
                    def info_callback(response):
                        res = response['result']
                        self.logger.info(f"Device info: {res.get('model', 'Unknown')} {res.get('firmware', 'Unknown')}")
                        self.gcode.respond_info(f"Connected {res.get('model', 'Unknown')} {res.get('firmware', 'Unknown')}")
                    self.send_request({"method": "get_info"}, info_callback)
                    
                    return True
                    
            except SerialException as e:
                self.logger.warning(f"Connection attempt {attempt + 1} failed: {str(e)}")
                time.sleep(1)
        
        self.logger.error("Failed to connect to ACE device")
        return False

    def _reconnect(self) -> bool:
        """Безопасное переподключение"""
        if not self._connected:
            return self._connect()
        
        try:
            # Сохраняем ссылки на старые потоки
            old_writer = getattr(self, '_writer_thread', None)
            old_reader = getattr(self, '_reader_thread', None)
            
            # Сбрасываем флаги перед новым подключением
            self._connected = False
            
            # Пытаемся подключиться
            if self._connect():
                # Аккуратно завершаем старые потоки
                if old_writer and old_writer.is_alive() and old_writer != threading.current_thread():
                    try:
                        old_writer.join(timeout=0.5)
                    except:
                        pass
                
                if old_reader and old_reader.is_alive() and old_reader != threading.current_thread():
                    try:
                        old_reader.join(timeout=0.5)
                    except:
                        pass
                
                return True
            return False
        except Exception as e:
            self.logger.error(f"Reconnect error: {str(e)}")
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
        
        # Не пытаемся завершить потоки из самих потоков
        current_thread = threading.current_thread()
        if hasattr(self, '_writer_thread') and self._writer_thread != current_thread:
            try:
                self._writer_thread.join(timeout=1)
            except:
                pass
        
        if hasattr(self, '_reader_thread') and self._reader_thread != current_thread:
            try:
                self._reader_thread.join(timeout=1)
            except:
                pass
        
        if hasattr(self, 'main_timer'):
            try:
                self.reactor.unregister_timer(self.main_timer)
            except:
                pass

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
            
            self._serial.write(packet)
            self.send_time = time.time()
            self.lock = True
            self.logger.debug(f"Sent request: {request}")
            return True
        except SerialException:
            self.logger.error("Write error, attempting reconnect")
            self._reconnect()
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
                time.sleep(max(0, next_eventtime - self.reactor.monotonic()))
            except Exception as e:
                self.logger.error(f"Reader loop error: {str(e)}")
                time.sleep(1)

    def _reader(self, eventtime):
        buffer = bytearray()
        while True:
            try:
                raw_bytes = self._serial.read(size=4096)
            except SerialException:
                self.logger.error("Unable to communicate with the ACE PRO" + traceback.format_exc())
                self.lock = False
                return eventtime + 0.5

            if len(raw_bytes):
                text_buffer = self.read_buffer + raw_bytes
                i = text_buffer.find(b'\xfe')
                if i >= 0:
                    buffer = text_buffer
                    self.read_buffer = bytearray()
                else:
                    self.read_buffer += raw_bytes
            else:
                break

        if self.lock and (self.reactor.monotonic() - self.send_time) > 2:
            self.lock = False
            self.logger.warning(f"Timeout {self.reactor.monotonic()}")
            return eventtime + 0.1

        if len(buffer) < 7:
            return eventtime + 0.1

        if buffer[0:2] != bytes([0xFF, 0xAA]):
            self.lock = False
            self.logger.error("Invalid data from ACE PRO (head bytes)")
            self.logger.debug(f"Buffer: {buffer}")
            return eventtime + 0.1

        payload_len = struct.unpack('<H', buffer[2:4])[0]
        payload = buffer[4:4 + payload_len]
        crc_data = buffer[4 + payload_len:4 + payload_len + 2]
        crc = struct.pack('@H', self._calc_crc(payload))

        if len(buffer) < (4 + payload_len + 2 + 1):
            self.lock = False
            self.logger.error(f"Invalid data from ACE PRO (len) {payload_len} {len(buffer)} {crc}")
            self.logger.debug(f"Buffer: {buffer}")
            return eventtime + 0.1

        if crc_data != crc:
            self.lock = False
            self.logger.error("Invalid data from ACE PRO (CRC)")
            return eventtime + 0.1

        try:
            response = json.loads(payload.decode('utf-8'))
            self.logger.debug(f"Received response: {response}")

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

            # Вызываем callback с ответом
            if 'id' in response and response['id'] in self._callback_map:
                callback = self._callback_map.pop(response['id'])
                try:
                    callback(response)  # Передаем только response
                except Exception as e:
                    self.logger.error(f"Callback error: {str(e)}")

        except json.JSONDecodeError:
            self.logger.error("Invalid JSON from ACE PRO")
            return eventtime + 0.1
        except Exception as e:
            self.logger.error(f"Error processing response: {str(e)}")
            return eventtime + 0.1

        return eventtime + 0.1

    def _complete_parking(self):
        """Завершение процесса парковки"""
        self._park_in_progress = False
        self.logger.info(f'Parked to toolhead with assist count: {self._last_assist_count}')

        def stop_callback(response):
            if response.get('code', 0) != 0:
                self.logger.error(f"Failed to stop feed assist: {response.get('msg', 'Unknown error')}")
            
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
                if not self._queue.empty():
                    task = self._queue.get_nowait()
                    if task:
                        request, callback = task
                        self._callback_map[request['id']] = callback
                        if not self._send_request(request):
                            continue
                
                # Периодический запрос статуса
                def status_callback(response):
                    if 'result' in response:
                        self._info = response['result']
                
                self.send_request({
                    "id": self._request_id,
                    "method": "get_status"
                }, status_callback)
                
                # Безопасная задержка
                time.sleep(0.25 if not self._park_in_progress else 0.68)

            except SerialException:
                self.logger.error("Serial write error")
                if self._connected:
                    self._reconnect()
            except Exception as e:
                self.logger.error(f"Writer loop error: {str(e)}")
                time.sleep(1)

    def _main_eval(self, eventtime):
        """Обработка задач в основном потоке"""
        while not self._main_queue.empty():
            task = self._main_queue.get_nowait()
            if task:
                task()
        return eventtime + 0.25

    def _handle_ready(self):
        """Обработчик готовности Klipper"""
        if not self._connect():
            self.logger.error("Failed to connect to ACE on startup")
            return

    def _handle_disconnect(self):
        """Обработчик отключения Klipper"""
        self._disconnect()

    def send_request(self, request: Dict[str, Any], callback: Callable):
        """Добавление запроса в очередь"""
        if not self._connected and not self._reconnect():
            raise SerialException("Device not connected")
        
        if 'id' not in request:
            request['id'] = self._request_id
            self._request_id += 1
        
        self._queue.put((request, callback))

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
        """Обработчик команды ACE_STATUS"""
        status = json.dumps(self._info, indent=2)
        gcmd.respond_info(f"ACE Status:\n{status}")
        self.logger.info(f"Status requested:\n{status}")

    cmd_ACE_DEBUG_help = "Debug ACE connection"
    def cmd_ACE_DEBUG(self, gcmd):
        """Обработчик команды ACE_DEBUG"""
        method = gcmd.get('METHOD')
        params = gcmd.get('PARAMS', '{}')
        
        # Создаем event для ожидания ответа
        response_event = threading.Event()
        response_data = [None]

        def callback(response):  # Теперь принимает только response
            response_data[0] = response
            response_event.set()

        try:
            request = {"method": method}
            if params.strip():
                try:
                    request["params"] = json.loads(params)
                except json.JSONDecodeError:
                    gcmd.respond_error("Invalid PARAMS format")
                    self.logger.error("Invalid PARAMS format in debug command")
                    return

            self.send_request(request, callback)
            self.logger.debug(f"Debug command sent: {request}")

            if not response_event.wait(5.0):
                gcmd.respond_error("Timeout waiting for response")
                self.logger.error("Timeout waiting for debug response")
                return

            response = response_data[0]
            if response is None:
                gcmd.respond_error("No response received")
                self.logger.error("No response received for debug command")
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
                    
                    # Добавляем информацию о слотах
                    for slot in result.get('slots', []):
                        output.append(f"\nSlot {slot.get('index', '?')}:")
                        output.append(f"  Status: {slot.get('status', 'Unknown')}")
                        output.append(f"  Type: {slot.get('type', 'Unknown')}")
                        output.append(f"  Color: {slot.get('color', 'Unknown')}")
                
                gcmd.respond_info("\n".join(output))
                self.logger.info("\n".join(output))
            else:
                gcmd.respond_info(json.dumps(response, indent=2))
                self.logger.info(f"Debug response: {json.dumps(response, indent=2)}")

        except Exception as e:
            gcmd.respond_error(f"Error: {str(e)}")
            self.logger.error(f"Debug command error: {str(e)}")

    cmd_ACE_FILAMENT_INFO_help = 'ACE_FILAMENT_INFO INDEX='
    def cmd_ACE_FILAMENT_INFO(self, gcmd):
        """Handler for ACE_FILAMENT_INFO command - get detailed filament slot info"""
        index = gcmd.get_int('INDEX', minval=0, maxval=3)
        try:
            def callback(response):
                if 'result' in response:
                    slot_info = response['result']
                    self.gcode.respond_info(str(slot_info))
                    self.logger.info(f'Filament slot {index} status: {slot_info}')
                else:
                    self.gcode.respond_info('Error: No result in response')
                    self.logger.error(f'No result in response for slot {index}')

            self.send_request(
                request={"method": "get_filament_info", "params": {"index": index}},
                callback=callback
            )
            self.logger.debug(f"Requested filament info for slot {index}")
        except Exception as e:
            self.gcode.respond_info('Error: ' + str(e))
            self.logger.error(f"Filament info error for slot {index}: {str(e)}")

    cmd_ACE_START_DRYING_help = "Start filament drying"
    def cmd_ACE_START_DRYING(self, gcmd):
        """Обработчик команды ACE_START_DRYING"""
        temperature = gcmd.get_int('TEMP', minval=20, maxval=self.max_dryer_temperature)
        duration = gcmd.get_int('DURATION', 240, minval=1)

        def callback(response):
            if response.get('code', 0) != 0:
                gcmd.respond_error(f"ACE Error: {response.get('msg', 'Unknown error')}")
                self.logger.error(f"Failed to start drying: {response.get('msg', 'Unknown error')}")
            else:
                gcmd.respond_info(f"Drying started at {temperature}°C for {duration} minutes")
                self.logger.info(f"Drying started at {temperature}°C for {duration} minutes")

        self.send_request({
            "method": "drying",
            "params": {
                "temp": temperature,
                "fan_speed": 7000,
                "duration": duration * 60
            }
        }, callback)
        self.logger.debug(f"Start drying command: {temperature}°C for {duration} minutes")

    cmd_ACE_STOP_DRYING_help = "Stop filament drying"
    def cmd_ACE_STOP_DRYING(self, gcmd):
        """Обработчик команды ACE_STOP_DRYING"""
        def callback(response):
            if response.get('code', 0) != 0:
                gcmd.respond_error(f"ACE Error: {response.get('msg', 'Unknown error')}")
                self.logger.error(f"Failed to stop drying: {response.get('msg', 'Unknown error')}")
            else:
                gcmd.respond_info("Drying stopped")
                self.logger.info("Drying stopped")

        self.send_request({"method": "drying_stop"}, callback)
        self.logger.debug("Stop drying command")

    cmd_ACE_ENABLE_FEED_ASSIST_help = "Enable feed assist"
    def cmd_ACE_ENABLE_FEED_ASSIST(self, gcmd):
        """Обработчик команды ACE_ENABLE_FEED_ASSIST"""
        index = gcmd.get_int('INDEX', minval=0, maxval=3)

        def callback(response):
            if response.get('code', 0) != 0:
                gcmd.respond_error(f"ACE Error: {response.get('msg', 'Unknown error')}")
                self.logger.error(f"Failed to enable feed assist for slot {index}: {response.get('msg', 'Unknown error')}")
            else:
                self._feed_assist_index = index
                gcmd.respond_info(f"Feed assist enabled for slot {index}")
                self.logger.info(f"Feed assist enabled for slot {index}")
                self.dwell(0.3)

        self.send_request({
            "method": "start_feed_assist",
            "params": {"index": index}
        }, callback)
        self.logger.debug(f"Enable feed assist for slot {index}")

    cmd_ACE_DISABLE_FEED_ASSIST_help = "Disable feed assist"
    def cmd_ACE_DISABLE_FEED_ASSIST(self, gcmd):
        """Обработчик команды ACE_DISABLE_FEED_ASSIST"""
        index = gcmd.get_int('INDEX', self._feed_assist_index, minval=0, maxval=3)

        def callback(response):
            if response.get('code', 0) != 0:
                gcmd.respond_error(f"ACE Error: {response.get('msg', 'Unknown error')}")
                self.logger.error(f"Failed to disable feed assist for slot {index}: {response.get('msg', 'Unknown error')}")
            else:
                self._feed_assist_index = -1
                gcmd.respond_info(f"Feed assist disabled for slot {index}")
                self.logger.info(f"Feed assist disabled for slot {index}")
                self.dwell(0.3)

        self.send_request({
            "method": "stop_feed_assist",
            "params": {"index": index}
        }, callback)
        self.logger.debug(f"Disable feed assist for slot {index}")

    def _park_to_toolhead(self, index: int):
        """Внутренний метод парковки филамента"""
        def callback(response):
            if response.get('code', 0) != 0:
                self.logger.error(f"Failed to park to toolhead: {response.get('msg', 'Unknown error')}")
                raise ValueError(f"ACE Error: {response.get('msg', 'Unknown error')}")
            
            self._assist_hit_count = 0
            self._last_assist_count = 0
            self._park_in_progress = True
            self._park_index = index
            self.logger.info(f"Parking to toolhead started for slot {index}")
            self.dwell(0.3)

        self.send_request({
            "method": "start_feed_assist",
            "params": {"index": index}
        }, callback)
        self.logger.debug(f"Park to toolhead command for slot {index}")

    cmd_ACE_PARK_TO_TOOLHEAD_help = "Park filament to toolhead"
    def cmd_ACE_PARK_TO_TOOLHEAD(self, gcmd):
        """Обработчик команды ACE_PARK_TO_TOOLHEAD"""
        if self._park_in_progress:
            gcmd.respond_error("Already parking to toolhead")
            self.logger.warning("Parking already in progress")
            return

        index = gcmd.get_int('INDEX', minval=0, maxval=3)
        
        if self._info['slots'][index]['status'] != 'ready':
            self.logger.error(f"Slot {index} is empty, cannot park")
            self.gcode.run_script_from_command(f"_ACE_ON_EMPTY_ERROR INDEX={index}")
            return

        self._park_to_toolhead(index)
        self.logger.info(f"Parking to toolhead initiated for slot {index}")

    cmd_ACE_FEED_help = "Feed filament"
    def cmd_ACE_FEED(self, gcmd):
        """Обработчик команды ACE_FEED"""
        index = gcmd.get_int('INDEX', minval=0, maxval=3)
        length = gcmd.get_int('LENGTH', minval=1)
        speed = gcmd.get_int('SPEED', self.feed_speed, minval=1)

        def callback(response):
            if response.get('code', 0) != 0:
                gcmd.respond_error(f"ACE Error: {response.get('msg', 'Unknown error')}")
                self.logger.error(f"Failed to feed filament: {response.get('msg', 'Unknown error')}")

        self.send_request({
            "method": "feed_filament",
            "params": {
                "index": index,
                "length": length,
                "speed": speed
            }
        }, callback)
        self.logger.info(f"Feeding {length}mm from slot {index} at {speed}mm/s")
        self.dwell((length / speed) + 0.1)

    cmd_ACE_RETRACT_help = "Retract filament"
    def cmd_ACE_RETRACT(self, gcmd):
        """Обработчик команды ACE_RETRACT"""
        index = gcmd.get_int('INDEX', minval=0, maxval=3)
        length = gcmd.get_int('LENGTH', minval=1)
        speed = gcmd.get_int('SPEED', self.retract_speed, minval=1)

        def callback(response):
            if response.get('code', 0) != 0:
                gcmd.respond_error(f"ACE Error: {response.get('msg', 'Unknown error')}")
                self.logger.error(f"Failed to retract filament: {response.get('msg', 'Unknown error')}")

        self.send_request({
            "method": "unwind_filament",
            "params": {
                "index": index,
                "length": length,
                "speed": speed
            }
        }, callback)
        self.logger.info(f"Retracting {length}mm from slot {index} at {speed}mm/s")
        self.dwell((length / speed) + 0.1)

    cmd_ACE_CHANGE_TOOL_help = "Change tool"
    def cmd_ACE_CHANGE_TOOL(self, gcmd):
        """Обработчик команды ACE_CHANGE_TOOL"""
        tool = gcmd.get_int('TOOL', minval=-1, maxval=3)
        was = self.variables.get('ace_current_index', -1)
        
        if was == tool:
            gcmd.respond_info(f"Tool already set to {tool}")
            self.logger.info(f"Tool already set to {tool}, no change needed")
            return
        
        if tool != -1 and self._info['slots'][tool]['status'] != 'ready':
            self.logger.error(f"Slot {tool} is empty, cannot change tool")
            self.gcode.run_script_from_command(f"_ACE_ON_EMPTY_ERROR INDEX={tool}")
            return

        self.logger.info(f"Starting tool change from {was} to {tool}")
        self.gcode.run_script_from_command(f"_ACE_PRE_TOOLCHANGE FROM={was} TO={tool}")
        self._park_is_toolchange = True
        self._park_previous_tool = was
        self.variables['ace_current_index'] = tool
        self.gcode.run_script_from_command(f'SAVE_VARIABLE VARIABLE=ace_current_index VALUE={tool}')

        def callback(response):
            if response.get('code', 0) != 0:
                gcmd.respond_error(f"ACE Error: {response.get('msg', 'Unknown error')}")
                self.logger.error(f"Tool change error: {response.get('msg', 'Unknown error')}")

        if was != -1:
            self.send_request({
                "method": "unwind_filament",
                "params": {
                    "index": was,
                    "length": self.toolchange_retract_length,
                    "speed": self.retract_speed
                }
            }, callback)
            self.logger.info(f"Retracting old tool {was} with length {self.toolchange_retract_length}mm")
            self.dwell((self.toolchange_retract_length / self.retract_speed) + 0.1)

            while self._info['status'] != 'ready':
                self.dwell(1.0)
            
            self.dwell(0.25)

            if tool != -1:
                self.logger.info(f"Loading new tool {tool}")
                self.gcode.run_script_from_command(f'ACE_PARK_TO_TOOLHEAD INDEX={tool}')
            else:
                self.logger.info("Tool change completed (unloaded)")
                self.gcode.run_script_from_command(f'_ACE_POST_TOOLCHANGE FROM={was} TO={tool}')
        else:
            self.logger.info(f"Loading new tool {tool}")
            self._park_to_toolhead(tool)

def load_config(config):
    return ValgAce(config)