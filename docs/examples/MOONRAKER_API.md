# Moonraker API Extension для ValgACE

Этот файл показывает, как можно расширить Moonraker API для доступа к статусу ACE через REST API.

## Установка

Скопируйте этот файл в `~/moonraker/moonraker/components/ace_status.py`

Добавьте в `moonraker.conf`:

```ini
[components]
ace_status: ace_status
```

## Использование API

После установки будут доступны следующие эндпоинты:

### GET /server/ace/status
Получить полный статус ACE устройства

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
      ...
    ]
  }
}
```

### GET /server/ace/slots
Получить информацию о слотах

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
        "sku": "PLA-RED-01",
        "rfid": 2
      }
    ]
  }
}
```

### POST /server/ace/command
Выполнить команду ACE

**Тело запроса:**
```json
{
  "command": "ACE_CHANGE_TOOL",
  "params": {
    "TOOL": 0
  }
}
```

**Доступные команды:**
- `ACE_CHANGE_TOOL` - смена инструмента
- `ACE_PARK_TO_TOOLHEAD` - парковка филамента
- `ACE_START_DRYING` - запуск сушки
- `ACE_STOP_DRYING` - остановка сушки
- И другие команды ACE_*

**Ответ:**
```json
{
  "result": {
    "success": true,
    "message": "Command executed"
  }
}
```

## WebSocket подписка

Подписка на обновления статуса ACE через WebSocket:

```javascript
// Подключение к Moonraker WebSocket
const ws = new WebSocket('ws://printer.local/websocket');

// Подписка на обновления ACE
ws.send(JSON.stringify({
  jsonrpc: "2.0",
  method: "printer.ace.status.subscribe",
  id: 5434
}));

// Получение обновлений
ws.onmessage = (event) => {
  const data = JSON.parse(event.data);
  if (data.method === "notify_ace_status_update") {
    console.log("ACE Status:", data.params[0]);
  }
};
```

## Пример интеграции в Mainsail/Fluidd

Можно создать кастомный компонент для Mainsail/Fluidd, который будет отображать статус ACE на главной панели принтера.

