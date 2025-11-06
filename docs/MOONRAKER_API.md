# Moonraker API Extension для ValgACE

Подробная документация по компоненту `ace_status.py` - расширению Moonraker API для доступа к статусу ACE через REST API и WebSocket.

## Содержание

1. [Описание](#описание)
2. [Установка](#установка)
3. [Архитектура компонента](#архитектура-компонента)
4. [API Эндпоинты](#api-эндпоинты)
5. [Подробное описание команд](#подробное-описание-команд)
6. [WebSocket подписка](#websocket-подписка)
7. [Примеры использования](#примеры-использования)
8. [Устранение неполадок](#устранение-неполадок)

---

## Описание

Компонент `ace_status.py` расширяет функциональность Moonraker, добавляя REST API эндпоинты для управления и мониторинга устройства ACE (Anycubic Color Engine Pro). Компонент позволяет:

- ✅ Получать статус ACE устройства через HTTP REST API
- ✅ Выполнять команды ACE через HTTP запросы
- ✅ Подписываться на обновления статуса через WebSocket
- ✅ Интегрироваться с веб-интерфейсами (Mainsail, Fluidd, кастомные)

Компонент реализован по паттернам Moonraker API и использует стандартные механизмы для интеграции с Klipper.

---

## Установка

### Автоматическая установка (рекомендуется)

Компонент устанавливается автоматически при выполнении скрипта `install.sh`:

```bash
cd ~/ValgACE
./install.sh
```

**Что делает скрипт:**

1. **Создаёт симлинк:**
   ```bash
   ~/moonraker/moonraker/components/ace_status.py → ~/ValgACE/moonraker/ace_status.py
   ```

2. **Добавляет секцию в `moonraker.conf`:**
   ```ini
   [ace_status]
   ```

3. **Перезапускает Moonraker:**
   ```bash
   sudo systemctl restart moonraker
   ```

### Ручная установка

Если автоматическая установка не подходит:

1. **Скопируйте файл:**
   ```bash
   cp ~/ValgACE/moonraker/ace_status.py ~/moonraker/moonraker/components/ace_status.py
   ```

2. **Добавьте в `moonraker.conf`:**
   ```ini
   [ace_status]
   ```

3. **Перезапустите Moonraker:**
   ```bash
   sudo systemctl restart moonraker
   ```

### Проверка установки

После установки проверьте логи Moonraker:

```bash
tail -f ~/printer_data/logs/moonraker.log | grep -i ace
```

Должно появиться сообщение:
```
ACE Status API extension loaded
```

Проверьте доступность эндпоинта:

```bash
curl http://localhost:7125/server/ace/status
```

---

## Архитектура компонента

### Структура класса `AceStatus`

Компонент состоит из одного класса `AceStatus`, который:

1. **Инициализируется** при загрузке Moonraker
2. **Регистрирует эндпоинты** в Moonraker API
3. **Подписывается на события** обновления статуса принтера
4. **Кэширует данные** для быстрого доступа

### Основные компоненты

#### 1. Инициализация (`__init__`)

```python
def __init__(self, config: ConfigHelper):
    self.server = config.get_server()
    self.klippy_apis = self.server.lookup_component('klippy_apis')
    
    # Регистрация эндпоинтов
    self.server.register_endpoint(...)
    
    # Подписка на события
    self.server.register_event_handler(...)
```

**Что происходит:**
- Получает ссылку на сервер Moonraker
- Получает компонент `klippy_apis` для выполнения G-code команд
- Регистрирует три REST API эндпоинта
- Подписывается на события обновления статуса принтера
- Инициализирует кэш для хранения последнего статуса

#### 2. Получение данных

Компонент использует многоуровневую стратегию получения данных:

1. **Попытка через `query_objects()`** - получение данных напрямую из модуля `ace` через Klipper API
2. **Fallback на кэш** - использование последнего известного статуса
3. **Структура по умолчанию** - возврат пустой структуры, если данных нет

**Почему так:**
- Модуль `ace` может не экспортировать данные в статус принтера автоматически
- Кэш позволяет быстро отвечать на запросы даже при временных проблемах
- Структура по умолчанию гарантирует, что API всегда возвращает валидный JSON

#### 3. Обработка команд

Компонент поддерживает несколько форматов передачи параметров:

1. **JSON body** (рекомендуется):
   ```json
   {"command": "ACE_CHANGE_TOOL", "params": {"TOOL": 0}}
   ```

2. **Query параметры**:
   ```
   ?command=ACE_CHANGE_TOOL&TOOL=0
   ```

3. **Комбинированный формат**:
   ```
   ?command=ACE_CHANGE_TOOL&params={"TOOL":0}
   ```

---

## API Эндпоинты

### GET /server/ace/status

Получить полный статус ACE устройства.

**Запрос:**
```bash
curl http://localhost:7125/server/ace/status
```

**Ответ:**
```json
{
  "result": {
    "status": "ready",
    "model": "Anycubic Color Engine Pro",
    "firmware": "V1.3.84",
    "dryer": {
      "status": "stop",
      "target_temp": 0,
      "duration": 0,
      "remain_time": 0
    },
    "temp": 25,
    "fan_speed": 7000,
    "enable_rfid": 1,
    "slots": [
      {
        "index": 0,
        "status": "ready",
        "type": "PLA",
        "color": [255, 0, 0],
        "sku": "PLA-RED-01",
        "rfid": 2
      },
      {
        "index": 1,
        "status": "ready",
        "type": "PLA",
        "color": [0, 255, 0],
        "sku": "",
        "rfid": 0
      },
      {
        "index": 2,
        "status": "empty",
        "type": "",
        "color": [0, 0, 0],
        "sku": "",
        "rfid": 0
      },
      {
        "index": 3,
        "status": "ready",
        "type": "PETG",
        "color": [0, 0, 255],
        "sku": "",
        "rfid": 1
      }
    ]
  }
}
```

**Поля ответа:**

| Поле | Тип | Описание |
|------|-----|----------|
| `status` | string | Статус устройства: `"ready"`, `"busy"`, `"unknown"` |
| `model` | string | Модель устройства |
| `firmware` | string | Версия прошивки |
| `dryer` | object | Статус сушилки (см. ниже) |
| `temp` | number | Текущая температура сушилки (°C) |
| `fan_speed` | number | Скорость вентилятора (RPM) |
| `enable_rfid` | number | RFID включен (1) или выключен (0) |
| `slots` | array | Массив информации о слотах (см. ниже) |

**Объект `dryer`:**
```json
{
  "status": "stop" | "drying",
  "target_temp": 0-55,
  "duration": 0-1440,
  "remain_time": 0-1440
}
```

**Объект слота:**
```json
{
  "index": 0-3,
  "status": "ready" | "empty" | "busy",
  "type": "PLA" | "PETG" | "ABS" | ...,
  "color": [R, G, B],
  "sku": "string",
  "rfid": 0-3
}
```

**RFID статусы:**
- `0` - Не найдено
- `1` - Ошибка идентификации
- `2` - Идентифицировано
- `3` - Идентификация в процессе

---

### GET /server/ace/slots

Получить информацию только о слотах филамента.

**Запрос:**
```bash
curl http://localhost:7125/server/ace/slots
```

**Ответ:**
```json
{
  "result": {
    "slots": [
      {
        "index": 0,
        "status": "ready",
        "type": "PLA",
        "color": [255, 0, 0],
        "sku": "",
        "rfid": 2
      },
      ...
    ]
  }
}
```

**Использование:**
Удобно для получения только информации о слотах без полного статуса устройства.

---

### POST /server/ace/command

Выполнить команду ACE через REST API.

**Метод:** `POST`  
**Content-Type:** `application/json` (для JSON body) или query параметры

**Формат запроса (JSON body):**
```json
{
  "command": "ACE_COMMAND_NAME",
  "params": {
    "PARAM1": "value1",
    "PARAM2": "value2"
  }
}
```

**Формат запроса (query параметры):**
```
POST /server/ace/command?command=ACE_COMMAND_NAME&PARAM1=value1&PARAM2=value2
```

**Ответ при успехе:**
```json
{
  "result": {
    "success": true,
    "message": "Command ACE_COMMAND_NAME executed successfully",
    "command": "ACE_COMMAND_NAME PARAM1=value1 PARAM2=value2"
  }
}
```

**Ответ при ошибке:**
```json
{
  "result": {
    "success": false,
    "error": "Error message",
    "command": "ACE_COMMAND_NAME PARAM1=value1"
  }
}
```

**Обработка параметров:**

Компонент поддерживает несколько способов передачи параметров:

1. **JSON body с объектом `params`:**
   ```json
   {
     "command": "ACE_FEED",
     "params": {
       "INDEX": 0,
       "LENGTH": 50,
       "SPEED": 25
     }
   }
   ```

2. **Query параметры напрямую:**
   ```
   POST /server/ace/command?command=ACE_FEED&INDEX=0&LENGTH=50&SPEED=25
   ```

3. **Комбинированный формат:**
   ```
   POST /server/ace/command?command=ACE_FEED&params={"INDEX":0,"LENGTH":50,"SPEED":25}
   ```

**Преобразование параметров:**

- Булевы значения (`true`/`false`) преобразуются в `1`/`0`
- Числа преобразуются в строки
- Все параметры объединяются в G-code команду: `COMMAND PARAM1=value1 PARAM2=value2`

---

## Подробное описание команд

### Команды управления инструментом

#### ACE_CHANGE_TOOL

Смена инструмента (загрузка/выгрузка филамента).

**Параметры:**
- `TOOL` (integer, обязательный): Индекс слота (0-3) или `-1` для выгрузки

**Пример:**
```bash
curl -X POST http://localhost:7125/server/ace/command \
  -H "Content-Type: application/json" \
  -d '{"command":"ACE_CHANGE_TOOL","params":{"TOOL":0}}'
```

**Что происходит:**
1. Выполняется макрос `_ACE_PRE_TOOLCHANGE`
2. Откат филамента из предыдущего слота (если был)
3. Ожидание готовности слота
4. Парковка филамента нового слота к хотэнду
5. Выполнение макроса `_ACE_POST_TOOLCHANGE`

---

#### ACE_PARK_TO_TOOLHEAD

Парковка филамента выбранного слота к хотэнду.

**Параметры:**
- `INDEX` (integer, обязательный): Индекс слота (0-3)

**Пример:**
```bash
curl -X POST http://localhost:7125/server/ace/command \
  -H "Content-Type: application/json" \
  -d '{"command":"ACE_PARK_TO_TOOLHEAD","params":{"INDEX":0}}'
```

**Что происходит:**
1. Проверка готовности слота
2. Запуск feed assist для слота
3. Мониторинг счетчика `feed_assist_count`
4. Автоматическое завершение при достижении `park_hit_count` стабильных проверок
5. Остановка feed assist

**Особенности:**
- Использует асинхронный мониторинг через `_handle_response`
- Автоматически определяет завершение парковки
- Обрабатывает ошибки (например, если feed assist не работает)

---

### Команды управления подачей

#### ACE_FEED

Подача филамента на заданную длину.

**Параметры:**
- `INDEX` (integer, обязательный): Индекс слота (0-3)
- `LENGTH` (integer, обязательный): Длина подачи в мм
- `SPEED` (integer, опциональный): Скорость подачи (мм/с), по умолчанию из конфига

**Пример:**
```bash
curl -X POST http://localhost:7125/server/ace/command \
  -H "Content-Type: application/json" \
  -d '{"command":"ACE_FEED","params":{"INDEX":0,"LENGTH":50,"SPEED":25}}'
```

---

#### ACE_RETRACT

Откат филамента на заданную длину.

**Параметры:**
- `INDEX` (integer, обязательный): Индекс слота (0-3)
- `LENGTH` (integer, обязательный): Длина отката в мм
- `SPEED` (integer, опциональный): Скорость отката (мм/с), по умолчанию из конфига
- `MODE` (integer, опциональный): Режим отката (0=обычный, 1=улучшенный), по умолчанию из конфига

**Пример:**
```bash
curl -X POST http://localhost:7125/server/ace/command \
  -H "Content-Type: application/json" \
  -d '{"command":"ACE_RETRACT","params":{"INDEX":0,"LENGTH":50,"SPEED":25,"MODE":0}}'
```

---

#### ACE_UPDATE_FEEDING_SPEED

Обновление скорости подачи во время работы.

**Параметры:**
- `INDEX` (integer, обязательный): Индекс слота (0-3)
- `SPEED` (integer, обязательный): Новая скорость подачи (мм/с)

**Пример:**
```bash
curl -X POST http://localhost:7125/server/ace/command \
  -H "Content-Type: application/json" \
  -d '{"command":"ACE_UPDATE_FEEDING_SPEED","params":{"INDEX":0,"SPEED":30}}'
```

---

#### ACE_UPDATE_RETRACT_SPEED

Обновление скорости отката во время работы.

**Параметры:**
- `INDEX` (integer, обязательный): Индекс слота (0-3)
- `SPEED` (integer, обязательный): Новая скорость отката (мм/с)

**Пример:**
```bash
curl -X POST http://localhost:7125/server/ace/command \
  -H "Content-Type: application/json" \
  -d '{"command":"ACE_UPDATE_RETRACT_SPEED","params":{"INDEX":0,"SPEED":30}}'
```

---

#### ACE_STOP_FEED

Остановка подачи филамента.

**Параметры:**
- `INDEX` (integer, обязательный): Индекс слота (0-3)

**Пример:**
```bash
curl -X POST http://localhost:7125/server/ace/command \
  -H "Content-Type: application/json" \
  -d '{"command":"ACE_STOP_FEED","params":{"INDEX":0}}'
```

---

#### ACE_STOP_RETRACT

Остановка отката филамента.

**Параметры:**
- `INDEX` (integer, обязательный): Индекс слота (0-3)

**Пример:**
```bash
curl -X POST http://localhost:7125/server/ace/command \
  -H "Content-Type: application/json" \
  -d '{"command":"ACE_STOP_RETRACT","params":{"INDEX":0}}'
```

---

### Команды управления сушкой

#### ACE_START_DRYING

Запуск процесса сушки филамента.

**Параметры:**
- `TEMP` (integer, обязательный): Целевая температура (20-55°C, ограничено `max_dryer_temperature`)
- `DURATION` (integer, обязательный): Длительность сушки в минутах

**Пример:**
```bash
curl -X POST http://localhost:7125/server/ace/command \
  -H "Content-Type: application/json" \
  -d '{"command":"ACE_START_DRYING","params":{"TEMP":50,"DURATION":240}}'
```

**Что происходит:**
- Устанавливается целевая температура
- Включается вентилятор (7000 RPM)
- Запускается таймер на указанное время
- Статус сушки обновляется в реальном времени

---

#### ACE_STOP_DRYING

Остановка процесса сушки.

**Параметры:** Нет

**Пример:**
```bash
curl -X POST http://localhost:7125/server/ace/command \
  -H "Content-Type: application/json" \
  -d '{"command":"ACE_STOP_DRYING"}'
```

---

### Команды управления feed assist

#### ACE_ENABLE_FEED_ASSIST

Включение feed assist для слота (автоматическая подача при печати).

**Параметры:**
- `INDEX` (integer, обязательный): Индекс слота (0-3)

**Пример:**
```bash
curl -X POST http://localhost:7125/server/ace/command \
  -H "Content-Type: application/json" \
  -d '{"command":"ACE_ENABLE_FEED_ASSIST","params":{"INDEX":0}}'
```

**Использование:**
Обычно включается автоматически при смене инструмента, но можно включить вручную для непрерывной подачи.

---

#### ACE_DISABLE_FEED_ASSIST

Выключение feed assist для слота.

**Параметры:**
- `INDEX` (integer, опциональный): Индекс слота (0-3), по умолчанию текущий активный

**Пример:**
```bash
curl -X POST http://localhost:7125/server/ace/command \
  -H "Content-Type: application/json" \
  -d '{"command":"ACE_DISABLE_FEED_ASSIST","params":{"INDEX":0}}'
```

---

### Команды режима Infinity Spool

#### ACE_SET_INFINITY_SPOOL_ORDER

Установка порядка смены слотов для режима бесконечной катушки.

**Параметры:**
- `ORDER` (string, обязательный): Порядок слотов в формате `"0,1,2,3"` или `"0,1,none,3"` (используйте `none` для пустых слотов)

**Пример:**
```bash
curl -X POST http://localhost:7125/server/ace/command \
  -H "Content-Type: application/json" \
  -d '{"command":"ACE_SET_INFINITY_SPOOL_ORDER","params":{"ORDER":"0,1,none,3"}}'
```

**Что происходит:**
- Сохраняется порядок в переменную `ace_infsp_order`
- Сбрасывается позиция в переменную `ace_infsp_position = 0`
- Порядок используется при выполнении `ACE_INFINITY_SPOOL`

---

#### ACE_INFINITY_SPOOL

Автоматическая смена катушки при окончании филамента (без отката).

**Параметры:** Нет

**Пример:**
```bash
curl -X POST http://localhost:7125/server/ace/command \
  -H "Content-Type: application/json" \
  -d '{"command":"ACE_INFINITY_SPOOL"}'
```

**Что происходит:**
1. Проверка включенности режима `infinity_spool_mode`
2. Определение текущего слота из переменной `ace_current_index`
3. Поиск следующего слота в порядке `ace_infsp_order`
4. Пропуск слотов со значением `none`
5. Выполнение макроса `_ACE_PRE_INFINITYSPOOL`
6. Парковка филамента нового слота
7. Выполнение макроса `_ACE_POST_INFINITYSPOOL`
8. Сохранение нового текущего слота и позиции

**Требования:**
- Режим `infinity_spool_mode` должен быть включен в конфигурации
- Порядок должен быть установлен через `ACE_SET_INFINITY_SPOOL_ORDER`
- Минимум один слот в порядке должен быть готов (`ready`)

---

### Информационные команды

#### ACE_STATUS

Получение статуса устройства (через G-code, не через API).

**Параметры:** Нет

**Примечание:** Для получения статуса через API используйте `GET /server/ace/status`

---

#### ACE_FILAMENT_INFO

Получение информации о филаменте в слоте.

**Параметры:**
- `INDEX` (integer, обязательный): Индекс слота (0-3)

**Пример:**
```bash
curl -X POST http://localhost:7125/server/ace/command \
  -H "Content-Type: application/json" \
  -d '{"command":"ACE_FILAMENT_INFO","params":{"INDEX":0}}'
```

---

#### ACE_DEBUG

Отладочная команда для прямого вызова методов ACE API.

**Параметры:**
- `METHOD` (string, обязательный): Имя метода ACE API (например, `"get_info"`, `"get_status"`)
- `PARAMS` (string, опциональный): JSON строка с параметрами метода

**Пример:**
```bash
curl -X POST http://localhost:7125/server/ace/command \
  -H "Content-Type: application/json" \
  -d '{"command":"ACE_DEBUG","params":{"METHOD":"get_info","PARAMS":"{}"}}'
```

---

## WebSocket подписка

Компонент поддерживает подписку на обновления статуса ACE через WebSocket.

### Подключение к WebSocket

```javascript
const ws = new WebSocket('ws://localhost:7125/websocket');

ws.onopen = () => {
    console.log('WebSocket connected');
};
```

### Подписка на обновления статуса принтера

```javascript
ws.send(JSON.stringify({
    jsonrpc: "2.0",
    method: "printer.objects.subscribe",
    params: {
        objects: {
            "ace": null
        }
    },
    id: 5434
}));
```

### Получение обновлений

```javascript
ws.onmessage = (event) => {
    const data = JSON.parse(event.data);
    
    if (data.method === "notify_status_update") {
        const aceData = data.params[0]?.ace;
        if (aceData) {
            console.log('ACE Status Update:', aceData);
            // Обновить UI
            updateAceUI(aceData);
        }
    }
    
    // Событие от компонента ace_status
    if (data.method === "notify_ace_status_update") {
        const aceData = data.params[0];
        console.log('ACE Status Update:', aceData);
        updateAceUI(aceData);
    }
};
```

### События компонента

Компонент отправляет событие `ace:status_update` при обновлении статуса:

```javascript
// Событие отправляется через:
self.server.send_event("ace:status_update", ace_data)
```

---

## Примеры использования

### JavaScript/TypeScript

#### Получение статуса

```javascript
async function getAceStatus() {
    const response = await fetch('http://localhost:7125/server/ace/status');
    const data = await response.json();
    return data.result;
}

// Использование
const status = await getAceStatus();
console.log('ACE Status:', status);
console.log('Slots:', status.slots);
```

#### Выполнение команды

```javascript
async function executeAceCommand(command, params = {}) {
    const response = await fetch('http://localhost:7125/server/ace/command', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ command, params })
    });
    return await response.json();
}

// Пример: смена инструмента
const result = await executeAceCommand('ACE_CHANGE_TOOL', { TOOL: 0 });
if (result.result.success) {
    console.log('Tool changed successfully');
} else {
    console.error('Error:', result.result.error);
}
```

#### Мониторинг статуса в реальном времени

```javascript
class AceStatusMonitor {
    constructor(url = 'ws://localhost:7125/websocket') {
        this.ws = new WebSocket(url);
        this.setupWebSocket();
    }
    
    setupWebSocket() {
        this.ws.onopen = () => {
            // Подписка на обновления
            this.ws.send(JSON.stringify({
                jsonrpc: "2.0",
                method: "printer.objects.subscribe",
                params: {
                    objects: { "ace": null }
                },
                id: 5434
            }));
        };
        
        this.ws.onmessage = (event) => {
            const data = JSON.parse(event.data);
            if (data.method === "notify_status_update") {
                const aceData = data.params[0]?.ace;
                if (aceData) {
                    this.onStatusUpdate(aceData);
                }
            }
        };
    }
    
    onStatusUpdate(data) {
        console.log('Status updated:', data);
        // Обновить UI
    }
}

// Использование
const monitor = new AceStatusMonitor();
```

### Python

#### Получение статуса

```python
import requests

def get_ace_status():
    response = requests.get('http://localhost:7125/server/ace/status')
    return response.json()['result']

# Использование
status = get_ace_status()
print(f"ACE Status: {status['status']}")
print(f"Slots: {len(status['slots'])}")
```

#### Выполнение команды

```python
import requests

def execute_ace_command(command, params=None):
    url = 'http://localhost:7125/server/ace/command'
    data = {'command': command}
    if params:
        data['params'] = params
    
    response = requests.post(url, json=data)
    return response.json()['result']

# Пример: парковка филамента
result = execute_ace_command('ACE_PARK_TO_TOOLHEAD', {'INDEX': 0})
if result['success']:
    print('Command executed successfully')
else:
    print(f"Error: {result['error']}")
```

### cURL

#### Получение статуса

```bash
curl http://localhost:7125/server/ace/status
```

#### Получение слотов

```bash
curl http://localhost:7125/server/ace/slots
```

#### Смена инструмента

```bash
curl -X POST http://localhost:7125/server/ace/command \
  -H "Content-Type: application/json" \
  -d '{"command":"ACE_CHANGE_TOOL","params":{"TOOL":0}}'
```

#### Парковка филамента

```bash
curl -X POST http://localhost:7125/server/ace/command \
  -H "Content-Type: application/json" \
  -d '{"command":"ACE_PARK_TO_TOOLHEAD","params":{"INDEX":0}}'
```

#### Запуск сушки

```bash
curl -X POST http://localhost:7125/server/ace/command \
  -H "Content-Type: application/json" \
  -d '{"command":"ACE_START_DRYING","params":{"TEMP":50,"DURATION":240}}'
```

#### Установка порядка Infinity Spool

```bash
curl -X POST http://localhost:7125/server/ace/command \
  -H "Content-Type: application/json" \
  -d '{"command":"ACE_SET_INFINITY_SPOOL_ORDER","params":{"ORDER":"0,1,none,3"}}'
```

---

## Устранение неполадок

### Компонент не загружается

**Симптомы:**
- В логах Moonraker нет сообщения "ACE Status API extension loaded"
- Эндпоинты недоступны (404 или 405 ошибка)

**Решения:**

1. **Проверьте наличие файла:**
   ```bash
   ls -la ~/moonraker/moonraker/components/ace_status.py
   ```

2. **Проверьте секцию в moonraker.conf:**
   ```bash
   grep -A 1 "\[ace_status\]" ~/printer_data/config/moonraker.conf
   ```

3. **Проверьте логи Moonraker на ошибки:**
   ```bash
   tail -f ~/printer_data/logs/moonraker.log | grep -i error
   ```

4. **Проверьте синтаксис Python файла:**
   ```bash
   python3 -m py_compile ~/moonraker/moonraker/components/ace_status.py
   ```

---

### Эндпоинты возвращают ошибку 404

**Причина:** Компонент не загружен или путь неправильный.

**Решение:**
1. Убедитесь, что файл существует и является симлинком
2. Перезапустите Moonraker: `sudo systemctl restart moonraker`
3. Проверьте логи на наличие ошибок загрузки

---

### Эндпоинты возвращают ошибку 405 (Method Not Allowed)

**Причина:** Используется неправильный HTTP метод.

**Решение:**
- `/server/ace/status` - используйте `GET`
- `/server/ace/slots` - используйте `GET`
- `/server/ace/command` - используйте `POST`

---

### Команды не выполняются

**Симптомы:**
- Запрос возвращает `{"success": false, "error": "..."}`

**Решения:**

1. **Проверьте формат команды:**
   ```bash
   # Правильно
   curl -X POST http://localhost:7125/server/ace/command \
     -H "Content-Type: application/json" \
     -d '{"command":"ACE_CHANGE_TOOL","params":{"TOOL":0}}'
   
   # Неправильно (GET вместо POST)
   curl http://localhost:7125/server/ace/command?command=ACE_CHANGE_TOOL
   ```

2. **Проверьте параметры команды:**
   - Убедитесь, что все обязательные параметры переданы
   - Проверьте типы параметров (числа должны быть числами, не строками)

3. **Проверьте логи Klipper:**
   ```bash
   tail -f ~/printer_data/logs/klippy.log | grep -i ace
   ```

---

### Статус всегда возвращает структуру по умолчанию

**Причина:** Модуль `ace` не экспортирует данные в статус принтера.

**Решение:**
Это нормальное поведение, если модуль `ace` не настроен на экспорт данных. Компонент использует fallback стратегию:
1. Попытка получить данные через `query_objects()`
2. Использование кэша
3. Возврат структуры по умолчанию

Для получения реальных данных можно:
- Модифицировать модуль `ace.py` для экспорта данных в статус
- Использовать G-code команду `ACE_STATUS` и парсить текстовый ответ (требует доработки компонента)

---

### WebSocket не получает обновления

**Причина:** Модуль `ace` не отправляет события обновления статуса.

**Решение:**
1. Убедитесь, что модуль `ace` экспортирует данные в статус принтера
2. Проверьте подписку на события в компоненте
3. Проверьте логи Moonraker на наличие событий

---

## Дополнительная информация

### Интеграция с веб-интерфейсами

Компонент можно использовать с:
- **Mainsail** - через кастомные компоненты
- **Fluidd** - через кастомные компоненты
- **Кастомные веб-интерфейсы** - через REST API и WebSocket

Примеры интеграции см. в `docs/examples/` (если доступны).

### Производительность

- **Кэширование:** Компонент кэширует последний известный статус для быстрого ответа
- **Асинхронность:** Все операции асинхронные, не блокируют Moonraker
- **Обработка ошибок:** Все ошибки логируются и возвращаются в ответе API

### Безопасность

- Компонент использует стандартные механизмы безопасности Moonraker
- Все команды выполняются через Klipper API с проверкой прав доступа
- Параметры валидируются перед выполнением команд

---

## См. также

- [Руководство по установке](../INSTALLATION.md) - установка ValgACE
- [Справочник команд](../COMMANDS.md) - все команды G-code ACE
- [Руководство по конфигурации](../CONFIGURATION.md) - настройка параметров
- [Протокол ACE](../Protocol.md) - техническая документация протокола

---

*Дата последнего обновления: 2024*

