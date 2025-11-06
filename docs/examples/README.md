# Примеры интеграции ValgACE с Moonraker API

Этот раздел содержит примеры интеграции ValgACE с Moonraker API для создания веб-интерфейса и доступа к статусу ACE через REST API.

## Содержание

1. [Moonraker API Extension](#moonraker-api-extension)
2. [Веб-интерфейс](#веб-интерфейс)
3. [Кастомный компонент для Mainsail/Fluidd](#кастомный-компонент-для-mainsailfluidd)
4. [Использование](#использование)

---

## Moonraker API Extension

### Описание

Компонент `ace_status.py` расширяет Moonraker API, добавляя эндпоинты для доступа к статусу ACE устройства через REST API и WebSocket.

### Установка

1. Скопируйте файл `ace_status.py` в директорию Moonraker:
```bash
cp docs/examples/ace_status.py ~/moonraker/moonraker/components/ace_status.py
```

2. Добавьте в `moonraker.conf`:
```ini
[components]
ace_status: ace_status
```

3. Перезапустите Moonraker:
```bash
sudo systemctl restart moonraker
```

### Доступные эндпоинты

#### GET /server/ace/status

Получить полный статус ACE устройства.

**Пример запроса:**
```bash
curl http://printer.local/server/ace/status
```

**Пример ответа:**
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
      }
    ]
  }
}
```

#### GET /server/ace/slots

Получить информацию только о слотах.

**Пример запроса:**
```bash
curl http://printer.local/server/ace/slots
```

#### POST /server/ace/command

Выполнить команду ACE.

**Пример запроса:**
```bash
curl -X POST http://printer.local/server/ace/command \
  -H "Content-Type: application/json" \
  -d '{
    "command": "ACE_CHANGE_TOOL",
    "params": {
      "TOOL": 0
    }
  }'
```

**Доступные команды:**
- `ACE_CHANGE_TOOL` - смена инструмента
- `ACE_PARK_TO_TOOLHEAD` - парковка филамента
- `ACE_START_DRYING` - запуск сушки
- `ACE_STOP_DRYING` - остановка сушки
- `ACE_FEED` - подача филамента
- `ACE_RETRACT` - откат филамента
- И другие команды ACE_*

---

## Веб-интерфейс

### Описание

Файл `ace_dashboard.html` содержит пример веб-интерфейса для отображения статуса ACE устройства в реальном времени.

### Особенности

- ✅ Real-time обновления через WebSocket
- ✅ Отображение статуса устройства
- ✅ Информация о слотах филамента
- ✅ Управление сушкой
- ✅ Адаптивный дизайн
- ✅ Красивый современный UI

### Установка

1. Скопируйте файл `ace_dashboard.html` в директорию веб-сервера Moonraker:
```bash
cp docs/examples/ace_dashboard.html ~/printer_data/web/ace_dashboard.html
```

2. Откройте в браузере:
```
http://printer.local/ace_dashboard.html
```

Или добавьте как кастомную страницу в Mainsail/Fluidd.

### Использование

Веб-интерфейс автоматически:
- Подключается к Moonraker WebSocket
- Загружает статус ACE через REST API
- Обновляет данные каждые 5 секунд
- Отображает информацию о всех слотах
- Позволяет управлять сушкой

---

## Кастомный компонент для Mainsail/Fluidd

### Описание

Компонент `AceStatusCard.vue` - это полнофункциональный Vue компонент для интеграции в Mainsail или Fluidd, который отображает статус ACE прямо на главной панели принтера.

### Особенности

- ✅ Real-time обновления через WebSocket
- ✅ Отображение статуса устройства и всех слотов
- ✅ Быстрое переключение между слотами
- ✅ Информация о сушке филамента
- ✅ Адаптивный дизайн
- ✅ Интеграция с системой уведомлений Mainsail/Fluidd

### Файлы

- **`AceStatusCard.vue`** - основной Vue компонент
- **`MAINSAIL_FLUIDD_COMPONENT.md`** - подробная инструкция по установке и использованию
- **`SIMPLE_INTEGRATION.md`** - упрощенные варианты интеграции

### Быстрая установка

1. Скопируйте `AceStatusCard.vue` в директорию компонентов Mainsail/Fluidd
2. Зарегистрируйте компонент в `main.js`
3. Добавьте на главную страницу Dashboard

Подробные инструкции см. в [MAINSAIL_FLUIDD_COMPONENT.md](MAINSAIL_FLUIDD_COMPONENT.md).

---

### Получение статуса через REST API

```javascript
async function getAceStatus() {
    const response = await fetch('http://printer.local/server/ace/status');
    const data = await response.json();
    return data.result;
}

// Использование
const status = await getAceStatus();
console.log('ACE Status:', status);
console.log('Slots:', status.slots);
```

### Выполнение команды

```javascript
async function executeAceCommand(command, params = {}) {
    const response = await fetch('http://printer.local/server/ace/command', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ command, params })
    });
    return await response.json();
}

// Пример: смена инструмента
await executeAceCommand('ACE_CHANGE_TOOL', { TOOL: 0 });

// Пример: запуск сушки
await executeAceCommand('ACE_START_DRYING', { TEMP: 50, DURATION: 240 });
```

### WebSocket подписка на обновления

```javascript
const ws = new WebSocket('ws://printer.local:7125/websocket');

ws.onopen = () => {
    // Подписка на обновления статуса принтера
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
};

ws.onmessage = (event) => {
    const data = JSON.parse(event.data);
    if (data.method === "notify_status_update") {
        const aceData = data.params[0].ace;
        if (aceData) {
            console.log('ACE Status Update:', aceData);
            // Обновить UI
            updateAceUI(aceData);
        }
    }
};
```

---

## Интеграция с Mainsail/Fluidd

Для интеграции с Mainsail/Fluidd используйте готовый компонент `AceStatusCard.vue`. 

См. раздел [Кастомный компонент для Mainsail/Fluidd](#кастомный-компонент-для-mainsailfluidd) выше или подробную инструкцию в [MAINSAIL_FLUIDD_COMPONENT.md](MAINSAIL_FLUIDD_COMPONENT.md).

---

## Примечания

⚠️ **Важно:**

1. Компонент `ace_status.py` является примером и требует доработки для получения данных напрямую из модуля `ace` через `printer.lookup_object('ace')`.

2. В текущей реализации используется выполнение G-code команд, что менее эффективно, чем прямой доступ к данным модуля.

3. Для production использования рекомендуется:
   - Получать данные напрямую из модуля `ace`
   - Добавить обработку ошибок
   - Добавить кэширование данных
   - Оптимизировать WebSocket подписки

4. Веб-интерфейс требует настройки CORS в Moonraker, если используется на другом домене.

---

## Дополнительные ресурсы

- [Moonraker API Documentation](https://moonraker.readthedocs.io/en/stable/web_api/)
- [ValgACE Commands Reference](../COMMANDS.md)
- [ValgACE Configuration Guide](../CONFIGURATION.md)

---

*Дата создания: 2024*

