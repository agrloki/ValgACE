# File: ace.py — ValgAce module for Klipper

import logging
import json
import struct
import queue
from typing import Optional, Dict, Any, Callable

# Check for required libraries and raise an error if they are not available
try:
    import serial
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
        
        # Initialize logger first
        self.logger = logging.getLogger('ace')
        self._name = 'ace'
        
        # Initialize filament sensor
        self.filament_sensor_name = config.get('filament_sensor', None)
        self.filament_sensor = None
        if self.filament_sensor_name:
            try:
                self.filament_sensor = self.printer.lookup_object(f'filament_switch_sensor {self.filament_sensor_name}')
                self.logger.info(f"Filament sensor '{self.filament_sensor_name}' found and connected")
            except Exception as e:
                self.logger.warning(f"Filament sensor '{self.filament_sensor_name}' not found: {str(e)}")
                self.filament_sensor = None
        
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
        # Устройство выбирается только из конфигурации
        # Device is selected only from configuration
        self.serial_name = config.get('serial', '/dev/ttyACM0')

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
        self.ins_spool_work = False  # Флаг выполнения операции ACE_INFINITY_SPOOL
        
        # Новые параметры для агрессивной парковки
        self.aggressive_parking = config.getboolean('aggressive_parking', False)
        self.max_parking_distance = config.getint('max_parking_distance', 100)
        self.parking_speed = config.getint('parking_speed', 10)
        # Дополнительное время к таймауту парковки (в секундах)
        self.extended_park_time = config.getint('extended_park_time', 10)
        # Максимальное время ожидания парковки (в секундах)
        self.max_parking_timeout = config.getint('max_parking_timeout', 60)

        # Макрос для паузы печати (по умолчанию PAUSE)
        self.pause_macro_name = config.get('set_pause_macro_name', 'PAUSE')

        # Добавляем возможность привязки к сенсору филамента
        # Optional filament sensor integration

        # Состояние устройства
        # Device state
        self._info = self._get_default_info()
        self._callback_map = {}
        
        # Отображение индексов в слоты (по умолчанию 0→0, 1→1, 2→2, 3→3)
        # Index to slot mapping (default: 0→0, 1→1, 2→2, 3→3)
        self.index_to_slot = [0, 1, 2, 3]
        self._request_id = 0
        self._connected = False
        self._manually_disconnected = False  # Track if disconnected by user command
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
        self._park_start_time = 0  # Initialize to prevent AttributeError
        # Флаги для агрессивной парковки с сенсором
        self._sensor_parking_active = False  # True когда используется сенсорная парковка
        self._sensor_parking_completed = False  # True когда сенсор успешно сработал

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
        # Инициализация флага для отслеживания увеличения счетчика парковки
        self._park_count_increased = False
        # Флаг для предотвращения рекурсивного вызова _ACE_POST_TOOLCHANGE
        self._post_toolchange_running = False
        # Ссылки на активные таймеры для корректной очистки (предотвращение утечек)
        self._park_monitor_timer = None
        self._sensor_monitor_timer = None
        # Флаги и счётчики для обработки обрыва связи
        self._connection_lost = False
        self._reconnect_attempts = 0
        self._max_reconnect_attempts = 10

        # Infinity Spool Auto-trigger state
        self.infsp_empty_detected = False        # Флаг обнаружения empty статуса
        self.infsp_debounce_timer = None         # Reactor timer для debounce
        self.infsp_sensor_monitor_timer = None   # Reactor timer для мониторинга датчика
        self.infsp_last_active_status = None     # Последний известный статус активного слота

        # Infinity Spool Auto-trigger configuration parameters
        self.infinity_spool_debounce = config.getfloat('infinity_spool_debounce', 2.0)
        self.infinity_spool_pause_on_no_sensor = config.getboolean('infinity_spool_pause_on_no_sensor', True)

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

    def _init_slot_mapping(self):
        """
        Инициализация отображения индексов в слоты из переменных.
        Если переменные отсутствуют, устанавливаются дефолтные значения (0→0, 1→1, 2→2, 3→3).
        Initialize index to slot mapping from variables.
        If variables are missing, default values are set (0→0, 1→1, 2→2, 3→3).
        """
        for i in range(4):
            var_name = f'ace_index{i}_to_slot'
            slot_value = self.variables.get(var_name, None)
            
            if slot_value is None:
                # Переменная отсутствует, создаём с дефолтным значением
                # Variable missing, create with default value
                self.index_to_slot[i] = i
                self._save_variable(var_name, i)
                self.logger.info(f"Slot mapping: initialized {var_name} = {i}")
            else:
                # Переменная существует, проверяем и используем её значение
                # Variable exists, validate and use its value
                try:
                    slot_int = int(slot_value)
                    if 0 <= slot_int <= 3:
                        self.index_to_slot[i] = slot_int
                        self.logger.info(f"Slot mapping: loaded {var_name} = {slot_int}")
                    else:
                        # Значение вне диапазона, сбрасываем в дефолт
                        # Value out of range, reset to default
                        self.logger.warning(f"Slot mapping: {var_name} = {slot_value} out of range (0-3), resetting to {i}")
                        self.index_to_slot[i] = i
                        self._save_variable(var_name, i)
                except (ValueError, TypeError):
                    # Ошибка преобразования, сбрасываем в дефолт
                    # Conversion error, reset to default
                    self.logger.warning(f"Slot mapping: invalid {var_name} = {slot_value}, resetting to {i}")
                    self.index_to_slot[i] = i
                    self._save_variable(var_name, i)
        
        self.logger.info(f"Slot mapping initialized: {self.index_to_slot}")

    def _get_real_slot(self, index: int) -> int:
        """
        Преобразовать индекс (из Klipper) в реальный слот устройства.
        Convert index (from Klipper) to real device slot.
        
        :param index: Индекс из Klipper (0-3)
        :return: Реальный слот устройства (0-3)
        """
        if 0 <= index <= 3:
            return self.index_to_slot[index]
        return index

    def _set_slot_mapping(self, index: int, slot: int) -> bool:
        """
        Установить отображение индекса в слот.
        Set index to slot mapping.
        
        :param index: Индекс (0-3)
        :param slot: Слот (0-3)
        :return: True если успешно, False если ошибка
        """
        if not (0 <= index <= 3):
            return False
        if not (0 <= slot <= 3):
            return False
        
        self.index_to_slot[index] = slot
        var_name = f'ace_index{index}_to_slot'
        self._save_variable(var_name, slot)
        self.logger.info(f"Slot mapping updated: index {index} → slot {slot}")
        return True

    def _reset_slot_mapping(self):
        """
        Сбросить отображение слотов в дефолтные значения (0→0, 1→1, 2→2, 3→3).
        Reset slot mapping to default values (0→0, 1→1, 2→2, 3→3).
        """
        for i in range(4):
            self.index_to_slot[i] = i
            var_name = f'ace_index{i}_to_slot'
            self._save_variable(var_name, i)
        self.logger.info("Slot mapping reset to defaults: [0, 1, 2, 3]")

    def _validate_index(self, index: int) -> tuple:
        """
        Валидация INDEX и преобразование в реальный слот.
        Validate INDEX and convert to real slot.
        
        :param index: Индекс из Klipper (0-3)
        :return: Кортеж (real_slot, error_message)
                 - real_slot: реальный слот устройства (0-3) если валиден, иначе -1
                 - error_message: сообщение об ошибке если INDEX невалиден, иначе None
        """
        # Проверка диапазона INDEX
        if not isinstance(index, int):
            return -1, f"INDEX must be integer, got {type(index).__name__}"
        
        if index < 0 or index > 3:
            return -1, f"INDEX {index} out of range (must be 0-3)"
        
        # Преобразование через маппинг
        real_slot = self.index_to_slot[index]
        
        self.logger.debug(f"INDEX validation: {index} → Slot {real_slot}")
        return real_slot, None

    def _validate_slot_status(self, real_slot: int, required_status: str = 'ready') -> tuple:
        """
        Проверка статуса слота.
        Check slot status.
        
        :param real_slot: Реальный слот устройства (0-3)
        :param required_status: Требуемый статус ('ready', 'empty', etc.)
        :return: Кортеж (is_valid, error_message)
                 - is_valid: True если слот имеет требуемый статус
                 - error_message: сообщение об ошибке если статус не соответствует
        """
        # Проверка подключения
        if not self._connected:
            return False, "ACE device not connected"
        
        # Проверка диапазона слота
        if real_slot < 0 or real_slot > 3:
            return False, f"Invalid slot {real_slot} (must be 0-3)"
        
        # Получение текущего статуса слота
        try:
            slots = self._info.get('slots', [])
            if real_slot >= len(slots):
                return False, f"Slot {real_slot} not found in device status"
            
            slot_info = slots[real_slot]
            current_status = slot_info.get('status', 'unknown')
            
            if current_status != required_status:
                return False, f"Slot {real_slot} status is '{current_status}', expected '{required_status}'"
            
            return True, None
            
        except Exception as e:
            self.logger.error(f"Error checking slot {real_slot} status: {str(e)}")
            return False, f"Error checking slot status: {str(e)}"

    def _validate_index_for_operation(self, index: int, operation_name: str = "operation") -> tuple:
        """
        Комплексная валидация INDEX для операции (проверка INDEX + статуса слота).
        Comprehensive INDEX validation for operation (INDEX check + slot status check).
        
        :param index: Индекс из Klipper (0-3)
        :param operation_name: Название операции для сообщений об ошибках
        :return: Кортеж (real_slot, error_message)
                 - real_slot: реальный слот устройства если валиден, иначе None
                 - error_message: сообщение об ошибке если валидация не прошла, иначе None
        """
        # Валидация INDEX
        real_slot, error = self._validate_index(index)
        if error:
            return None, error
        
        # Проверка подключения устройства
        if not self._connected:
            return None, "ACE device not connected"
        
        return real_slot, None

    def _is_slot_ready(self, index: int) -> bool:
        """
        Проверить готовность слота по индексу.
        Check if slot is ready by index.
        
        :param index: Индекс слота (0-3)
        :return: True если слот готов, иначе False
        """
        try:
            slots = self._info.get('slots', [])
            if index < 0 or index >= len(slots):
                return False
            slot_info = slots[index]
            return slot_info.get('status', 'unknown') == 'ready'
        except Exception as e:
            self.logger.error(f"Error checking slot {index} readiness: {str(e)}")
            return False

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
            ('ACE_CHECK_FILAMENT_SENSOR', self.cmd_ACE_CHECK_FILAMENT_SENSOR, "Check filament sensor status"),
            ('ACE_DISCONNECT', self.cmd_ACE_DISCONNECT, "Force disconnect device"),
            ('ACE_CONNECT', self.cmd_ACE_CONNECT, "Connect to device"),
            ('ACE_CONNECTION_STATUS', self.cmd_ACE_CONNECTION_STATUS, "Check connection status"),
            ('ACE_RECONNECT', self.cmd_ACE_RECONNECT, "Manually reset connection and clear error flags"),
            ('ACE_GET_HELP', self.cmd_ACE_GET_HELP, "Show all available ACE commands with descriptions"),
            ('ACE_GET_SLOTMAPPING', self.cmd_ACE_GET_SLOTMAPPING, "Get current slot mapping"),
            ('ACE_SET_SLOTMAPPING', self.cmd_ACE_SET_SLOTMAPPING, "Set slot mapping"),
            ('ACE_RESET_SLOTMAPPING', self.cmd_ACE_RESET_SLOTMAPPING, "Reset slot mapping to defaults"),
            ('ACE_GET_CURRENT_INDEX', self.cmd_ACE_GET_CURRENT_INDEX, "Get current tool index"),
            ('ACE_SET_CURRENT_INDEX', self.cmd_ACE_SET_CURRENT_INDEX, "Set current tool index (for error recovery)"),
        ]
        for name, func, desc in commands:
            self.gcode.register_command(name, func, desc=desc)

    def _connect_check(self, eventtime):
        # Only auto-connect if the device is not connected and hasn't been manually disconnected
        if not self._connected and not self._manually_disconnected:
            # Try to connect
            self._connect()
        return eventtime + 1.0

    def _connect(self) -> bool:
        if self._connected:
            return True
            
        # Ensure any existing connection is properly closed
        if self._serial and self._serial.is_open:
            try:
                self._serial.close()
            except:
                pass
            self._serial = None
            
        for attempt in range(self._max_connection_attempts):
            try:
                self.logger.info(f"Attempting to connect to ACE at {self.serial_name} (attempt {attempt + 1}/{self._max_connection_attempts})")
                
                self._serial = serial.Serial(
                    port=self.serial_name,
                    baudrate=self.baud,
                    timeout=0,
                    write_timeout=self._write_timeout
                )
                
                if self._serial.is_open:
                    self._connected = True
                    self._info['status'] = 'ready'
                    # Сбрасываем счётчик попыток при успешном подключении
                    self._reconnect_attempts = 0
                    self._connection_lost = False
                    self.logger.info(f"Connected to ACE at {self.serial_name}")

                    def info_callback(response):
                        res = response['result']
                        self.logger.info(f"Device info: {res.get('model', 'Unknown')} {res.get('firmware', 'Unknown')}")
                        self.gcode.respond_info(f"Connected {res.get('model', 'Unknown')} {res.get('firmware', 'Unknown')}")

                    self.send_request({"method": "get_info"}, info_callback)

                    # Register timers if not already registered
                    if self._reader_timer is None:
                        self._reader_timer = self.reactor.register_timer(self._reader_loop, self.reactor.NOW)
                    if self._writer_timer is None:
                        self._writer_timer = self.reactor.register_timer(self._writer_loop, self.reactor.NOW)

                    self.logger.info("Connection established successfully")
                    return True
                else:
                    # Close the serial port if it wasn't opened properly
                    if self._serial:
                        self._serial.close()
                        self._serial = None
            except SerialException as e:
                self.logger.info(f"Connection attempt {attempt + 1} failed: {str(e)}")
                if self._serial:
                    try:
                        self._serial.close()
                    except:
                        pass
                    self._serial = None
                self.dwell(1.0, lambda: None)
            except Exception as e:
                self.logger.error(f"Unexpected error during connection: {str(e)}")
                if self._serial:
                    try:
                        self._serial.close()
                    except:
                        pass
                    self._serial = None
                self.dwell(1.0, lambda: None)
                
        self.logger.info("Failed to connect to ACE device")
        return False

    def _disconnect(self):
        """Gracefully disconnect from the device and stop all timers"""
        if not self._connected:
            return
            
        self.logger.info("Disconnecting from ACE device...")
        
        # Stop all timers
        if self._reader_timer:
            self.reactor.unregister_timer(self._reader_timer)
            self._reader_timer = None
        if self._writer_timer:
            self.reactor.unregister_timer(self._writer_timer)
            self._writer_timer = None
            
        # Close serial connection
        try:
            if self._serial and self._serial.is_open:
                self._serial.close()
        except Exception as e:
            self.logger.error(f"Error closing serial connection: {str(e)}")
        finally:
            self._serial = None
        
        # Update connection status
        self._connected = False
        self._info['status'] = 'disconnected'
        
        # Clear any pending requests
        try:
            while not self._queue.empty():
                _, callback = self._queue.get_nowait()
                if callback:
                    try:
                        callback({'error': 'Device disconnected'})
                    except Exception as e:
                        self.logger.debug(f"Error in callback during disconnect: {str(e)}")
        except Exception as e:
            self.logger.debug(f"Error clearing request queue: {str(e)}")
        
        # Clear callback map
        self._callback_map.clear()
        
        self.logger.info("ACE device disconnected successfully")

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
        
        # Инициализация отображения слотов
        # Initialize slot mapping
        self._init_slot_mapping()

    def _handle_disconnect(self):
        # When klipper disconnects, reset the manually disconnected flag so auto-reconnect can work after restart
        self._manually_disconnected = False

        # Проверяем состояние печати и вызываем паузу если нужно
        printer_state = self._get_printer_state()
        if printer_state == 'printing':
            self.logger.info(f"Klipper disconnect detected during printing, triggering {self.pause_macro_name}")
            try:
                self.gcode.run_script_from_command(self.pause_macro_name)
            except Exception as e:
                self.logger.error(f"Error triggering {self.pause_macro_name} during klipper disconnect: {str(e)}")

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
            
            # remain_time всегда приходит в секундах - конвертируем в минуты
            remain_time_raw = dryer_normalized.get('remain_time', 0)
            if remain_time_raw > 0:
                dryer_normalized['remain_time'] = remain_time_raw / 60  # Сохраняем дробную часть для секунд
            
            # duration всегда приходит в минутах - оставляем как есть
            # (ничего не делаем, уже в правильном формате)
        else:
            dryer_normalized = {}
        
        # Получаем статус сенсора филамента, если он настроен
        filament_sensor_status = None
        if self.filament_sensor:
            try:
                filament_sensor_status = self.filament_sensor.get_status(eventtime)
            except Exception as e:
                self.logger.warning(f"Error getting filament sensor status: {str(e)}")
                filament_sensor_status = {"filament_detected": False, "enabled": False}
        
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
            'slots': self._info.get('slots', []),
            'filament_sensor': filament_sensor_status,
            'slot_mapping': self.index_to_slot.copy()  # Отображение индексов в слоты
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
            
            # ОТЛАДКА: Выводим сырой JSON для всех ответов со статусом
            # Проверяем наличие данных о сушилке как признак ответа на get_status
            if 'dryer' in result or 'dryer_status' in result or 'slots' in result:
#                self.logger.info(f"RAW JSON response from device (get_status): {json.dumps(response, indent=2)}")
                if 'dryer' in result or 'dryer_status' in result:
                    dryer_data = result.get('dryer') or result.get('dryer_status', {})
#                    self.logger.info(f"RAW dryer data: {json.dumps(dryer_data, indent=2)}")
            
            # Нормализация данных о сушилке: если приходит dryer_status, сохраняем также как dryer
            if 'dryer_status' in result and isinstance(result['dryer_status'], dict):
                result['dryer'] = result['dryer_status']
            self._info.update(result)
            
            # Infinity Spool Auto-trigger: проверка empty статуса при печати
            # ВАЖНО: Не запускать мониторинг если уже идёт смена слота (ins_spool_work=True)
            if self.infinity_spool_mode and self._is_printer_printing() and not self.ins_spool_work:
                if self._check_slot_empty_status():
                    self.logger.info(f"_handle_response: Starting empty slot monitoring, ins_spool_work={self.ins_spool_work}")
                    self._start_empty_slot_monitoring()
            
            if self._park_in_progress:
                current_status = result.get('status', 'unknown')
                current_assist_count = result.get('feed_assist_count', 0)
                elapsed_time = self.reactor.monotonic() - self._park_start_time

                # Определяем режим парковки
                parking_mode = "sensor" if self._sensor_parking_active else ("traditional" if self._sensor_parking_completed else "normal")
                self.logger.debug(f"Parking check ({parking_mode}): slot {self._park_index}, count={current_assist_count}, " +
                                f"last={self._last_assist_count}, hits={self._assist_hit_count}, elapsed={elapsed_time:.1f}s")
                
                # Skip count monitoring during sensor-based parking - it has its own timer
                # Sensor-based parking is managed by _monitor_filament_sensor_for_parking()
                if self._sensor_parking_active:
                    self.logger.debug(f"Skipping count check during sensor-based parking for slot {self._park_index}")
                    return
                
                if current_status == 'ready':
                    if current_assist_count != self._last_assist_count:
                        self._last_assist_count = current_assist_count
                        self._assist_hit_count = 0
                        # Mark that count has increased at least once
                        if current_assist_count > 0:
                            self._park_count_increased = True
                            self.logger.info(f"Feed assist working for slot {self._park_index}, count: {current_assist_count}")
                    else:
                        self._assist_hit_count += 1

                        # Check if feed assist is actually working
                        # BUT: Skip this check for sensor-based parking - count won't change until sensor triggers
                        if not self._sensor_parking_active and elapsed_time > 3.0 and not self._park_count_increased:
                            # 3 seconds passed and count never increased - feed assist not working
                            self.logger.error(f"Feed assist for slot {self._park_index} not working - count stayed at {current_assist_count}")
                            self._park_error = True  # Mark as error BEFORE resetting flag
                            self._park_in_progress = False
                            self._park_index = -1
                            # Сбрасываем флаги сенсорной парковки
                            self._sensor_parking_active = False
                            self._sensor_parking_completed = False
                            return
                        
                        if self._assist_hit_count >= self.park_hit_count:
                            # Only complete if count actually increased
                            if self._park_count_increased:
                                self._complete_parking()
                            else:
                                self.logger.warning(f"Parking check completed but count never increased (stayed at {current_assist_count})")
                                # Mark as error and abort
                                self._park_error = True
                                self._park_in_progress = False
                                # Сбрасываем флаги сенсорной парковки
                                self._sensor_parking_active = False
                                self._sensor_parking_completed = False
                            return
                        # Проверяем, что таймер не будет создаваться бесконечно
                        # если self.dwell уже запланирован, не вызываем его снова
                        if not self._dwell_scheduled:
                            self._dwell_scheduled = True
                            self.dwell(0.7, lambda: setattr(self, '_dwell_scheduled', False))

    def _complete_parking(self):
        if not self._park_in_progress:
            return
        self.logger.info(f"Parking completed for slot {self._park_index}")
        
        # Останавливаем feed assist для указанного слота
        def stop_feed_assist_callback(response):
            if response.get('code', 0) != 0:
                self.logger.warning(f"Warning stopping feed assist after parking: {response.get('msg', 'Unknown error')}")
            else:
                self.logger.info(f"Feed assist stopped successfully after parking for slot {self._park_index}")
        
        self.send_request({
            "method": "stop_feed_assist",
            "params": {"index": self._park_index}
        }, stop_feed_assist_callback)
        
        # Если это была смена инструмента, выполняем макрос пост-обработки
        if self._park_is_toolchange:
            self.logger.info(f"Executing post-toolchange macro: FROM={self._park_previous_tool} TO={self._park_index}")
            # Вызываем соответствующий POST-макрос в зависимости от режима
            if self.ins_spool_work:
                self.gcode.run_script_from_command(
                    f'_ACE_POST_INFINITYSPOOL FROM={self._park_previous_tool} TO={self._park_index}'
                )
            else:
                self.gcode.run_script_from_command(
                    f'_ACE_POST_TOOLCHANGE FROM={self._park_previous_tool} TO={self._park_index}'
                )
        
        self._park_in_progress = False
        self._park_error = False  # Reset error flag
        self._park_is_toolchange = False
        self._park_previous_tool = -1
        self._park_index = -1
        # Сбрасываем флаги сенсорной парковки
        self._sensor_parking_active = False
        self._sensor_parking_completed = False
        # Очищаем ссылки на таймеры для предотвращения утечек
        self._park_monitor_timer = None
        self._sensor_monitor_timer = None
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


    def _get_printer_state(self):
        """Получить текущее состояние печати"""
        eventtime = self.reactor.monotonic()
        try:
            print_stats = self.printer.lookup_object('print_stats')
            ps_status = print_stats.get_status(eventtime)
            return ps_status.get('state', 'unknown')
        except Exception:
            return 'unknown'

    def _pause_print_if_needed(self):
        """Вызвать паузу если принтер печатает"""
        printer_state = self._get_printer_state()
        if printer_state == 'printing':
            self.logger.info(f"Print in progress, triggering {self.pause_macro_name}")
            try:
                self.gcode.run_script_from_command(self.pause_macro_name)
            except Exception as e:
                self.logger.error(f"Error triggering {self.pause_macro_name}: {str(e)}")

    def _notify_connection_lost(self):
        """Уведомить пользователя о потере связи и вызвать паузу при печати"""
        self.gcode.respond_raw("ACE: CRITICAL - Connection lost after maximum attempts")
        self._pause_print_if_needed()

    def _reconnect(self):
        # Проверяем, не достигнут ли лимит попыток
        if self._connection_lost:
            return  # Уже превышен лимит, не пытаемся подключиться
        
        self._reconnect_attempts += 1
        
        if self._reconnect_attempts > self._max_reconnect_attempts:
            # Превышен лимит попыток
            self._connection_lost = True
            self._notify_connection_lost()
            return
        
        # Уведомляем о попытке переподключения
        self.logger.info(f"Attempting to reconnect to ACE (attempt {self._reconnect_attempts}/{self._max_reconnect_attempts})")
        
        # During automatic reconnect, reset the manually disconnected flag
        self._manually_disconnected = False
        self._disconnect()
        self.dwell(1.0, lambda: None)
        self._connect()

    def _reset_connection(self):
        # Во время сброса соединения также проверяем лимит попыток
        if self._connection_lost:
            return  # Уже превышен лимит
        
        self._reconnect_attempts += 1
        
        if self._reconnect_attempts > self._max_reconnect_attempts:
            # Превышен лимит попыток
            self._connection_lost = True
            self._notify_connection_lost()
            return
        
        self.logger.info(f"Resetting ACE connection (attempt {self._reconnect_attempts}/{self._max_reconnect_attempts})")
        
        # During connection reset, reset the manually disconnected flag
        self._manually_disconnected = False
        self._disconnect()
        self.dwell(1.0, lambda: None)
        self._connect()

    def cmd_ACE_STATUS(self, gcmd):
        try:
            # Запрашиваем свежий статус перед выводом
            # Request fresh status before output
            def status_callback(response):
                # ОТЛАДКА: Выводим сырой JSON ответа
                self.logger.info(f"RAW JSON response in ACE_STATUS callback: {json.dumps(response, indent=2)}")
                
                if 'result' in response:
                    result = response['result']
                    # ОТЛАДКА: Выводим данные о сушилке
                    if 'dryer' in result or 'dryer_status' in result:
                        dryer_data = result.get('dryer') or result.get('dryer_status', {})
                        self.logger.info(f"RAW dryer data in callback: {json.dumps(dryer_data, indent=2)}")
                    
                    # ��ормализация данных о сушилке
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
                # duration всегда в минутах
                duration = dryer.get('duration', 0)
                output.append(f"Duration: {duration} minutes")
                
                # remain_time всегда приходит в секундах - конвертируем в минуты
                remain_time_raw = dryer.get('remain_time', 0)
                # Конвертируем секунды в минуты (с сохранением дробной части для секунд)
                remain_time = remain_time_raw / 60 if remain_time_raw > 0 else 0
                
                if remain_time > 0:
                    # Форматируем как "119m 54s"
                    total_minutes = int(remain_time)
                    fractional_part = remain_time - total_minutes
                    seconds = int(round(fractional_part * 60))
                    if seconds >= 60:
                        total_minutes += 1
                        seconds = 0
                    if total_minutes > 0:
                        if seconds > 0:
                            output.append(f"Remaining Time: {total_minutes}m {seconds}s")
                        else:
                            output.append(f"Remaining Time: {total_minutes}m")
                    else:
                        output.append(f"Remaining Time: {seconds}s")
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
            
            # Filament Sensor Status
            if self.filament_sensor:
                try:
                    eventtime = self.reactor.monotonic()
                    sensor_status = self.filament_sensor.get_status(eventtime)
                    
                    filament_detected = sensor_status.get('filament_detected', False)
                    sensor_enabled = sensor_status.get('enabled', False)
                    
                    output.append("=== Filament Sensor ===")
                    if filament_detected:
                        output.append("Status: filament detected")
                    else:
                        output.append("Status: filament not detected")
                    output.append(f"Enabled: {'Yes' if sensor_enabled else 'No'}")
                    output.append("")
                except Exception as e:
                    output.append("=== Filament Sensor ===")
                    output.append(f"Error reading sensor: {str(e)}")
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
                # Специальная обработка для метода get_status
                if method == 'get_status' and 'result' in response:
                    # Добавляем информацию о датчике филамента к результату
                    eventtime = self.reactor.monotonic()
                    response_with_filament = response.copy()
                    response_with_filament['result'] = response['result'].copy()
                    
                    # Добавляем информацию о датчике филамента
                    filament_sensor_status = None
                    if self.filament_sensor:
                        try:
                            filament_sensor_status = self.filament_sensor.get_status(eventtime)
                        except Exception as e:
                            self.logger.warning(f"Error getting filament sensor status: {str(e)}")
                            filament_sensor_status = {"filament_detected": False, "enabled": False}
                    
                    response_with_filament['result']['filament_sensor'] = filament_sensor_status
                    
                    # Выводим дополненный результат
                    gcmd.respond_info(json.dumps(response_with_filament, indent=2))
                else:
                    # Выводим обычный ответ для других методов
                    gcmd.respond_info(json.dumps(response, indent=2))
            
            self.send_request(request, callback)
        except Exception as e:
            self.logger.info(f"Debug command error: {str(e)}")
            gcmd.respond_raw(f"Error: {str(e)}")
            return
    def cmd_ACE_FILAMENT_INFO(self, gcmd):
        index = gcmd.get_int('INDEX', minval=0, maxval=3)
        
        # Валидация INDEX и преобразование в реальный слот
        real_slot, error = self._validate_index_for_operation(index, "ACE_FILAMENT_INFO")
        if error:
            gcmd.respond_raw(f"ACE Error: {error}")
            return
        
        try:
            def callback(response):
                if 'result' in response:
                    slot_info = response['result']
                    self.gcode.respond_info(str(slot_info))
                else:
                    self.gcode.respond_info('Error: No result in response')
            self.send_request({"method": "get_filament_info", "params": {"index": real_slot}}, callback)
        except Exception as e:
            self.logger.info(f"Filament info error: {str(e)}")
            self.gcode.respond_info('Error: ' + str(e))
 
    def cmd_ACE_CHECK_FILAMENT_SENSOR(self, gcmd):
        """Command to check the filament sensor status"""
        if self.filament_sensor:
            try:
                eventtime = self.reactor.monotonic()
                sensor_status = self.filament_sensor.get_status(eventtime)
                
                filament_detected = sensor_status.get('filament_detected', False)
                sensor_enabled = sensor_status.get('enabled', False)
                
                if filament_detected:
                    gcmd.respond_info("Filament sensor: filament detected")
                else:
                    gcmd.respond_info("Filament sensor: filament not detected")
                    
                gcmd.respond_info(f"Filament sensor: {'enabled' if sensor_enabled else 'disabled'}")
            except Exception as e:
                gcmd.respond_info(f"Error checking filament sensor: {str(e)}")
        else:
            gcmd.respond_info("No filament sensor configured")
 
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
                 "duration": duration
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
        
        # Валидация INDEX и преобразование в реальный слот
        real_slot, error = self._validate_index_for_operation(index, "ACE_ENABLE_FEED_ASSIST")
        if error:
            gcmd.respond_raw(f"ACE Error: {error}")
            return
        
        def callback(response):
            if response.get('code', 0) != 0:
                gcmd.respond_raw(f"ACE Error: {response.get('msg', 'Unknown error')}")
            else:
                self._feed_assist_index = index
                gcmd.respond_info(f"Feed assist enabled for index {index} (slot {real_slot})")
                self.dwell(0.3, lambda: None)
        self.send_request({"method": "start_feed_assist", "params": {"index": real_slot}}, callback)
 
    def cmd_ACE_DISABLE_FEED_ASSIST(self, gcmd):
        index = gcmd.get_int('INDEX', self._feed_assist_index, minval=0, maxval=3)
        
        # Валидация INDEX и преобразование в реальный слот
        real_slot, error = self._validate_index_for_operation(index, "ACE_DISABLE_FEED_ASSIST")
        if error:
            gcmd.respond_raw(f"ACE Error: {error}")
            return
        
        def callback(response):
            if response.get('code', 0) != 0:
                gcmd.respond_raw(f"ACE Error: {response.get('msg', 'Unknown error')}")
            else:
                self._feed_assist_index = -1
                gcmd.respond_info(f"Feed assist disabled for index {index} (slot {real_slot})")
                self.dwell(0.3, lambda: None)
        self.send_request({"method": "stop_feed_assist", "params": {"index": real_slot}}, callback)
 
    def cmd_ACE_PARK_TO_TOOLHEAD(self, gcmd):
        if self._park_in_progress:
            gcmd.respond_raw("Already parking to toolhead")
            return
        
        index = gcmd.get_int('INDEX', minval=0, maxval=3)
        
        # Валидация INDEX и преобразование в реальный слот
        real_slot, error = self._validate_index_for_operation(index, "ACE_PARK_TO_TOOLHEAD")
        if error:
            gcmd.respond_raw(f"ACE Error: {error}")
            return
        
        # Проверка статуса слота (должен быть 'ready')
        is_valid, error = self._validate_slot_status(real_slot, 'ready')
        if not is_valid:
            self.gcode.run_script_from_command(f"_ACE_ON_EMPTY_ERROR INDEX={index}")
            return
        
        self._park_to_toolhead(real_slot)
 
    def cmd_ACE_FEED(self, gcmd):
        index = gcmd.get_int('INDEX', minval=0, maxval=3)
        length = gcmd.get_int('LENGTH', minval=1)
        speed = gcmd.get_int('SPEED', self.feed_speed, minval=1)
        
        # Валидация INDEX и преобразование в реальный слот
        real_slot, error = self._validate_index_for_operation(index, "ACE_FEED")
        if error:
            gcmd.respond_raw(f"ACE Error: {error}")
            return
        
        def callback(response):
            if response.get('code', 0) != 0:
                gcmd.respond_raw(f"ACE Error: {response.get('msg', 'Unknown error')}")
        self.send_request({
            "method": "feed_filament",
            "params": {"index": real_slot, "length": length, "speed": speed}
        }, callback)
        self.dwell((length / speed) + 0.1, lambda: None)
 
    def cmd_ACE_UPDATE_FEEDING_SPEED(self, gcmd):
        index = gcmd.get_int('INDEX', minval=0, maxval=3)
        speed = gcmd.get_int('SPEED', self.feed_speed, minval=1)
        
        # Валидация INDEX и преобразование в реальный слот
        real_slot, error = self._validate_index_for_operation(index, "ACE_UPDATE_FEEDING_SPEED")
        if error:
            gcmd.respond_raw(f"ACE Error: {error}")
            return
        
        def callback(response):
            if response.get('code', 0) != 0:
                gcmd.respond_raw(f"ACE Error: {response.get('msg', 'Unknown error')}")
        self.send_request({
            "method": "update_feeding_speed",
            "params": {"index": real_slot, "speed": speed}
        }, callback)
        self.dwell(0.5, lambda: None)
 
    def cmd_ACE_STOP_FEED(self, gcmd):
        index = gcmd.get_int('INDEX', minval=0, maxval=3)
        
        # Валидация INDEX и преобразование в реальный слот
        real_slot, error = self._validate_index_for_operation(index, "ACE_STOP_FEED")
        if error:
            gcmd.respond_raw(f"ACE Error: {error}")
            return
        
        def callback(response):
            if response.get('code', 0) != 0:
                gcmd.respond_raw(f"ACE Error: {response.get('msg', 'Unknown error')}")
            else:
                gcmd.respond_info("Feed stopped")
        self.send_request({
            "method": "stop_feed_filament",
            "params": {"index": real_slot},
            },callback)
        self.dwell(0.5, lambda: None)
 
    def cmd_ACE_RETRACT(self, gcmd):
        index = gcmd.get_int('INDEX', minval=0, maxval=3)
        length = gcmd.get_int('LENGTH', minval=1)
        speed = gcmd.get_int('SPEED', self.retract_speed, minval=1)
        mode = gcmd.get_int('MODE', self.retract_mode, minval=0, maxval=1)
        
        # Валидация INDEX и преобразование в реальный слот
        real_slot, error = self._validate_index_for_operation(index, "ACE_RETRACT")
        if error:
            gcmd.respond_raw(f"ACE Error: {error}")
            return
        
        def callback(response):
            if response.get('code', 0) != 0:
                gcmd.respond_raw(f"ACE Error: {response.get('msg', 'Unknown error')}")
        self.send_request({
            "method": "unwind_filament",
            "params": {"index": real_slot, "length": length, "speed": speed, "mode": mode}
        }, callback)
        # Use async dwell instead of blocking pdwell
        self.dwell((length / speed) + 0.1, lambda: None)
 
    def cmd_ACE_UPDATE_RETRACT_SPEED(self, gcmd):
        index = gcmd.get_int('INDEX', minval=0, maxval=3)
        speed = gcmd.get_int('SPEED', self.retract_speed, minval=1)
        
        # Валидация INDEX и преобразование в реальный слот
        real_slot, error = self._validate_index_for_operation(index, "ACE_UPDATE_RETRACT_SPEED")
        if error:
            gcmd.respond_raw(f"ACE Error: {error}")
            return
        
        def callback(response):
            if response.get('code', 0) != 0:
                gcmd.respond_raw(f"ACE Error: {response.get('msg', 'Unknown error')}")
        self.send_request({
            "method": "update_unwinding_speed",
            "params": {"index": real_slot, "speed": speed}
        }, callback)
        self.dwell(0.5, lambda: None)
 
    def cmd_ACE_STOP_RETRACT(self, gcmd):
        index = gcmd.get_int('INDEX', minval=0, maxval=3)
        
        # Валидация INDEX и преобразование в реальный слот
        real_slot, error = self._validate_index_for_operation(index, "ACE_STOP_RETRACT")
        if error:
            gcmd.respond_raw(f"ACE Error: {error}")
            return
        
        def callback(response):
            if response.get('code', 0) != 0:
                gcmd.respond_raw(f"ACE Error: {response.get('msg', 'Unknown error')}")
            else:
                gcmd.respond_info("Retract stopped")
        self.send_request({
            "method": "stop_unwind_filament",
            "params": {"index": real_slot},
            },callback)
        self.dwell(0.5, lambda: None)
 
    def _distance_based_parking(self, index: int):
        """
        Distance-based parking algorithm for use when no filament sensor is configured.
        
        Algorithm:
        1. Feed filament for (max_parking_distance - 20) mm
        2. Wait for (max_parking_distance / parking_speed) seconds
        3. Poll slot status until it becomes 'ready'
        4. Start traditional parking (feed_assist)
        """
        self.logger.info(f"Starting distance-based parking for slot {index}")

        # Set parking flags
        self._park_in_progress = True
        self._park_error = False
        self._park_index = index
        self._park_start_time = self.reactor.monotonic()
        # Устанавливаем флаги сенсорной парковки (используем те же флаги для совместимости)
        self._sensor_parking_active = True
        self._sensor_parking_completed = False

        # Calculate feed distance: max_parking_distance - 20 mm
        feed_distance = max(self.max_parking_distance - 20, 10)  # Minimum 10mm
        # Calculate wait time: max_parking_distance / parking_speed seconds
        wait_time = self.max_parking_distance / self.parking_speed
        
        self.logger.info(f"Distance-based parking: feeding {feed_distance}mm, wait time {wait_time:.1f}s")

        # Start feeding filament
        def start_feed_callback(response):
            if response.get('code', 0) != 0:
                self.logger.error(f"Error starting feed for distance-based parking: {response.get('msg', 'Unknown error')}")
                self._park_in_progress = False
                self._park_error = True
                self._sensor_parking_active = False
                return

            self.logger.info(f"Started feeding filament for slot {index}: {feed_distance}mm at speed {self.parking_speed}")
            
            # Schedule the wait and status check
            self.dwell(wait_time, lambda: self._check_slot_status_for_parking(index))

        # Send the feed command
        self.send_request({
            "method": "feed_filament",
            "params": {"index": index, "length": feed_distance, "speed": self.parking_speed}
        }, start_feed_callback)
        
        return True

    def _check_slot_status_for_parking(self, index: int):
        """
        Check slot status after distance-based feeding and start traditional parking when ready.
        """
        if not self._park_in_progress:
            self.logger.info(f"Parking already cancelled for slot {index}")
            return

        # Check slot status
        slots = self._info.get('slots', [])
        slot_status = 'unknown'
        if index >= 0 and index < len(slots):
            slot_status = slots[index].get('status', 'unknown')
            
            if slot_status == 'ready':
                self.logger.info(f"Slot {index} is ready, switching to traditional parking")
                self._sensor_parking_active = False
                self._sensor_parking_completed = True
                self._switch_to_traditional_parking(index)
                return
        
        # Slot not ready yet, check again after a short delay
        elapsed = self.reactor.monotonic() - self._park_start_time
        max_wait_time = self.max_parking_timeout
        
        if elapsed > max_wait_time:
            self.logger.error(f"Distance-based parking timeout for slot {index} after {elapsed:.1f}s")
            self._park_in_progress = False
            self._park_error = True
            self._sensor_parking_active = False
            self._sensor_parking_completed = False
            self._pause_print_if_needed()
            return
        
        # Continue polling
        self.logger.debug(f"Slot {index} not ready yet (status: {slot_status}), waiting...")
        self.dwell(0.5, lambda: self._check_slot_status_for_parking(index))

    def _sensor_based_parking(self, index: int):
        """
        Alternative parking algorithm using filament sensor detection.
        Starts feeding filament and monitors the sensor. When the sensor triggers,
        stops the feed and switches to the traditional parking algorithm.
        """
        if not self.filament_sensor:
            self.logger.error("Filament sensor not configured for sensor-based parking")
            return False

        self.logger.info(f"Starting sensor-based parking for slot {index}")

        # Set parking flags
        self._park_in_progress = True
        self._park_error = False
        self._park_index = index
        self._park_start_time = self.reactor.monotonic()
        # Устанавливаем флаги сенсорной парковки
        self._sensor_parking_active = True
        self._sensor_parking_completed = False

        # Calculate timeout: (max_parking_distance / parking_speed) + extended_park_time seconds
        timeout_duration = (self.max_parking_distance / self.parking_speed) + self.extended_park_time
        self.logger.info(f"Sensor-based parking timeout: {timeout_duration:.1f}s")
        
        # Start feeding filament at parking_speed
        def start_feed_callback(response):
            if response.get('code', 0) != 0:
                self.logger.error(f"Error starting feed for sensor-based parking: {response.get('msg', 'Unknown error')}")
                self._park_in_progress = False
                self._park_error = True
                # Очищаем ссылки на таймеры
                self._park_monitor_timer = None
                self._sensor_monitor_timer = None
                return

            self.logger.info(f"Started feeding filament for slot {index} at speed {self.parking_speed}")

            # Start monitoring the filament sensor
            self._monitor_filament_sensor_for_parking(index, timeout_duration)
        
        # Send the feed command
        self.send_request({
            "method": "feed_filament",
            "params": {"index": index, "length": self.max_parking_distance, "speed": self.parking_speed}
        }, start_feed_callback)
        
        return True

    def _monitor_filament_sensor_for_parking(self, index: int, timeout_duration: float):
        """
        Monitor the filament sensor during parking and switch to traditional algorithm when triggered.
        """
        start_time = self.reactor.monotonic()

        def cleanup_sensor_timer():
            """Очистка ссылки на таймер сенсора"""
            self._sensor_monitor_timer = None

        def check_sensor(eventtime):
            if not self._park_in_progress:
                # Parking was cancelled or completed elsewhere
                cleanup_sensor_timer()
                return self.reactor.NEVER
            
            # Check if timeout has been reached
            elapsed = eventtime - start_time
            if elapsed > timeout_duration:
                self.logger.error(f"Sensor-based parking timeout for slot {index} after {elapsed:.1f}s")
                # Stop feeding filament
                self.send_request({
                    "method": "stop_feed_filament",
                    "params": {"index": index}
                }, lambda r: None)
                
                # ALSO stop feed assist to prevent conflicts
                self.send_request({
                    "method": "stop_feed_assist",
                    "params": {"index": index}
                }, lambda r: None)
                
                self._park_in_progress = False
                self._park_error = True
                # Сбрасываем флаги сенсорной парковки
                self._sensor_parking_active = False
                self._sensor_parking_completed = False
                # Проверяем состояние печати и вызываем паузу если нужно
                self._pause_print_if_needed()
                cleanup_sensor_timer()
                return self.reactor.NEVER
            
            # Check filament sensor status
            try:
                sensor_status = self.filament_sensor.get_status(eventtime)
                filament_detected = sensor_status.get('filament_detected', False)
                
                if filament_detected:
                    self.logger.info(f"Filament detected by sensor for slot {index}, switching to traditional parking")
                    # Stop feeding filament and potentially any active feed assist
                    self.send_request({
                        "method": "stop_feed_filament",
                        "params": {"index": index}
                    }, lambda r: None)
                    
                    # Переключаем флаги: сенсорная парковка завершена, запускаем традиционную
                    self._sensor_parking_active = False
                    self._sensor_parking_completed = True
                    
                    # Цикл ожидания статуса устройства (ready) перед переключением на традиционную парковку
                    # Интервал опроса: 0.2 секунды, тайм-аут: 5 секунд
                    status_wait_start = self.reactor.monotonic()
                    status_wait_timeout = 5.0
                    status_poll_interval = 0.2
                    
                    def wait_for_device_ready(eventtime):
                        elapsed = eventtime - status_wait_start
                        
                        # Проверка тайм-аута
                        if elapsed > status_wait_timeout:
                            self.logger.warning(f"Timeout waiting for device ready status after stop_feed_filament for slot {index}, continuing anyway")
                            self.gcode.respond_info(f"ACE: Timeout waiting for device ready, continuing with traditional parking")
                            self._switch_to_traditional_parking(index)
                            cleanup_sensor_timer()
                            return self.reactor.NEVER
                        
                        # Проверяем статус устройства
                        current_status = self._info.get('status', 'unknown')
                        if current_status == 'ready':
                            self.logger.info(f"Device ready after {elapsed:.1f}s, switching to traditional parking for slot {index}")
                            self._switch_to_traditional_parking(index)
                            cleanup_sensor_timer()
                            return self.reactor.NEVER
                        
                        # Продолжаем опрос
                        return eventtime + status_poll_interval
                    
                    # Запускаем таймер опроса статуса
                    self.reactor.register_timer(wait_for_device_ready, self.reactor.NOW)
                    return self.reactor.NEVER
                else:
                    # Continue monitoring
                    return eventtime + 0.1  # Check every 100ms
            except Exception as e:
                self.logger.error(f"Error checking filament sensor during parking: {str(e)}")
                # Stop feeding filament
                self.send_request({
                    "method": "stop_feed_filament",
                    "params": {"index": index}
                }, lambda r: None)
                
                # ALSO stop feed assist to prevent conflicts
                self.send_request({
                    "method": "stop_feed_assist",
                    "params": {"index": index}
                }, lambda r: None)
                
                self._park_in_progress = False
                self._park_error = True
                # Сбрасываем флаги сенсорной парковки
                self._sensor_parking_active = False
                self._sensor_parking_completed = False
                # Проверяем состояние печати и вызываем паузу если нужно
                self._pause_print_if_needed()
                cleanup_sensor_timer()
                return self.reactor.NEVER

        # Register the timer to monitor the sensor and save reference
        self._sensor_monitor_timer = self.reactor.register_timer(check_sensor, self.reactor.NOW)

    def _switch_to_traditional_parking(self, index: int):
        """
        Switch from sensor-based parking to traditional parking algorithm.
        After sensor detects filament, we start feed assist and monitor parking completion
        using the traditional algorithm (feed_assist_count tracking).
        """
        self.logger.info(f"Switching to traditional parking for slot {index} after sensor detection")

        # CRITICAL: Reset timers and counters for the new parking phase
        # This is necessary because elapsed_time and hit_count were accumulated
        # during sensor-based parking phase and would cause false errors
        self._park_start_time = self.reactor.monotonic()
        self._assist_hit_count = 0
        self._park_count_increased = False
        self._last_assist_count = 0
        self.logger.info(f"Reset parking timers for traditional phase: start_time reset, hit_count=0")

        # First, make sure feed assist is stopped before starting traditional parking
        # This prevents conflicts between the two parking phases
        def ensure_feed_assist_stopped(response):
            if response.get('code', 0) != 0:
                self.logger.warning(f"Warning stopping feed assist before traditional parking: {response.get('msg', 'Unknown error')}")
            else:
                self.logger.info(f"Feed assist stopped successfully before traditional parking for slot {index}")
            
            # Now start feed assist for traditional parking
            def start_feed_callback(response):
                if response.get('code', 0) != 0:
                    self.logger.error(f"Error starting feed assist for traditional parking: {response.get('msg', 'Unknown error')}")
                    self._park_error = True
                    self._park_in_progress = False
                    return

                # Получаем начальный счетчик feed_assist_count
                self._last_assist_count = response.get('result', {}).get('feed_assist_count', 0)
                self.logger.info(f"Traditional parking started for slot {index}, initial count: {self._last_assist_count}")
                # Дальше мониторинг будет происходить в _reader_loop через _handle_response

            # Activate feed assist for the slot
            self.send_request({
                "method": "start_feed_assist",
                "params": {"index": index}
            }, start_feed_callback)

        # Stop any ongoing feed assist before switching to traditional parking
        # This ensures clean transition between parking phases
        self.send_request({
            "method": "stop_feed_assist",
            "params": {"index": index}
        }, ensure_feed_assist_stopped)

    def _park_to_toolhead(self, index: int):
        # Устанавливаем флаги парковки ДО вызова любого метода для предотвращения гонки данных
        self._park_in_progress = True
        self._park_error = False
        self._park_index = index
        self._assist_hit_count = 0
        self._park_start_time = self.reactor.monotonic()
        self._park_count_increased = False

        # Check if aggressive parking should be used
        if self.aggressive_parking:
            # Check if filament sensor is configured and available
            if self.filament_sensor:
                self.logger.info(f"Using sensor-based aggressive parking for slot {index}")
                self._sensor_based_parking(index)
            else:
                self.logger.info(f"Using distance-based aggressive parking for slot {index} (no filament sensor)")
                self._distance_based_parking(index)
        else:
            self.logger.info(f"Starting traditional parking for slot {index}")

            def callback(response):
                if response.get('code', 0) != 0:
                    if 'result' in response and 'msg' in response['result']:
                        self.logger.error(f"ACE Error starting feed assist: {response['result']['msg']}")
                    else:
                        self.logger.error(f"ACE Error starting feed assist: {response.get('msg', 'Unknown error')}")
                    # Reset parking flag on error since device won't start feeding
                    self._park_in_progress = False
                    self._park_monitor_timer = None
                    self._sensor_monitor_timer = None
                    self.logger.error(f"Parking aborted for slot {index} due to start_feed_assist error")
                else:
                    self._last_assist_count = response.get('result', {}).get('feed_assist_count', 0)
                    self.logger.info(f"Feed assist started for slot {index}, count: {self._last_assist_count}")
                self.dwell(0.3, lambda: None)
            self.send_request({"method": "start_feed_assist", "params": {"index": index}}, callback)

    def cmd_ACE_CHANGE_TOOL(self, gcmd):
        tool = gcmd.get_int('TOOL', minval=-1, maxval=3)
        was = self.variables.get('ace_current_index', -1)

        if was == tool:
            gcmd.respond_info(f"Tool already set to {tool}")
            return

        # Преобразуем индексы Klipper в реальные слоты устройства
        # Convert Klipper indices to real device slots
        real_tool = self._get_real_slot(tool) if tool != -1 else -1
        real_was = self._get_real_slot(was) if was != -1 else -1

        if tool != -1 and self._info['slots'][real_tool]['status'] != 'ready':
            self.gcode.run_script_from_command(f"_ACE_ON_EMPTY_ERROR INDEX={tool}")
            return

        # Вызываем соответствующий PRE-макрос в зависимости от режима
        if self.ins_spool_work:
            self.gcode.run_script_from_command(f"_ACE_PRE_INFINITYSPOOL FROM={was} TO={tool}")
        else:
            self.gcode.run_script_from_command(f"_ACE_PRE_TOOLCHANGE FROM={was} TO={tool}")
        self._park_is_toolchange = True
        self._park_previous_tool = was
        if self.toolhead:
            self.toolhead.wait_moves()
        self.variables['ace_current_index'] = tool
        self._save_variable('ace_current_index', tool)

        def callback(response):
            if response.get('code', 0) != 0:
                gcmd.respond_raw(f"ACE Error: {response.get('msg', 'Unknown error')}")

        if was != -1:
            # При работе infinity spool ретракт не выполняется - филамент уже закончился
            # When infinity spool is working, skip retract - filament is already empty
            if not self.ins_spool_work:
                # Retract current tool first (используем реальный слот)
                # Retract current tool first (use real slot)
                self.logger.info(f"Retracting from real slot {real_was} (Klipper index {was})")
                self.send_request({
                    "method": "unwind_filament",
                    "params": {
                        "index": real_was,
                        "length": self.toolchange_retract_length,
                        "speed": self.retract_speed
                    }
                }, callback)
                
                # Wait for retract to physically complete
                retract_time = (self.toolchange_retract_length / self.retract_speed) + 1.0
                self.logger.info(f"Waiting {retract_time:.1f}s for retract to complete")
                if self.toolhead:
                    self.toolhead.dwell(retract_time)
                
                # Wait for slot to be ready (status changes to 'ready' after retraction)
                self.logger.info(f"Waiting for real slot {real_was} to be ready")
                timeout = self.reactor.monotonic() + 10.0  # 10 second timeout
                while self._info['slots'][real_was]['status'] != 'ready':
                    if self.reactor.monotonic() > timeout:
                        gcmd.respond_raw(f"ACE Error: Timeout waiting for slot {real_was} to be ready")
                        return
                    if self.toolhead:
                        self.toolhead.dwell(1.0)
                
                self.logger.info(f"Slot {real_was} is ready, parking new tool {tool} (real slot {real_tool})")
            else:
                self.logger.info(f"Skipping retract for infinity spool - slot {real_was} is empty, parking new tool {tool} (real slot {real_tool})")
            
            if tool != -1:
                # Park new tool to toolhead (используем реальный слот)
                # Park new tool to toolhead (use real slot)
                self._park_to_toolhead(real_tool)

                # Wait for parking to complete (check self._park_in_progress)
                self.logger.info(f"Waiting for parking to complete (real slot {real_tool})")
                timeout = self.reactor.monotonic() + self.max_parking_timeout  # max_parking_timeout seconds timeout for parking
                while self._park_in_progress:
                    if self._connection_lost:
                        gcmd.respond_raw(f"ACE Error: Connection lost during parking for slot {real_tool}")
                        self._pause_print_if_needed()
                        return
                    if self._park_error:
                        gcmd.respond_raw(f"ACE Error: Parking failed for slot {real_tool}")
                        return
                    if self.reactor.monotonic() > timeout:
                        gcmd.respond_raw(f"ACE Error: Timeout waiting for parking to complete ({self.max_parking_timeout}s)")
                        self._pause_print_if_needed()
                        return
                    if self.toolhead:
                        self.toolhead.dwell(1.0)

                self.logger.info(f"Parking completed, executing post-toolchange")
                if self.toolhead:
                    self.toolhead.wait_moves()

                # Execute post-toolchange macro
                if self.ins_spool_work:
                    self.gcode.run_script_from_command(f'_ACE_POST_INFINITYSPOOL FROM={was} TO={tool}')
                else:
                    self.gcode.run_script_from_command(f'_ACE_POST_TOOLCHANGE FROM={was} TO={tool}')
                if self.toolhead:
                    self.toolhead.wait_moves()
                gcmd.respond_info(f"Tool changed from {was} to {tool} (real slot {real_tool})")
            else:
                # Unloading only, no new tool
                if self.ins_spool_work:
                    self.gcode.run_script_from_command(f'_ACE_POST_INFINITYSPOOL FROM={was} TO={tool}')
                else:
                    self.gcode.run_script_from_command(f'_ACE_POST_TOOLCHANGE FROM={was} TO={tool}')
                if self.toolhead:
                    self.toolhead.wait_moves()
                gcmd.respond_info(f"Tool changed from {was} to {tool}")
        else:
            # No previous tool, just park the new one (используем реальный слот)
            # No previous tool, just park the new one (use real slot)
            self.logger.info(f"Starting parking for real slot {real_tool} (Klipper index {tool}, no previous tool)")
            self._park_to_toolhead(real_tool)

            # Wait for parking to complete (check self._park_in_progress)
            self.logger.info(f"Waiting for parking to complete (real slot {real_tool})")
            timeout = self.reactor.monotonic() + self.max_parking_timeout  # max_parking_timeout seconds timeout for parking
            while self._park_in_progress:
                if self._connection_lost:
                    gcmd.respond_raw(f"ACE Error: Connection lost during parking for slot {real_tool}")
                    self._pause_print_if_needed()
                    return
                if self._park_error:
                    gcmd.respond_raw(f"ACE Error: Parking failed for slot {real_tool}")
                    return
                if self.reactor.monotonic() > timeout:
                    gcmd.respond_raw(f"ACE Error: Timeout waiting for parking to complete ({self.max_parking_timeout}s)")
                    self._pause_print_if_needed()
                    return
                if self.toolhead:
                    self.toolhead.dwell(1.0)
            
            self.logger.info(f"Parking completed, executing post-toolchange")
            if self.toolhead:
                self.toolhead.wait_moves()

            # Execute post-toolchange macro
            if self.ins_spool_work:
                self.gcode.run_script_from_command(f'_ACE_POST_INFINITYSPOOL FROM={was} TO={tool}')
            else:
                self.gcode.run_script_from_command(f'_ACE_POST_TOOLCHANGE FROM={was} TO={tool}')
            if self.toolhead:
                self.toolhead.wait_moves()
            gcmd.respond_info(f"Tool changed from {was} to {tool} (real slot {real_tool})")
     
    def cmd_ACE_DISCONNECT(self, gcmd):
        """G-code command to force disconnect from the device"""
        try:
            if self._connected:
                self._manually_disconnected = True  # Mark as manually disconnected
                self._disconnect()
                gcmd.respond_info("ACE device disconnected successfully")
                self.logger.info("Device manually disconnected via ACE_DISCONNECT command")
            else:
                gcmd.respond_info("ACE device is already disconnected")
        except Exception as e:
            self.logger.error(f"Error during forced disconnect: {str(e)}")
            gcmd.respond_raw(f"Error disconnecting: {str(e)}")

    def cmd_ACE_CONNECT(self, gcmd):
        """G-code command to connect to the device"""
        try:
            if self._connected:
                gcmd.respond_info("ACE device is already connected")
            else:
                self._manually_disconnected = False  # Reset the manually disconnected flag
                
                # Attempt immediate connection
                success = self._connect()
                
                if success:
                    gcmd.respond_info("ACE device connected successfully")
                    self.logger.info("Device manually connected via ACE_CONNECT command")
                else:
                    gcmd.respond_raw("Failed to connect to ACE device")
                    self.logger.error("Manual connection attempt failed")
        except Exception as e:
            self.logger.error(f"Error during manual connect: {str(e)}")
            gcmd.respond_raw(f"Error connecting: {str(e)}")

    def cmd_ACE_CONNECTION_STATUS(self, gcmd):
        """G-code command to check connection status"""
        try:
            status = "connected" if self._connected else "disconnected"
            gcmd.respond_info(f"ACE Connection Status: {status}")

            if self._connected:
                # Provide additional connection details
                try:
                    model = self._info.get('model', 'Unknown')
                    firmware = self._info.get('firmware', 'Unknown')
                    gcmd.respond_info(f"Device: {model}, Firmware: {firmware}")
                except Exception:
                    gcmd.respond_info("Device: connected (details unavailable)")
            else:
                gcmd.respond_info(f"Serial Port: {self.serial_name}")
                gcmd.respond_info(f"Baud Rate: {self.baud}")
            
            # Дополнительная информация о состоянии обрыва связи
            if self._connection_lost:
                gcmd.respond_raw(f"ACE: Connection lost flag is set (attempts: {self._reconnect_attempts}/{self._max_reconnect_attempts})")
                gcmd.respond_info("Try ACE_RECONNECT to reset the connection")
        except Exception as e:
            self.logger.error(f"Error checking connection status: {str(e)}")
            gcmd.respond_raw(f"Error checking status: {str(e)}")

    def cmd_ACE_RECONNECT(self, gcmd):
        """G-code command to manually reset connection and clear error flags"""
        try:
            self.logger.info("Manual reconnection requested via ACE_RECONNECT")
            # Сбрасываем флаги ошибки
            self._connection_lost = False
            self._reconnect_attempts = 0
            
            # Пробуем подключиться
            self._manually_disconnected = False
            self._disconnect()
            self.dwell(1.0, lambda: None)
            
            success = self._connect()
            if success:
                gcmd.respond_info("ACE: Reconnection successful")
            else:
                gcmd.respond_raw("ACE: Reconnection failed, will retry automatically")
        except Exception as e:
            self.logger.error(f"Error during manual reconnect: {str(e)}")
            gcmd.respond_raw(f"Error reconnecting: {str(e)}")

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
        """
        Автоматическая смена слота при окончании филамента.
        Вызывает ACE_CHANGE_TOOL с установленным флагом ins_spool_work,
        который определяет какие макросы будут вызваны (PRE/POST_INFINITYSPOOL вместо PRE/POST_TOOLCHANGE).
        """
        # 1. Проверка что операция не выполняется
        if self.ins_spool_work:
            gcmd.respond_info("ACE_INFINITY_SPOOL: Operation already in progress")
            self.logger.info("ACE_INFINITY_SPOOL: BLOCKED - ins_spool_work is already True")
            return
        
        # 2. Отменить все активные таймеры мониторинга перед началом смены
        if self.infsp_debounce_timer is not None:
            self.logger.info("ACE_INFINITY_SPOOL: Cancelling debounce timer")
            try:
                self.reactor.unregister_timer(self.infsp_debounce_timer)
            except:
                pass
            self.infsp_debounce_timer = None
        
        if self.infsp_sensor_monitor_timer is not None:
            self.logger.info("ACE_INFINITY_SPOOL: Cancelling sensor monitor timer")
            try:
                self.reactor.unregister_timer(self.infsp_sensor_monitor_timer)
            except:
                pass
            self.infsp_sensor_monitor_timer = None
        
        # 3. Сбросить флаг empty_detected
        self.infsp_empty_detected = False
        
        # 4. Установить флаг работы
        self.ins_spool_work = True
        self.logger.info("ACE_INFINITY_SPOOL: STARTED - ins_spool_work set to True")
        
        try:
            # 3. Проверка infinity_spool_mode
            if not self.infinity_spool_mode:
                gcmd.respond_info("ACE_INFINITY_SPOOL: Mode is disabled")
                return
            
            # 4. Получить текущий индекс
            current_index = self.variables.get('ace_current_index', -1)
            
            if current_index == -1:
                gcmd.respond_info("ACE_INFINITY_SPOOL: Tool is not set")
                return
            
            # 5. Получить порядок слотов
            order_str = self.variables.get('ace_infsp_order', '')
            
            # 6. Выбрать следующий слот
            next_slot = None
            new_position = None
            
            if order_str:
                # Парсим порядок (формат "0,2,1,3" или подобный)
                # Проверяем тип order_str - может быть строкой или кортежем
                self.logger.debug(f"ace_infsp_order type: {type(order_str).__name__}, value: {order_str}")
                try:
                    order_list = []
                    # Если order_str - кортеж или список, конвертируем в список напрямую
                    if isinstance(order_str, (tuple, list)):
                        self.logger.info(f"ace_infsp_order is {type(order_str).__name__}, converting to list")
                        for item in order_str:
                            item_str = str(item).strip().lower()
                            if item_str == 'none':
                                order_list.append('none')
                            else:
                                order_list.append(int(item_str))
                    else:
                        # Строковый формат - парсим через split
                        for item in str(order_str).split(','):
                            item = item.strip().lower()
                            if item == 'none':
                                order_list.append('none')
                            else:
                                order_list.append(int(item))
                    
                    # Получить текущую позицию в порядке
                    current_pos = self.variables.get('ace_infsp_position', -1)
                    
                    # Найти текущий слот в порядке если позиция не сохранена
                    if current_pos < 0 or current_pos >= len(order_list):
                        for i, slot in enumerate(order_list):
                            if slot != 'none' and slot == current_index:
                                current_pos = i
                                break
                    
                    # Найти следующий в порядке
                    for i in range(len(order_list)):
                        idx = (current_pos + 1 + i) % len(order_list)
                        slot = order_list[idx]
                        if slot != 'none' and self._is_slot_ready(slot):
                            next_slot = slot
                            new_position = idx
                            break
                            
                except Exception as e:
                    self.logger.error(f"Error parsing infinity spool order: {str(e)}")
            else:
                # Первый доступный в порядке 0,1,2,3
                for idx in range(4):
                    if self._is_slot_ready(idx):
                        next_slot = idx
                        new_position = idx
                        break
            
            if next_slot is None:
                gcmd.respond_info("ACE_INFINITY_SPOOL: No ready slot found")
                return
            
            # 7. Сохранить позицию в порядке
            if new_position is not None:
                self._save_variable('ace_infsp_position', new_position)
            
            self.logger.info(f"ACE_INFINITY_SPOOL: changing from {current_index} to {next_slot}")
            
            # 8. Вызвать ACE_CHANGE_TOOL с выбранным слотом
            self.gcode.run_script_from_command(f"ACE_CHANGE_TOOL TOOL={next_slot}")
            
        finally:
            # 9. Сбросить флаг и состояние перед завершением
            self.logger.info(f"ACE_INFINITY_SPOOL: FINALLY - resetting ins_spool_work from {self.ins_spool_work} to False")
            self.ins_spool_work = False
            # Сбросить последний известный статус, чтобы избежать повторного триггера
            # при следующем вызове _check_slot_empty_status
            self.infsp_last_active_status = None

    def cmd_ACE_GET_HELP(self, gcmd):
        """Show all available ACE commands with descriptions"""
        help_text = """
====== ValgACE Commands Help ======

Information Commands:
  ACE_STATUS                - Get full ACE device status
  ACE_FILAMENT_INFO         - Get filament info from slot (requires RFID)
  ACE_CHECK_FILAMENT_SENSOR - Check external filament sensor status

Tool Management:
  ACE_CHANGE_TOOL           - Change tool (auto load/unload filament)
  ACE_PARK_TO_TOOLHEAD      - Park filament to toolhead nozzle

Filament Control:
  ACE_FEED                  - Feed filament from specified slot
  ACE_RETRACT               - Retract filament back to slot
  ACE_STOP_FEED             - Stop filament feeding
  ACE_STOP_RETRACT          - Stop filament retraction
  ACE_UPDATE_FEEDING_SPEED  - Update feeding speed on the fly
  ACE_UPDATE_RETRACT_SPEED  - Update retract speed on the fly

Feed Assist:
  ACE_ENABLE_FEED_ASSIST    - Enable feed assist for slot
  ACE_DISABLE_FEED_ASSIST   - Disable feed assist for slot

Drying Control:
  ACE_START_DRYING          - Start filament drying process
  ACE_STOP_DRYING           - Stop filament drying process

Connection:
  ACE_DISCONNECT            - Force disconnect from ACE device
  ACE_CONNECT               - Connect to ACE device
  ACE_CONNECTION_STATUS     - Check connection status
  ACE_RECONNECT             - Reset connection and clear error flags

Infinity Spool Mode:
  ACE_SET_INFINITY_SPOOL_ORDER - Set slot change order for infinity spool
  ACE_INFINITY_SPOOL        - Auto spool change on filament end

Slot Mapping:
  ACE_GET_SLOTMAPPING       - Get current slot mapping (index to slot)
  ACE_SET_SLOTMAPPING       - Set slot mapping (INDEX=0-3 SLOT=0-3)
  ACE_RESET_SLOTMAPPING     - Reset slot mapping to defaults (0→0, 1→1, 2→2, 3→3)

Index Management:
  ACE_GET_CURRENT_INDEX     - Get current tool index value
  ACE_SET_CURRENT_INDEX     - Set current tool index value (for error recovery)

Debug:
  ACE_DEBUG                 - Debug command for direct device interaction

===================================

"""
        gcmd.respond_info(help_text)

    def cmd_ACE_GET_SLOTMAPPING(self, gcmd):
        """
        Получить текущее отображение индексов в слоты.
        Get current index to slot mapping.
        
        Формат вывода / Output format:
        Slot Mapping:
          Index 0 → Slot X
          Index 1 → Slot X
          Index 2 → Slot X
          Index 3 → Slot X
        """
        output = ["=== Slot Mapping ==="]
        for i in range(4):
            output.append(f"  Index {i} → Slot {self.index_to_slot[i]}")
        output.append("")
        output.append(f"Current mapping: {self.index_to_slot}")
        gcmd.respond_info("\n".join(output))

    def cmd_ACE_SET_SLOTMAPPING(self, gcmd):
        """
        Установить отображение индекса в слот.
        Set index to slot mapping.
        
        Параметры / Parameters:
          INDEX=0-3  - Индекс из Klipper (T0-T3) / Index from Klipper (T0-T3)
          SLOT=0-3   - Реальный слот устройства / Real device slot
        """
        index = gcmd.get_int('INDEX', minval=0, maxval=3)
        slot = gcmd.get_int('SLOT', minval=0, maxval=3)
        
        # Валидация INDEX
        real_index, error = self._validate_index(index)
        if error:
            gcmd.respond_raw(f"ACE Error: {error}")
            return
        
        # Валидация SLOT
        if slot < 0 or slot > 3:
            gcmd.respond_raw(f"ACE Error: SLOT {slot} out of range (must be 0-3)")
            return
        
        old_slot = self.index_to_slot[index]
        
        if self._set_slot_mapping(index, slot):
            gcmd.respond_info(f"Slot mapping updated: Index {index} → Slot {slot} (was Slot {old_slot})")
            gcmd.respond_info(f"Current mapping: {self.index_to_slot}")
        else:
            gcmd.respond_raw(f"Error: Failed to set slot mapping for index {index}")

    def cmd_ACE_RESET_SLOTMAPPING(self, gcmd):
        """
        Сбросить отображение слотов в дефолтные значения.
        Reset slot mapping to default values (0→0, 1→1, 2→2, 3→3).
        """
        old_mapping = self.index_to_slot.copy()
        self._reset_slot_mapping()
        gcmd.respond_info(f"Slot mapping reset to defaults")
        gcmd.respond_info(f"  Old mapping: {old_mapping}")
        gcmd.respond_info(f"  New mapping: {self.index_to_slot}")

    def cmd_ACE_GET_CURRENT_INDEX(self, gcmd):
        """
        Get the current tool index value.
        This command outputs the current value of the ace_current_index variable.
        """
        current_index = self.variables.get('ace_current_index', -1)
        gcmd.respond_info(f"Current tool index: {current_index}")
        
    def cmd_ACE_SET_CURRENT_INDEX(self, gcmd):
        """
        Set the current tool index value.
        This command allows users to set an arbitrary index in the range -1 to 3.
        Useful when the printer encounters an error and the correct index was not recorded during filament change.
        
        Parameters:
          INDEX: The index to set (-1 to 3)
        """
        new_index = gcmd.get_int('INDEX', minval=-1, maxval=3)
        
        old_index = self.variables.get('ace_current_index', -1)
        
        # Update the variable
        self.variables['ace_current_index'] = new_index
        self._save_variable('ace_current_index', new_index)
        
        gcmd.respond_info(f"Tool index changed from {old_index} to {new_index}")

    # ============================================================
    # Infinity Spool Auto-trigger Methods
    # ============================================================

    def _is_printer_printing(self):
        """Проверяет, находится ли принтер в состоянии печати."""
        try:
            idle_timeout = self.printer.lookup_object('idle_timeout')
            state = idle_timeout.get_status(eventtime=self.reactor.monotonic()).get('state', 'idle')
            return state == 'Printing'
        except Exception:
            return False

    def _get_active_slot_index(self):
        """Возвращает индекс текущего активного слота или -1."""
        return self.variables.get('ace_current_index', -1)

    def _get_active_slot_status(self):
        """Возвращает статус текущего активного слота или None."""
        idx = self._get_active_slot_index()
        if idx is None or idx < 0:
            return None
        # Получаем реальный слот через маппинг
        real_slot = self._get_real_slot(idx)
        slots = self._info.get('slots', [])
        if real_slot < 0 or real_slot >= len(slots):
            return None
        return slots[real_slot].get('status', None)

    def _check_slot_empty_status(self):
        """Проверяет, изменился ли статус активного слота на 'empty'."""
        if not self.infinity_spool_mode:
            return False
        
        # ВАЖНО: Не запускать мониторинг если уже идёт смена слота
        if self.ins_spool_work:
            self.logger.debug(f"_check_slot_empty_status: SKIP - ins_spool_work is True")
            return False

        current_status = self._get_active_slot_status()
        self.logger.debug(f"_check_slot_empty_status: current_status={current_status}, last_status={self.infsp_last_active_status}, ins_spool_work={self.ins_spool_work}")

        # Обнаружен переход в empty
        if current_status == 'empty' and self.infsp_last_active_status != 'empty':
            self.infsp_last_active_status = current_status
            self.logger.info(f"_check_slot_empty_status: EMPTY detected! ins_spool_work={self.ins_spool_work}")
            return True

        self.infsp_last_active_status = current_status
        return False

    def _start_empty_slot_monitoring(self):
        """Запускает debounce-мониторинг при обнаружении empty статуса."""
        self.logger.info(f"_start_empty_slot_monitoring: CALLED, ins_spool_work={self.ins_spool_work}, debounce_timer={self.infsp_debounce_timer is not None}")
        
        # ВАЖНО: Не запускать мониторинг если уже идёт смена слота
        if self.ins_spool_work:
            self.logger.info("_start_empty_slot_monitoring: SKIP - ins_spool_work is True")
            return
        
        if self.infsp_debounce_timer is not None:
            self.logger.info("_start_empty_slot_monitoring: Cancelling existing debounce timer")
            try:
                self.reactor.unregister_timer(self.infsp_debounce_timer)
            except:
                pass
            self.infsp_debounce_timer = None

        self.infsp_empty_detected = True
        self.infsp_debounce_timer = self.reactor.register_timer(
            self._monitor_empty_slot_debounce,
            self.reactor.monotonic() + self.infinity_spool_debounce
        )

    def _monitor_empty_slot_debounce(self, eventtime):
        """Подтверждает empty статус после debounce периода."""
        self.infsp_debounce_timer = None

        # ВАЖНО: Не продолжать если уже идёт смена слота
        if self.ins_spool_work:
            self.logger.info("_monitor_empty_slot_debounce: SKIP - ins_spool_work is True")
            self.infsp_empty_detected = False
            return self.reactor.NEVER

        # Проверяем условия
        if not self._is_printer_printing():
            self.infsp_empty_detected = False
            return self.reactor.NEVER

        if self._get_active_slot_status() != 'empty':
            self.infsp_empty_detected = False
            return self.reactor.NEVER

        # Empty статус подтверждён — переходим к обработке
        self._handle_infinity_spool_scenario()
        return self.reactor.NEVER

    def _handle_infinity_spool_scenario(self):
        """Обрабатывает сценарий empty слота: с датчиком или без."""
        # ВАЖНО: Не продолжать если уже идёт смена слота
        if self.ins_spool_work:
            self.logger.info("_handle_infinity_spool_scenario: SKIP - ins_spool_work is True")
            self.infsp_empty_detected = False
            return
        
        if not self._is_printer_printing():
            self.infsp_empty_detected = False
            return

        # Если есть датчик филамента — ждём его срабатывания
        if self.filament_sensor:
            self._monitor_filament_sensor_for_empty()
        else:
            # Без датчика — пауза или немедленная смена
            if self.infinity_spool_pause_on_no_sensor:
                self._trigger_pause_macro()
            else:
                self._trigger_infinity_spool_auto()

    def _monitor_filament_sensor_for_empty(self):
        """Мониторит датчик филамента без таймаута."""
        if self.infsp_sensor_monitor_timer is not None:
            self.infsp_sensor_monitor_timer.cancel()

        self.infsp_sensor_monitor_timer = self.reactor.register_timer(
            self._check_filament_sensor_trigger,
            self.reactor.monotonic() + 1.0  # Проверка каждую секунду
        )

    def _check_filament_sensor_trigger(self, eventtime):
        """Периодически проверяет датчик филамента без таймаута."""
        # ВАЖНО: Не продолжать если уже идёт смена слота
        if self.ins_spool_work:
            self.logger.info("_check_filament_sensor_trigger: SKIP - ins_spool_work is True")
            self.infsp_sensor_monitor_timer = None
            self.infsp_empty_detected = False
            return self.reactor.NEVER
        
        # Проверяем датчик
        try:
            fs = self.printer.lookup_object(f'filament_switch_sensor {self.filament_sensor_name}')
            sensor_active = fs.get_status(eventtime).get('filament_detected', True)

            if not sensor_active:  # Филамент не обнаружен
                self.infsp_sensor_monitor_timer = None
                self._trigger_infinity_spool_auto()
                return self.reactor.NEVER
        except Exception as e:
            self.logger.warning(f"Error checking filament sensor: {str(e)}")
            pass

        return eventtime + 1.0  # Следующая проверка через секунду

    def _trigger_infinity_spool_auto(self):
        """Программно вызывает ACE_INFINITY_SPOOL."""
        self.logger.info(f"_trigger_infinity_spool_auto: CALLED, ins_spool_work={self.ins_spool_work}")
        
        # ВАЖНО: Не запускать если уже идёт смена слота
        if self.ins_spool_work:
            self.logger.info("_trigger_infinity_spool_auto: SKIP - ins_spool_work is True")
            self.infsp_empty_detected = False
            return
        
        self.infsp_empty_detected = False

        # Создаём фиктивный GCode command
        gcode = self.printer.lookup_object('gcode')
        gcode.run_script('ACE_INFINITY_SPOOL')

    def _trigger_pause_macro(self):
        """Вызывает макрос паузы печати."""
        self.infsp_empty_detected = False
        gcode = self.printer.lookup_object('gcode')
        gcode.run_script('PAUSE')

def load_config(config):
    return ValgAce(config)