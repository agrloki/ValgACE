# Функция управления подключением ValgACE

## Описание

Функция управления подключением позволяет управлять подключением к устройству Anycubic Color Engine Pro через G-code команды, REST API и веб-интерфейс. Эта функция предоставляет возможность включать и отключать автоматическое подключение к ACE, а также проверять статус подключения.

## Новые G-code команды

### `ACE_CONNECT`

Включает автоматическое подключение к устройству ACE и пытается подключиться немедленно.

**Синтаксис:**
```
ACE_CONNECT
```

**Описание:**
- Включает флаг автоматического подключения
- Немедленно пытается установить соединение с ACE
- Увеличивает частоту проверки подключения до 1 раза в секунду

**Пример:**
```
ACE_CONNECT
```

**Ответ:**
```
// Если подключение успешно:
// // Automatic connection enabled

// Если подключение не удалось:
// // Automatic connection enabled
// // Connection attempt failed
```

### `ACE_DISCONNECT`

Отключает автоматическое подключение и закрывает текущее соединение с ACE.

**Синтаксис:**
```
ACE_DISCONNECT
```

**Описание:**
- Отключает флаг автоматического подключения
- Закрывает текущее соединение с ACE (если оно установлено)
- Уменьшает частоту проверки подключения до 1 раза в 10 секунд

**Пример:**
```
ACE_DISCONNECT
```

**Ответ:**
```
// Automatic connection disabled and device disconnected
```

### `ACE_CONNECTION_STATUS`

Получает текущий статус подключения к ACE.

**Синтаксис:**
```
ACE_CONNECTION_STATUS
```

**Описание:**
- Возвращает статус подключения (подключен/отключен)
- Возвращает статус автоматического подключения (включено/выключено)

**Пример:**
```
ACE_CONNECTION_STATUS
```

**Ответ:**
```
// Connection status: connected, Auto-connect: enabled
```

## Новые API эндпоинты

### POST `/server/ace/connect`

Включает автоматическое подключение к ACE и пытается подключиться немедленно.

**Метод:** `POST`

**Content-Type:** `application/json`

**Формат запроса:**
```json
{}
```

**Пример запроса:**
```bash
curl -X POST http://localhost:7125/server/ace/connect
```

**Ответ при успехе:**
```json
{
  "success": true,
  "message": "ACE connected successfully"
}
```

**Ответ при ошибке:**
```json
{
  "success": false,
  "error": "Failed to connect to ACE"
}
```

### POST `/server/ace/disconnect`

Отключает автоматическое подключение и закрывает текущее соединение с ACE.

**Метод:** `POST`

**Content-Type:** `application/json`

**Формат запроса:**
```json
{}
```

**Пример запроса:**
```bash
curl -X POST http://localhost:7125/server/ace/disconnect
```

**Ответ при успехе:**
```json
{
  "success": true,
  "message": "ACE disconnected successfully"
}
```

**Ответ при ошибке:**
```json
{
  "success": false,
  "error": "Error message"
}
```

### GET `/server/ace/connection_status`

Получает текущий статус подключения к ACE.

**Метод:** `GET`

**Пример запроса:**
```bash
curl http://localhost:7125/server/ace/connection_status
```

**Ответ при успехе:**
```json
{
  "connected": true,
  "connection_attempts": 1
}
```

**Ответ при ошибке:**
```json
{
  "connected": false,
 "error": "Error message"
}
```

## Новый функционал веб-интерфейса

### Кнопки управления подключением

В веб-интерфейсе добавлены новые элементы управления подключением к ACE:

1. **Кнопка "Подключить"** - включает автоматическое подключение и пытается подключиться к ACE
2. **Кнопка "Отключить"** - отключает автоматическое подключение и закрывает соединение с ACE
3. **Индикатор статуса подключения** - визуально отображает текущий статус подключения

### HTML-структура

```html
<div class="connection-controls">
    <button @click="connectToACE" class="btn btn-primary" :disabled="aceConnectionStatus === 'connecting'">
        {{ t('connectionControls.connect') }}
    </button>
    <button @click="disconnectFromACE" class="btn btn-danger" :disabled="aceConnectionStatus !== 'connected'">
        {{ t('connectionControls.disconnect') }}
    </button>
    <div class="ace-connection-status" 
         :class="{ 
             connected: aceConnectionStatus === 'connected', 
             disconnected: aceConnectionStatus === 'disconnected', 
             connecting: aceConnectionStatus === 'connecting' 
         }">
        <span class="status-dot"></span>
        <span>{{ t('connectionControls.status.' + aceConnectionStatus) }}</span>
    </div>
</div>
```

### Стили

Индикатор статуса подключения имеет три состояния:
- **Подключено** - зеленый цвет фона (#d1fae5)
- **Отключено** - красный цвет фона (#fee2e2)
- **Подключение** - желтый цвет фона (#fef3c7)

### Локализация

Интерфейс поддерживает локализацию для элементов управления подключением:

**Русский:**
```javascript
connectionControls: {
    connect: 'Подключить',
    disconnect: 'Отключить',
    status: {
        connected: 'ACE Подключено',
        disconnected: 'ACE Отключено',
        connecting: 'ACE Подключение...'
    }
}
```

**Английский:**
```javascript
connectionControls: {
    connect: 'Connect',
    disconnect: 'Disconnect',
    status: {
        connected: 'ACE Connected',
        disconnected: 'ACE Disconnected',
        connecting: 'ACE Connecting...'
    }
}
```

## Примеры использования и сценарии применения

### Сценарий 1: Ручное управление подключением

При необходимости временно отключить автоматическое подключение к ACE:

```
; Отключить автоматическое подключение
ACE_DISCONNECT

; Выполнить обслуживание или настройку

; Включить автоматическое подключение обратно
ACE_CONNECT
```

### Сценарий 2: Проверка статуса подключения

Периодическая проверка статуса подключения в процессе печати:

```
; Проверить статус подключения
ACE_CONNECTION_STATUS

; Ответ будет содержать информацию о текущем статусе подключения
; и статусе автоматического подключения
```

### Сценарий 3: Интеграция с внешними системами

Использование API для мониторинга и управления подключением из внешних систем:

```python
import requests

def check_ace_connection():
    response = requests.get('http://localhost:7125/server/ace/connection_status')
    status = response.json()
    return status['connected']

def connect_ace():
    response = requests.post('http://localhost:7125/server/ace/connect')
    return response.json()['success']

def disconnect_ace():
    response = requests.post('http://localhost:7125/server/ace/disconnect')
    return response.json()['success']
```

### Сценарий 4: Использование в веб-интерфейсе

В веб-интерфейсе пользователь может:
1. Нажать кнопку "Подключить" для включения автоматического подключения
2. Наблюдать за статусом подключения в реальном времени
3. Нажать кнопку "Отключить" для отключения от ACE
4. Получать уведомления о статусе подключения

## Технические детали

### Внутренняя реализация

Внутри модуля ACE добавлена переменная `_auto_connect_enabled`, которая управляет поведением проверки подключения. При включении автоматического подключения интервал проверки составляет 1 секунду, при отключении - 10 секунд.

### Обработка ошибок

Все команды и API-эндпоинты включают обработку ошибок и возвращают соответствующие сообщения об ошибках для диагностики проблем с подключением.

### Совместимость

Функция управления подключением полностью совместима с существующими функциями ValgACE и не влияет на нормальную работу других компонентов.