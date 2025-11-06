# File: ace.py — ValgAce module for Klipper

import logging
import json
import struct
import queue
from typing import Optional, Dict, Any, Callable

# Check for required libraries and raise an error if they are not available
try:
    import serial
    import serial.tools.list_ports
    from serial import SerialException
except ImportError:
    serial = None
    SerialException = Exception
    raise ImportError("The 'pyserial' library is required for ValgAce module. Please install it using 'pip install pyserial'")


class ValgAce:
    """
    Модуль ValgAce для Klipper
    Обеспечивает управление устройством автоматической смены филамента (ACE)
    Поддерживает до 4 слотов для катушек с возможностью сушки, подачи и обратной подачи филамента
    """
    def __init__(self, config):
        self.printer = config.get_printer()
        self.toolhead = None
        self.reactor = self.printer.get_reactor()
        self.gcode = self.printer.lookup_object('gcode')
        # Initialize logger
        self.logger = logging.getLogger('ace')
        self._name = 'ace'
        # Optional dependency: save_variables
        try:
            save_vars = self.printer.lookup_object('save_variables')
            self.variables = save_vars.allVariables
        except self.printer.config_error:
            # save_variables not loaded, create fallback dict
            self.variables = {}
            self.logger.warning("save_variables module not found, variables will not persist across restarts")
        self.read_buffer = bytearray()
        self.send_time = 0
        self._last_status_request = 0

        # Параметры таймаутов
        # Timeout parameters
        self._response_timeout = config.getfloat('response_timeout', 2.0)
        self._read_timeout = config.getfloat('read_timeout', 0.1)
        self._write_timeout = config.getfloat('write_timeout', 0.5)
        self._max_queue_size = config.getint('max_queue_size', 20)

        # Автопоиск устройства
        # Auto-detect device
        default_serial = self._find_ace_device()
        self.serial_name = config.get('serial', default_serial or '/dev/ttyACM0')
        self.baud = config.getint('baud', 115200)

        # Параметры конфигурации
        # Configuration parameters
        self.feed_speed = config.getint('feed_speed', 50)
        self.retract_speed = config.getint('retract_speed', 50)
        self.retract_mode = config.getint('retract_mode', 0)
        self.toolchange_retract_length = config.getint('toolchange_retract_length', 100)
        self.park_hit_count = config.getint('park_hit_count', 5)
        self.max_dryer_temperature = config.getint('max_dryer_temperature', 55)
        self.disable_assist_after_toolchange = config.getboolean('disable_assist_after_toolchange', True)
        self.infinity_spool_mode = config.getboolean ('infinity_spool_mode', False)

        # Состояние устройства
        # Device state
        self._info = self._get_default_info()
        self._callback_map = {}
        self._request_id = 0
        self._connected = False
        self._connection_attempts = 0
        self._max_connection_attempts = 5

        # Работа
        # Operation
        self._feed_assist_index = -1
        self._last_assist_count = 0
        self._assist_hit_count = 0
        self._park_in_progress = False
        self._park_error = False  # Flag to track parking errors
        self._park_is_toolchange = False
        self._park_previous_tool = -1
        self._park_index = -1

        # Очереди
        # Queues
        self._queue = queue.Queue(maxsize=self._max_queue_size)

        # Порты и реактор
        # Ports and reactor
        self._serial = None
        self._reader_timer = None
        self._writer_timer = None

        # Регистрация событий
        # Register events
        self._register_handlers()
        self._register_gcode_commands()

        # Подключение при запуске
        # Connect on startup
        self.reactor.register_timer(self._connect_check, self.reactor.NOW)
        
        # Инициализация флага для избежания дублирования dwell таймеров
        self._dwell_scheduled = False

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
        """
        Регистрация обработчиков событий принтера
        """
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
            ('ACE_INFINITY_SPOOL', self.cmd_ACE_INFINITY_SPOOL, "Change tool when current spool is empty"),
            ('ACE_SET_INFINITY_SPOOL_ORDER', self.cmd_ACE_SET_INFINITY_SPOOL_ORDER, "Set infinity spool slot order"),
            ('ACE_FILAMENT_INFO', self.cmd_ACE_FILAMENT_INFO, "Show filament info"),
        ]
        for name, func, desc in commands:
            self.gcode.register_command(name, func, desc=desc)

    def _find_ace_device(self) -> Optional[str]:
        """
        Автоматический поиск устройства ACE по VID/PID или описанию
        :return: Путь к порту устройства или None, если устройство не найдено
        """
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
        self.logger.info("No ACE device found by auto-detection")
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
                    self.logger.info(f"Connected to ACE at {self.serial_name}")

                    def info_callback(response):
                        res = response['result']
                        self.logger.info(f"Device info: {res.get('model', 'Unknown')} {res.get('firmware', 'Unknown')}")
                        self.gcode.respond_info(f"Connected {res.get('model', 'Unknown')} {res.get('firmware', 'Unknown')}")

                    self.send_request({"method": "get_info"}, info_callback)

                    if self._reader_timer is None:
                        self._reader_timer = self.reactor.register_timer(self._reader_loop, self.reactor.NOW)
                    if self._writer_timer is None:
                        self._writer_timer = self.reactor.register_timer(self._writer_loop, self.reactor.NOW)
                        
                    self.logger.info("Connection established successfully")
                    return True
            except SerialException as e:
                self.logger.info(f"Connection attempt {attempt + 1} failed: {str(e)}")
                self.dwell(1.0, lambda: None)
        self.logger.info("Failed to connect to ACE device")
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
            self.logger.info(f"Disconnect error: {str(e)}")

    def _save_variable(self, name: str, value):
        """Safely save variable if save_variables module is available"""
        self.variables[name] = value
        try:
            self.gcode.run_script_from_command(f'SAVE_VARIABLE VARIABLE={name} VALUE={value}')
        except Exception as e:
            # save_variables not available or error saving
            self.logger.debug(f"Could not save variable {name}: {e}")

    def _handle_ready(self):
        self.toolhead = self.printer.lookup_object('toolhead')
        if self.toolhead is None:
            raise self.printer.config_error("Toolhead not found in ValgAce module")

    def _handle_disconnect(self):
        self._disconnect()

    def get_status(self, eventtime):
        """Возвращает статус для Moonraker API через query_objects"""
        # Klipper автоматически вызывает этот метод при запросе через query_objects
        # Moonraker автоматически оборачивает результат в ключ с именем модуля ('ace')
        
        # Получаем данные о сушилке
        dryer_data = self._info.get('dryer', {}) or self._info.get('dryer_status', {})
        
        # Нормализуем время: конвертируем секунды в минуты если нужно
        if isinstance(dryer_data, dict):
            dryer_normalized = dryer_data.copy()
            
            # По протоколу duration и remain_time должны быть в минутах
            # Но устройство может отправлять в секундах, проверяем
            duration_raw = dryer_normalized.get('duration', 0)
            remain_time_raw = dryer_normalized.get('remain_time', 0)
            
            # Если duration > 1440 минут (24 часа), вероятно это секунды
            if duration_raw > 1440:
                dryer_normalized['duration'] = duration_raw / 60  # Сохраняем дробную часть
                if remain_time_raw > 0:
                    dryer_normalized['remain_time'] = remain_time_raw / 60  # Сохраняем дробную часть
            elif remain_time_raw > 1440:
                # duration в минутах, но remain_time в секундах
                dryer_normalized['remain_time'] = remain_time_raw / 60  # Сохраняем дробную часть
            # Иначе оставляем как есть (уже в минутах)
        else:
            dryer_normalized = {}
        
        return {
            'status': self._info.get('status', 'unknown'),
            'model': self._info.get('model', ''),
            'firmware': self._info.get('firmware', ''),
            'boot_firmware': self._info.get('boot_firmware', ''),
            'temp': self._info.get('temp', 0),
            'fan_speed': self._info.get('fan_speed', 0),
            'enable_rfid': self._info.get('enable_rfid', 0),
            'feed_assist_count': self._info.get('feed_assist_count', 0),
            'cont_assist_time': self._info.get('cont_assist_time', 0.0),
            'feed_assist_slot': self._feed_assist_index,  # Индекс слота с активным feed assist (-1 = выключен)
            'dryer': dryer_normalized,
            'dryer_status': dryer_normalized,
            'slots': self._info.get('slots', [])
        }

    def _calc_crc(self, buffer: bytes) -> int:
        """
        Вычисление CRC для буфера данных
        :param buffer: Байтовый буфер для вычисления CRC
        :return: Значение CRC
        """
        crc = 0xffff
        for byte in buffer:
            data = byte ^ (crc & 0xff)
            data ^= (data & 0x0f) << 4
            crc = (((data << 8) | (crc >> 8)) ^ (data >> 4) ^ (data << 3)) & 0xffff
        return crc & 0xffff

    def send_request(self, request: Dict[str, Any], callback: Callable):
        if self._queue.qsize() >= self._max_queue_size:
            self.logger.info("Request queue overflow, clearing...")
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
            self.logger.info(f"JSON encoding error: {str(e)}")
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
            self.logger.info(f"Send error: {str(e)}")
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
            self.logger.info(f"Read error: {str(e)}")
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
                self.logger.info(f"Incomplete message received (expected {expected_length}, got {len(msg)})")
                incomplete_message_count += 1
                if incomplete_message_count > max_incomplete_messages_before_reset:
                    self.logger.info("Too many incomplete messages, resetting connection")
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
                self.logger.info(f"JSON decode error: {str(je)} Data: {msg}")
            except Exception as e:
                self.logger.info(f"Message processing error: {str(e)} Data: {msg}")

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
                    self.logger.info("Failed to send request, requeuing...")
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
                self.logger.info(f"Status request error: {str(e)}")

    def _handle_response(self, response: dict):
        if 'id' in response:
            callback = self._callback_map.pop(response['id'], None)
            if callback:
                try:
                    callback(response)
                except Exception as e:
                    self.logger.info(f"Callback error: {str(e)}")
        if 'result' in response and isinstance(response['result'], dict):
            result = response['result']
            # Нормализация данных о сушилке: если приходит dryer_status, сохраняем также как dryer
            if 'dryer_status' in result and isinstance(result['dryer_status'], dict):
                result['dryer'] = result['dryer_status']
            self._info.update(result)
            if self._park_in_progress:
                current_status = result.get('status', 'unknown')
                current_assist_count = result.get('feed_assist_count', 0)
                elapsed_time = self.reactor.monotonic() - self._park_start_time
                
                self.logger.debug(f"Parking check: slot {self._park_index}, count={current_assist_count}, " +
                                f"last={self._last_assist_count}, hits={self._assist_hit_count}, elapsed={elapsed_time:.1f}s")
                
                if current_status == 'ready':
                    if current_assist_count != self._last_assist_count:
                        self._last_assist_count = current_assist_count
                        self._assist_hit_count = 0
                        # Mark that count has increased at least once
                        if not hasattr(self, '_park_count_increased'):
                            self._park_count_increased = False
                        if current_assist_count > 0:
                            self._park_count_increased = True
                            self.logger.info(f"Feed assist working for slot {self._park_index}, count: {current_assist_count}")
                    else:
                        self._assist_hit_count += 1
                        
                        # Check if feed assist is actually working
                        if elapsed_time > 3.0 and not getattr(self, '_park_count_increased', False):
                            # 3 seconds passed and count never increased - feed assist not working
                            self.logger.error(f"Feed assist for slot {self._park_index} not working - count stayed at {current_assist_count}")
                            self._park_error = True  # Mark as error BEFORE resetting flag
                            self._park_in_progress = False
                            self._park_index = -1
                            return
                        
                        if self._assist_hit_count >= self.park_hit_count:
                            # Only complete if count actually increased
                            if getattr(self, '_park_count_increased', False):
                                self._complete_parking()
                            else:
                                self.logger.warning(f"Parking check completed but count never increased (stayed at {current_assist_count})")
                                # Mark as error and abort
                                self._park_error = True
                                self._park_in_progress = False
                            return
                        # Проверяем, что таймер не будет создаваться бесконечно
                        # если self.dwell уже запланирован, не вызываем его снова
                        if not hasattr(self, '_dwell_scheduled') or not self._dwell_scheduled:
                            self._dwell_scheduled = True
                            self.dwell(0.7, lambda: setattr(self, '_dwell_scheduled', False))

    def _complete_parking(self):
        if not self._park_in_progress:
            return
        self.logger.info(f"Parking completed for slot {self._park_index}")
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
            self.logger.info(f"Parking completion error: {str(e)}")
        finally:
            self._park_in_progress = False
            self._park_error = False  # Reset error flag
            self._park_is_toolchange = False
            self._park_previous_tool = -1
            self._park_index = -1
            if self.disable_assist_after_toolchange:
                self._feed_assist_index = -1

    def dwell(self, delay: float = 1.0, callback: Optional[Callable] = None):
        """Асинхронная пауза через reactor"""
        """Asynchronous pause through reactor"""
        if delay <= 0:
            if callback:
                try:
                    callback()
                except Exception as e:
                    self.logger.error(f"Error in dwell callback: {e}")
            return
        
        def timer_handler(event_time):
            if callback:
                try:
                    callback()
                except Exception as e:
                    self.logger.error(f"Error in dwell callback: {e}")
            return self.reactor.NEVER
        
        self.reactor.register_timer(timer_handler, self.reactor.monotonic() + delay)


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
            # Запрашиваем свежий статус перед выводом
            # Request fresh status before output
            def status_callback(response):
                if 'result' in response:
                    result = response['result']
                    # Нормализация данных о сушилке
                    if 'dryer_status' in result and isinstance(result['dryer_status'], dict):
                        result['dryer'] = result['dryer_status']
                    self._info.update(result)
                    # Выводим статус после обновления
                    self._output_status(gcmd)
            
            # Отправляем запрос статуса
            self.send_request({"method": "get_status"}, status_callback)
            
        except Exception as e:
            self.logger.info(f"Status command error: {str(e)}")
            gcmd.respond_raw(f"Error retrieving status: {str(e)}")
    
    def _output_status(self, gcmd):
        """Вывод статуса ACE (вызывается после получения данных)"""
        try:
            info = self._info
            output = []
            
            # Device Information
            output.append("=== ACE Device Status ===")
            output.append(f"Status: {info.get('status', 'unknown')}")
            
            # Device Info
            if 'model' in info:
                output.append(f"Model: {info.get('model', 'Unknown')}")
            if 'firmware' in info:
                output.append(f"Firmware: {info.get('firmware', 'Unknown')}")
            if 'boot_firmware' in info:
                output.append(f"Boot Firmware: {info.get('boot_firmware', 'Unknown')}")
            
            output.append("")
            
            # Dryer Status
            output.append("=== Dryer ===")
            # Проверяем оба ключа для совместимости
            dryer = info.get('dryer', {})
            if not dryer and 'dryer_status' in info:
                dryer = info.get('dryer_status', {})
            
            dryer_status = dryer.get('status', 'unknown') if isinstance(dryer, dict) else 'unknown'
            output.append(f"Status: {dryer_status}")
            if dryer_status == 'drying':
                output.append(f"Target Temperature: {dryer.get('target_temp', 0)}°C")
                output.append(f"Current Temperature: {info.get('temp', 0)}°C")
                duration = dryer.get('duration', 0)
                # Если duration в секундах, конвертируем в минуты
                if duration > 1000:  # Вероятно в секундах
                    duration = duration // 60
                output.append(f"Duration: {duration} minutes")
                remain_time = dryer.get('remain_time', 0)
                # Если remain_time в секундах, конвертируем в минуты
                if remain_time > 1000:  # Вероятно в секундах
                    remain_time = remain_time // 60
                if remain_time > 0:
                    hours = remain_time // 60
                    minutes = remain_time % 60
                    output.append(f"Remaining Time: {hours}h {minutes}m")
            else:
                output.append(f"Temperature: {info.get('temp', 0)}°C")
            
            output.append("")
            
            # Device Parameters
            output.append("=== Device Parameters ===")
            output.append(f"Fan Speed: {info.get('fan_speed', 0)} RPM")
            output.append(f"RFID Enabled: {'Yes' if info.get('enable_rfid', 0) else 'No'}")
            output.append(f"Feed Assist Count: {info.get('feed_assist_count', 0)}")
            cont_assist = info.get('cont_assist_time', 0.0)
            if cont_assist > 0:
                output.append(f"Continuous Assist Time: {cont_assist:.1f} ms")
            
            output.append("")
            
            # Slots Information
            output.append("=== Filament Slots ===")
            slots = info.get('slots', [])
            for slot in slots:
                index = slot.get('index', -1)
                status = slot.get('status', 'unknown')
                slot_type = slot.get('type', '')
                color = slot.get('color', [0, 0, 0])
                sku = slot.get('sku', '')
                rfid_status = slot.get('rfid', 0)
                
                output.append(f"Slot {index}:")
                output.append(f"  Status: {status}")
                if slot_type:
                    output.append(f"  Type: {slot_type}")
                if sku:
                    output.append(f"  SKU: {sku}")
                if color and isinstance(color, list) and len(color) >= 3:
                    output.append(f"  Color: RGB({color[0]}, {color[1]}, {color[2]})")
                rfid_text = {0: "Not found", 1: "Failed", 2: "Identified", 3: "Identifying"}.get(rfid_status, "Unknown")
                output.append(f"  RFID: {rfid_text}")
                output.append("")
            
            gcmd.respond_info("\n".join(output))
        except Exception as e:
            self.logger.info(f"Status output error: {str(e)}")
            gcmd.respond_raw(f"Error outputting status: {str(e)}")

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
            self.logger.info(f"Debug command error: {str(e)}")
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
            self.logger.info(f"Filament info error: {str(e)}")
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
        # Set parking flag BEFORE sending request to ensure timers see it
        self._park_in_progress = True
        self._park_error = False  # Reset error flag
        self._park_index = index
        self._assist_hit_count = 0
        self._park_start_time = self.reactor.monotonic()
        self._park_count_increased = False  # Track if count actually increased
        
        self.logger.info(f"Starting parking for slot {index}")
        
        def callback(response):
            if response.get('code', 0) != 0:
                if 'result' in response and 'msg' in response['result']:
                    self.logger.error(f"ACE Error starting feed assist: {response['result']['msg']}")
                else:
                    self.logger.error(f"ACE Error starting feed assist: {response.get('msg', 'Unknown error')}")
                # Reset parking flag on error since device won't start feeding
                self._park_in_progress = False
                self.logger.error(f"Parking aborted for slot {index} due to start_feed_assist error")
            else:
                self._last_assist_count = response.get('result', {}).get('feed_assist_count', 0)
                self.logger.info(f"Feed assist started for slot {index}, count: {self._last_assist_count}")
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
        # Use async dwell instead of blocking pdwell
        self.dwell((length / speed) + 0.1, lambda: None)

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
        if self.toolhead:
            self.toolhead.wait_moves()
        self.variables['ace_current_index'] = tool
        self._save_variable('ace_current_index', tool)

        def on_retract_complete(response):
            if response.get('code', 0) != 0:
                gcmd.respond_raw(f"ACE Error: {response.get('msg', 'Unknown error')}")
                self.logger.error(f"Retract failed for slot {was}")
                return
            
            # Calculate retract delay
            retract_delay = (self.toolchange_retract_length / self.retract_speed) + 1.0
            self.logger.info(f"Retract completed for slot {was}, waiting {retract_delay:.1f}s for completion")
            
            def after_retract_delay():
                self.logger.info(f"Retract delay complete, waiting for slot {was} to be ready")
                # Start waiting for slot to be ready after retraction
                self._wait_for_slot_ready_async(was, lambda: self._on_slot_ready_callback(tool, was, gcmd), self.reactor.monotonic())
            
            # Wait for retract to physically complete
            self.dwell(retract_delay, after_retract_delay)

        if was != -1:
            self.send_request({
                "method": "unwind_filament",
                "params": {
                    "index": was,
                    "length": self.toolchange_retract_length,
                    "speed": self.retract_speed
                }
            }, on_retract_complete)
        else:
            # Use G-code command for consistency
            self.gcode.run_script_from_command(f'ACE_PARK_TO_TOOLHEAD INDEX={tool}')
            if self.toolhead:
                self.toolhead.wait_moves()
           
            def after_park_delay():
                self.gcode.run_script_from_command(f'_ACE_POST_TOOLCHANGE FROM={was} TO={tool}')
                if self.toolhead:
                    self.toolhead.wait_moves()
                gcmd.respond_info(f"Tool changed from {was} to {tool}")
            
            # Monitor parking completion instead of fixed timeout
            def check_parking_completion(eventtime):
                # Check if parking failed
                if self._park_error:
                    self.logger.error(f"Parking failed for slot {tool} during toolchange")
                    gcmd.respond_raw(f"ACE Error: Parking failed for slot {tool}")
                    return self.reactor.NEVER
                
                # Check if parking completed (changed from True to False)
                if not self._park_in_progress:
                    self.logger.info(f"Parking completed for slot {tool}, executing post-toolchange")
                    after_park_delay()
                    return self.reactor.NEVER
                
                # Continue checking every 0.5 seconds
                return eventtime + 0.5
            
            # Start monitoring parking completion
            self.reactor.register_timer(check_parking_completion, self.reactor.monotonic() + 0.5)

    def _wait_for_slot_ready_async(self, index, on_ready, event_time):
        """Asynchronous waiting for slot to be ready"""
        current_status = self._info['slots'][index]['status']
        self.logger.info(f"Waiting for slot {index} to be ready, current status: {current_status}")
        
        if current_status == 'ready':
            self.logger.info(f"Slot {index} already ready, proceeding immediately")
            on_ready()
            return
        
        # Register timer to check status
        start_check_time = self.reactor.monotonic()
        max_wait = 10.0  # Maximum 10 seconds to wait for slot to be ready
        
        def check_status_timer(eventtime):
            current_status = self._info['slots'][index]['status']
            elapsed = eventtime - start_check_time
            self.logger.debug(f"Checking slot {index} status: {current_status}, elapsed: {elapsed:.1f}s")
            
            if current_status == 'ready':
                self.logger.info(f"Slot {index} is now ready after {elapsed:.1f}s")
                on_ready()
                return self.reactor.NEVER
            
            # Timeout check
            if elapsed > max_wait:
                self.logger.error(f"Timeout waiting for slot {index} to be ready (status: {current_status})")
                return self.reactor.NEVER
            
            return eventtime + 1.0
        self.reactor.register_timer(check_status_timer, event_time + 1.0)

    def _on_slot_ready_callback(self, tool, was, gcmd):
        """Callback when slot is ready after retraction"""
        self.logger.info(f"Slot ready callback: tool={tool}, was={was}")
        
        if tool == -1:
            # Unloading only, no new tool to park
            self.logger.info(f"Unloading complete, executing post-toolchange")
            self.gcode.run_script_from_command(f'_ACE_POST_TOOLCHANGE FROM={was} TO={tool}')
            if self.toolhead:
                self.toolhead.wait_moves()
            gcmd.respond_info(f"Tool unloaded (was {was})")
        else:
            # Park new tool using G-code command (like in working version)
            self.logger.info(f"Starting parking of new tool {tool} using G-code command")
            self.gcode.run_script_from_command(f'ACE_PARK_TO_TOOLHEAD INDEX={tool}')
            if self.toolhead:
                self.toolhead.wait_moves()
                    
            def after_park_delay():
                self.logger.info(f"Parking delay complete for slot {tool}, executing post-toolchange")
                self.gcode.run_script_from_command(f'_ACE_POST_TOOLCHANGE FROM={was} TO={tool}')
                if self.toolhead:
                    self.toolhead.wait_moves()
                gcmd.respond_info(f"Tool changed from {was} to {tool}")
            
            # Monitor parking completion instead of fixed timeout
            def check_parking_completion(eventtime):
                # Check if parking failed
                if self._park_error:
                    self.logger.error(f"Parking failed for slot {tool} during toolchange callback")
                    gcmd.respond_raw(f"ACE Error: Parking failed for slot {tool}")
                    return self.reactor.NEVER
                
                # Check if parking completed (changed from True to False)
                if not self._park_in_progress:
                    self.logger.info(f"Parking completed for slot {tool}, executing post-toolchange")
                    after_park_delay()
                    return self.reactor.NEVER
                
                # Continue checking every 0.5 seconds
                return eventtime + 0.5
            
            # Start monitoring parking completion
            self.reactor.register_timer(check_parking_completion, self.reactor.monotonic() + 0.5)

    def cmd_ACE_SET_INFINITY_SPOOL_ORDER(self, gcmd):
        """Set the order of slots for infinity spool mode"""
        order_str = gcmd.get('ORDER', '')
        
        if not order_str:
            gcmd.respond_raw("Error: ORDER parameter is required")
            gcmd.respond_info("Usage: ACE_SET_INFINITY_SPOOL_ORDER ORDER=\"0,1,2,3\"")
            gcmd.respond_info("Use 'none' for empty slots, e.g.: ORDER=\"0,1,none,3\"")
            return
        
        # Parse order string
        try:
            order_list = [item.strip().lower() for item in order_str.split(',')]
            
            # Validate order
            if len(order_list) != 4:
                gcmd.respond_raw(f"Error: Order must contain exactly 4 items, got {len(order_list)}")
                return
            
            # Validate each item
            valid_slots = []
            for i, item in enumerate(order_list):
                if item == 'none':
                    valid_slots.append('none')
                else:
                    try:
                        slot_num = int(item)
                        if slot_num < 0 or slot_num > 3:
                            gcmd.respond_raw(f"Error: Slot number {slot_num} at position {i+1} is out of range (0-3)")
                            return
                        valid_slots.append(slot_num)
                    except ValueError:
                        gcmd.respond_raw(f"Error: Invalid value '{item}' at position {i+1}. Use slot number (0-3) or 'none'")
                        return
            
            # Save order as comma-separated string
            order_str_saved = ','.join(str(s) if s != 'none' else 'none' for s in valid_slots)
            self._save_variable('ace_infsp_order', order_str_saved)
            self._save_variable('ace_infsp_position', 0)  # Reset position to start
            
            gcmd.respond_info(f"Infinity spool order set: {order_str_saved}")
            gcmd.respond_info(f"Order: {valid_slots}")
            
        except Exception as e:
            self.logger.error(f"Error setting infinity spool order: {str(e)}")
            gcmd.respond_raw(f"Error: {str(e)}")

    def cmd_ACE_INFINITY_SPOOL(self, gcmd):
        was = self.variables.get('ace_current_index', -1)
        infsp_status = self.infinity_spool_mode
        
        if not infsp_status:
            gcmd.respond_info(f"ACE_INFINITY_SPOOL disabled")
            gcmd.respond_info(f"ACE_INFINITY_SPOOL status {infsp_status}")
            return
        if was == -1:
            gcmd.respond_info(f"Tool is not set")
            return
        
        # Get order from variables
        order_str = self.variables.get('ace_infsp_order', '')
        if not order_str:
            gcmd.respond_raw("Error: Infinity spool order not set. Use ACE_SET_INFINITY_SPOOL_ORDER ORDER=\"...\" first")
            gcmd.respond_info("Example: ACE_SET_INFINITY_SPOOL_ORDER ORDER=\"0,1,2,3\"")
            return
        
        # Parse order
        try:
            order_list = []
            for item in order_str.split(','):
                item = item.strip().lower()
                if item == 'none':
                    order_list.append('none')
                else:
                    order_list.append(int(item))
        except Exception as e:
            self.logger.error(f"Error parsing infinity spool order: {str(e)}")
            gcmd.respond_raw(f"Error: Invalid order format: {order_str}")
            return
        
        # Get current position in order (if set, otherwise find current slot)
        saved_position = self.variables.get('ace_infsp_position', -1)
        
        # Find current slot position in order
        current_order_index = -1
        
        # If we have a saved position, use it (more reliable)
        if saved_position >= 0 and saved_position < len(order_list):
            # Verify that saved position matches current slot
            if order_list[saved_position] != 'none' and order_list[saved_position] == was:
                current_order_index = saved_position
            else:
                # Saved position doesn't match, find current slot
                self.logger.warning(f"Saved position {saved_position} doesn't match current slot {was}, searching...")
                for i, slot in enumerate(order_list):
                    if slot != 'none' and slot == was:
                        current_order_index = i
                        break
        else:
            # No saved position, find current slot in order
            for i, slot in enumerate(order_list):
                if slot != 'none' and slot == was:
                    current_order_index = i
                    break
        
        if current_order_index == -1:
            # Current slot not found in order, start from beginning
            self.logger.warning(f"Current slot {was} not found in order, starting from beginning")
            current_order_index = -1
        
        # Find next valid slot (skip 'none'), cycling through order
        tool = None
        new_position = None
        
        # Search through entire order (max one full cycle)
        for i in range(len(order_list)):
            next_index = (current_order_index + 1 + i) % len(order_list)
            next_slot = order_list[next_index]
            
            if next_slot == 'none':
                continue  # Skip empty slots
            
            # Check if slot is ready
            if self._info['slots'][next_slot]['status'] == 'ready':
                tool = next_slot
                new_position = next_index
                break
        
        if tool is None:
            gcmd.respond_raw("Error: No more ready slots available in order")
            self.logger.error("INFINITY_SPOOL: No ready slots found in order")
            return
        
        # CRITICAL: Check if new slot is ready before proceeding
        if self._info['slots'][tool]['status'] != 'ready':
            gcmd.respond_raw(f"ACE Error: Slot {tool} is not ready (status: {self._info['slots'][tool]['status']})")
            self.logger.error(f"INFINITY_SPOOL aborted: slot {tool} not ready")
            return
        
        self.logger.info(f"INFINITY_SPOOL: changing from {was} to {tool} (no retract - filament exhausted)")
        
        # Pre-processing
        self.gcode.run_script_from_command(f"_ACE_PRE_INFINITYSPOOL")
        if self.toolhead:
            self.toolhead.wait_moves()
        
        # Track parking success
        parking_success = {'completed': False}
        
        def on_park_complete():
            if parking_success['completed']:
                return  # Already processed
            parking_success['completed'] = True
            
            self.logger.info(f"INFINITY_SPOOL: parking complete for slot {tool}, executing post-processing")
            self.gcode.run_script_from_command(f'_ACE_POST_INFINITYSPOOL')
            if self.toolhead:
                self.toolhead.wait_moves()
            
            # Save variables only on success
            self._save_variable('ace_current_index', tool)
            self._save_variable('ace_infsp_position', new_position)
            gcmd.respond_info(f"Tool changed from {was} to {tool}")
        
        def on_park_error():
            if parking_success['completed']:
                return  # Already processed
            parking_success['completed'] = True
            
            self.logger.error(f"INFINITY_SPOOL: parking failed for slot {tool}")
            gcmd.respond_raw(f"ACE Error: Failed to park slot {tool}")
            # Don't save variables on error
        
        # Start parking with monitoring
        self.logger.info(f"INFINITY_SPOOL: starting parking for slot {tool} with monitoring")
        
        # Set up monitoring for parking completion
        self._park_in_progress = True
        self._park_error = False
        self._park_index = tool
        self._assist_hit_count = 0
        self._park_start_time = self.reactor.monotonic()
        self._park_count_increased = False
        
        # Start parking via G-code command
        self.gcode.run_script_from_command(f'ACE_PARK_TO_TOOLHEAD INDEX={tool}')
        if self.toolhead:
            self.toolhead.wait_moves()
            
        # Monitor parking with timeout
        max_wait_time = 30.0
        start_time = self.reactor.monotonic()
        
        def check_parking_status(eventtime):
            elapsed = eventtime - start_time
            
            # Check for error
            if self._park_error:
                on_park_error()
                return self.reactor.NEVER
            
            # Check for completion
            if not self._park_in_progress:
                on_park_complete()
                return self.reactor.NEVER
            
            # Check for timeout
            if elapsed > max_wait_time:
                self.logger.error(f"INFINITY_SPOOL: parking timeout after {elapsed:.1f}s")
                self._park_in_progress = False
                self._park_error = True
                on_park_error()
                return self.reactor.NEVER
            
            # Continue monitoring
            return eventtime + 0.5
        
        # Register monitoring timer
        self.reactor.register_timer(check_parking_status, self.reactor.monotonic() + 0.5)


def load_config(config):
    return ValgAce(config)