# ACE Temperature Sensor для Klipper

## Описание

Модуль **temperature_ace** предоставляет интеграцию температуры ACE устройства с системой температурных сенсоров Klipper. Это позволяет мониторить температуру ACE через стандартные интерфейсы Klipper (Mainsail, Fluidd, KlipperScreen) и использовать её в макросах.

## Возможности

- ✅ Отображение температуры ACE в веб-интерфейсе
- ✅ Мониторинг минимальной и максимальной температуры
- ✅ Использование в G-code макросах
- ✅ Защита от перегрева (автоматический shutdown)
- ✅ Логирование температурной статистики
- ✅ Moonraker API интеграция

## Установка

### 1. Файл уже создан

Модуль находится в:
```
klippy/extras/temperature_ace.py
```

### 2. Загрузите модуль

**Важно!** Сначала нужно загрузить модуль, затем использовать sensor_type.

В `printer.cfg` добавьте **В ТАКОМ ПОРЯДКЕ**:

```ini
# Шаг 1: Загрузить модуль temperature_ace
[temperature_ace]

# Шаг 2: Использовать sensor_type
[temperature_sensor ace_chamber]
sensor_type: temperature_ace
min_temp: 0
max_temp: 70
```

**Или через include:**

```ini
# Шаг 1: Загрузить модуль
[include temperature_ace.cfg]

# Шаг 2: Использовать sensor_type
[temperature_sensor ace_chamber]
sensor_type: temperature_ace
min_temp: 0
max_temp: 70
```

### 3. Перезапустите Klipper

```gcode
RESTART
```

## Конфигурация

### Базовая конфигурация

```ini
[temperature_sensor ace_chamber]
sensor_type: temperature_ace  # Тип сенсора (обязательно)
min_temp: 0                   # Минимальная температура (°C)
max_temp: 70                  # Максимальная температура (°C)
```

### Параметры

| Параметр | Обязательный | Описание | Значение по умолчанию |
|----------|--------------|----------|----------------------|
| `sensor_type` | ✅ Да | Тип сенсора | `temperature_ace` |
| `min_temp` | ✅ Да | Минимальная допустимая температура (°C) | - |
| `max_temp` | ✅ Да | Максимальная допустимая температура (°C) | - |

### Рекомендуемые значения

```ini
# Для мониторинга камеры
min_temp: 0
max_temp: 70

# Для мониторинга сушилки
min_temp: 0
max_temp: 60  # Макс. температура сушки ACE = 55°C
```

**Важно:** Если температура выйдет за пределы `min_temp`/`max_temp`, Klipper выполнит **emergency shutdown**!

## Использование

### Просмотр в веб-интерфейсе

После настройки температура ACE будет отображаться:

**Mainsail/Fluidd:**
- На главной панели в разделе "Temperature"
- График температуры в реальном времени
- История температур

**KlipperScreen:**
- На главном экране
- В меню Temperature

### Использование в G-code макросах

```gcode
[gcode_macro CHECK_CHAMBER_TEMP]
gcode:
    {% set ace_temp = printer["temperature_sensor ace_chamber"].temperature %}
    M118 ACE temperature: {ace_temp}°C
```

### Доступ к статистике

```gcode
[gcode_macro ACE_TEMP_STATS]
gcode:
    {% set sensor = printer["temperature_sensor ace_chamber"] %}
    {% set current = sensor.temperature %}
    {% set min = sensor.measured_min_temp %}
    {% set max = sensor.measured_max_temp %}
    
    M118 Current: {current}°C
    M118 Min: {min}°C
    M118 Max: {max}°C
```

## Примеры использования

### Пример 1: Мониторинг температуры камеры

```ini
[temperature_sensor ace_chamber]
sensor_type: temperature_ace
min_temp: 0
max_temp: 70
```

```gcode
[gcode_macro START_PRINT]
gcode:
    {% set chamber_temp = printer["temperature_sensor ace_chamber"].temperature %}
    
    M118 Starting print, chamber temperature: {chamber_temp}°C
    
    # Ваша логика start_print
    # ...
```

### Пример 2: Предупреждение о перегреве

```ini
[temperature_sensor ace_monitor]
sensor_type: temperature_ace
min_temp: 0
max_temp: 65  # Shutdown при превышении
```

```gcode
[gcode_macro MONITOR_ACE_TEMP]
gcode:
    {% set temp = printer["temperature_sensor ace_monitor"].temperature %}
    
    {% if temp > 55 %}
        M118 Warning: ACE temperature high ({temp}°C)
        # Опционально: остановить сушку
        ACE_STOP_DRYING
    {% elif temp > 60 %}
        M118 Critical: ACE temperature critical ({temp}°C)!
        PAUSE
    {% endif %}
```

### Пример 3: Периодический мониторинг

```gcode
[delayed_gcode ace_temp_monitor]
initial_duration: 60.0
gcode:
    {% set sensor = printer["temperature_sensor ace_chamber"] %}
    {% set temp = sensor.temperature %}
    {% set min = sensor.measured_min_temp %}
    {% set max = sensor.measured_max_temp %}
    
    M118 ACE: {temp}°C (Min: {min}°C, Max: {max}°C)
    
    # Продолжить мониторинг каждые 5 минут
    UPDATE_DELAYED_GCODE ID=ace_temp_monitor DURATION=300
```

### Пример 4: Условный старт печати

```gcode
[gcode_macro SMART_START_PRINT]
gcode:
    {% set target_chamber = params.CHAMBER|default(30)|float %}
    {% set chamber_temp = printer["temperature_sensor ace_chamber"].temperature %}
    
    # Проверка температуры камеры
    {% if chamber_temp < target_chamber %}
        M118 Chamber too cold ({chamber_temp}°C), heating required
        # Включить обогрев камеры или подождать
        TEMPERATURE_WAIT SENSOR="temperature_sensor ace_chamber" MINIMUM={target_chamber}
    {% endif %}
    
    M118 Chamber ready ({chamber_temp}°C)
    # Продолжить печать
```

### Пример 5: Интеграция с сушкой

```gcode
[gcode_macro START_DRYING_MONITORED]
gcode:
    {% set TEMP = params.TEMP|default(50)|int %}
    {% set DURATION = params.DURATION|default(120)|int %}
    
    M118 Starting drying at {TEMP}°C for {DURATION} minutes
    ACE_START_DRYING TEMP={TEMP} DURATION={DURATION}
    
    # Мониторинг температуры во время сушки
    UPDATE_DELAYED_GCODE ID=drying_monitor DURATION=60

[delayed_gcode drying_monitor]
gcode:
    {% set dryer = printer.ace._info.dryer %}
    {% set temp = printer["temperature_sensor ace_chamber"].temperature %}
    
    {% if dryer.status == 'run' %}
        M118 Drying: {temp}°C / {dryer.target_temp}°C (remaining: {dryer.remain_time/60}min)
        UPDATE_DELAYED_GCODE ID=drying_monitor DURATION=60
    {% else %}
        M118 Drying complete
    {% endif %}
```

## Технические детали

### Как работает модуль

1. **Регистрация сенсора:**
   - Модуль регистрируется в системе `heaters` как sensor factory
   - Создается объект `temperature_ace <name>`

2. **Периодическое чтение:**
   - Каждую секунду (`ACE_REPORT_TIME = 1.0`)
   - Читает `ace._info['temp']` из модуля ACE
   - Вызывает callback с новым значением температуры

3. **Отслеживание статистики:**
   - Минимальная температура с момента запуска
   - Максимальная температура с момента запуска
   - Текущая температура

4. **Защита от выхода за пределы:**
   - Проверка `min_temp` и `max_temp`
   - Emergency shutdown при превышении

### Источник температуры

Температура читается из:
```python
ace._info['temp']
```

Которая обновляется ACE модулем через:
- Периодические запросы `get_status` (каждую 1 секунду в обычном режиме)
- Частые запросы (каждые 0.2 секунды) во время парковки

### Интервал обновления

- **Чтение из ACE:** каждую 1 секунду (через `_writer_loop`)
- **Обновление сенсора:** каждую 1 секунду (`ACE_REPORT_TIME`)
- **Отображение в UI:** зависит от настроек UI (обычно 1-2 секунды)

### Точность

Температура от ACE устройства:
- **Разрешение:** 1°C (целочисленное значение от устройства)
- **Точность:** зависит от датчика ACE (~±1-2°C)
- **Диапазон:** 0-70°C

## Moonraker API

Температура доступна через Moonraker API:

```python
# Текущая температура
printer.temperature_sensor.ace_chamber.temperature

# Минимальная температура
printer.temperature_sensor.ace_chamber.measured_min_temp

# Максимальная температура
printer.temperature_sensor.ace_chamber.measured_max_temp
```

### Пример запроса через API

```bash
# HTTP GET запрос
curl http://localhost:7125/printer/objects/query?temperature_sensor
```

**Ответ:**
```json
{
  "result": {
    "status": {
      "temperature_sensor": {
        "ace_chamber": {
          "temperature": 28.0,
          "measured_min_temp": 24.5,
          "measured_max_temp": 55.3
        }
      }
    }
  }
}
```

## Устранение неполадок

### Проблема: Температура всегда 0

**Причины:**
1. ACE модуль не загружен
2. ACE устройство не подключено
3. Не получен статус от устройства

**Решение:**
```gcode
# Проверьте ACE модуль
ACE_STATUS

# Проверьте подключение
ACE_DEBUG METHOD=get_status

# Посмотрите логи
# journalctl -u klipper -f | grep -i "temperature_ace"
```

### Проблема: Температура не обновляется

**Причины:**
1. ACE модуль не получает обновления статуса
2. Проблемы с serial соединением

**Решение:**
```gcode
# Проверьте что ACE получает обновления
ACE_STATUS

# В логах должно быть:
# "ACE temperature sensor: ACE module found"
```

### Проблема: Klipper shutdown из-за температуры

**Симптомы:**
```
ACE temperature 71.0 above maximum temperature of 70.0
```

**Решение:**
```ini
# Увеличьте max_temp в конфигурации
[temperature_sensor ace_chamber]
sensor_type: temperature_ace
min_temp: 0
max_temp: 75  # Увеличено
```

### Проблема: Множественные сенсоры показывают одно значение

**Это нормально!** Все сенсоры с типом `temperature_ace` читают температуру из одного источника (ACE устройство имеет один датчик температуры).

Если нужны разные значения - используйте разные источники:
```ini
[temperature_sensor ace]
sensor_type: temperature_ace

[temperature_sensor raspberry_pi]
sensor_type: temperature_host

[temperature_sensor mcu]
sensor_type: temperature_mcu
```

## Графики температуры

### В Mainsail/Fluidd

Температура ACE автоматически появится в:
1. Графике температур (Temperature Chart)
2. Списке сенсоров на главной странице
3. Истории температур

### Настройка отображения

В Mainsail/Fluidd можно:
- Включить/выключить отображение на графике
- Настроить цвет линии
- Установить автоматическое масштабирование

## Интеграция с другими модулями

### С temperature_fan

Управление вентилятором на основе температуры ACE:

```ini
[temperature_fan ace_cooling_fan]
sensor_type: temperature_ace
pin: PB15  # Пин вентилятора
min_temp: 0
max_temp: 70
target_temp: 40.0  # Целевая температура
max_speed: 1.0
min_speed: 0.3
control: watermark
```

Вентилятор будет автоматически включаться когда температура ACE превысит 40°C.

### С heater_generic

**Примечание:** ACE не является нагревателем с PWM управлением, поэтому `heater_generic` напрямую не применим. Но можно использовать для мониторинга:

```ini
# Только для мониторинга, НЕ для управления!
[temperature_sensor ace_monitor]
sensor_type: temperature_ace
min_temp: 0
max_temp: 70
```

### С gcode_macro

```gcode
[gcode_macro WAIT_FOR_CHAMBER]
gcode:
    {% set target = params.TARGET|default(30)|float %}
    
    M118 Waiting for chamber temperature: {target}°C
    TEMPERATURE_WAIT SENSOR="temperature_sensor ace_chamber" MINIMUM={target}
    M118 Chamber ready!
```

## Мониторинг сушки

### Автоматический мониторинг процесса сушки

```gcode
[gcode_macro START_DRYING_WITH_MONITOR]
gcode:
    {% set TEMP = params.TEMP|default(50)|int %}
    {% set DURATION = params.DURATION|default(120)|int %}
    
    # Запуск сушки
    ACE_START_DRYING TEMP={TEMP} DURATION={DURATION}
    
    # Запуск мониторинга
    UPDATE_DELAYED_GCODE ID=drying_monitor DURATION=60

[delayed_gcode drying_monitor]
gcode:
    {% set ace_temp = printer["temperature_sensor ace_chamber"].temperature %}
    {% set dryer = printer.ace._info.dryer %}
    
    {% if dryer.status == 'run' %}
        M118 Drying: {ace_temp}°C / {dryer.target_temp}°C
        M118 Remaining: {dryer.remain_time // 60} minutes
        
        # Проверка перегрева
        {% if ace_temp > dryer.target_temp + 10 %}
            M118 Warning: Temperature too high!
            ACE_STOP_DRYING
        {% else %}
            UPDATE_DELAYED_GCODE ID=drying_monitor DURATION=60
        {% endif %}
    {% else %}
        M118 Drying complete, final temperature: {ace_temp}°C
    {% endif %}
```

## Логирование

### Статистика в логах Klipper

Модуль автоматически пишет статистику в лог:

```
Stats 123.4: temperature_ace ace_chamber: temp=28.5
```

Для просмотра:
```bash
journalctl -u klipper | grep "temperature_ace"
```

### Уровни логирования

```python
# Info уровень - при инициализации
"ACE temperature sensor: ACE module found"

# Warning уровень - при проблемах
"ACE temperature sensor: ACE module not found, sensor will report 0"

# Exception уровень - при ошибках чтения
"temperature_ace: Error reading temperature from ACE"
```

## Расширенные примеры

### Управление подогревом камеры

```gcode
[gcode_macro HEAT_CHAMBER]
gcode:
    {% set target = params.TARGET|default(40)|float %}
    
    M118 Heating chamber to {target}°C
    
    # Включить подогрев (ваша логика)
    SET_HEATER_TEMPERATURE HEATER=chamber_heater TARGET={target}
    
    # Ждать достижения температуры
    TEMPERATURE_WAIT SENSOR="temperature_sensor ace_chamber" MINIMUM={target}
    
    M118 Chamber heated to {target}°C
```

### Адаптивное охлаждение

```gcode
[gcode_macro ADAPTIVE_COOLING]
gcode:
    {% set temp = printer["temperature_sensor ace_chamber"].temperature %}
    
    {% if temp < 30 %}
        # Низкая температура - минимальное охлаждение
        M106 S64  # 25% скорость вентилятора
    {% elif temp < 45 %}
        # Средняя температура
        M106 S128  # 50% скорость
    {% else %}
        # Высокая температура - максимальное охлаждение
        M106 S255  # 100% скорость
    {% endif %}
```

### Предварительный прогрев для ABS

```gcode
[gcode_macro PREHEAT_ABS]
gcode:
    M118 Preheating for ABS
    
    # Нагрев стола
    M140 S100
    
    # Включить сушку ACE для прогрева камеры
    ACE_START_DRYING TEMP=50 DURATION=30
    
    # Ждать прогрева камеры
    TEMPERATURE_WAIT SENSOR="temperature_sensor ace_chamber" MINIMUM=35
    
    M118 Chamber preheated, starting print
```

## Сравнение с другими сенсорами

| Сенсор | Источник | Интервал | Применение |
|--------|----------|----------|------------|
| `temperature_ace` | ACE устройство | 1с | Температура внутри ACE |
| `temperature_host` | Raspberry Pi | 1с | Температура хоста |
| `temperature_mcu` | MCU | 0.3с | Температура микроконтроллера |
| `thermistor` | ADC пин | 0.3с | Стол, хотенд, и т.д. |

## Ограничения

1. **Одно устройство ACE**
   - Модуль поддерживает только одно ACE устройство
   - Все сенсоры `temperature_ace` читают из одного источника

2. **Только чтение**
   - Сенсор только отображает температуру
   - Нельзя управлять температурой ACE через этот сенсор
   - Для управления сушкой используйте `ACE_START_DRYING`

3. **Зависимость от ACE модуля**
   - Требуется рабочий модуль `ace.py`
   - Если ACE не подключен - температура будет 0

4. **Разрешение**
   - ACE предоставляет температуру с разрешением 1°C
   - Дробные значения не поддерживаются устройством

## Отладка

### Проверка работы модуля

```gcode
# 1. Проверьте что модуль загружен
# В логах должно быть:
# "ACE temperature sensor: ACE module found"

# 2. Проверьте температуру
ACE_STATUS
# Найдите "temp": <число>

# 3. Проверьте сенсор через Moonraker
# GET http://localhost:7125/printer/objects/query?temperature_sensor
```

### Включение debug логирования

В `printer.cfg`:
```ini
[temperature_sensor ace_debug]
sensor_type: temperature_ace
min_temp: 0
max_temp: 70

# В klippy/extras/temperature_ace.py временно измените:
# logging.info(...) → logging.debug(...)
```

Затем в `moonraker.conf`:
```ini
[debug]
log_level: debug
```

### Проверка значений

```python
# В Klipper console или через SSH
# Подключитесь к Klipper:
~/klippy-env/bin/python ~/klipper/scripts/whconsole.py

# В консоли выполните:
ace = printer.lookup_object('ace')
print(ace._info['temp'])

sensor = printer.lookup_object('temperature_ace ace_chamber')
print(sensor.temp)
```

## Совместимость

| Компонент | Версия | Статус |
|-----------|--------|--------|
| Klipper | Актуальная | ✅ Совместимо |
| Mainsail | Все версии | ✅ Работает |
| Fluidd | Все версии | ✅ Работает |
| KlipperScreen | Все версии | ✅ Работает |
| Moonraker | Все версии | ✅ API поддерживается |

## Дополнительные возможности

### Множественные сенсоры

Можно создать несколько сенсоров для разных целей:

```ini
# Основной мониторинг
[temperature_sensor ace]
sensor_type: temperature_ace
min_temp: 0
max_temp: 70

# Для камеры
[temperature_sensor chamber]
sensor_type: temperature_ace
min_temp: 0
max_temp: 65

# Для сушилки
[temperature_sensor dryer]
sensor_type: temperature_ace
min_temp: 0
max_temp: 60
```

Все они будут показывать одну и ту же температуру, но с разными пределами shutdown.

### История температур

Mainsail/Fluidd автоматически сохраняют историю температур. Можно просмотреть графики за:
- Последний час
- Последние 24 часа
- Пользовательский период

---

## Заключение

Модуль `temperature_ace` обеспечивает полную интеграцию температуры ACE устройства с экосистемой Klipper, позволяя:
- Мониторить температуру в реальном времени
- Использовать в автоматизации и макросах
- Защититься от перегрева
- Отслеживать статистику

---

**Версия:** 1.0  
**Дата:** 2025-01-07  
**Автор:** ValgACE Project  
**Лицензия:** GNU GPLv3

