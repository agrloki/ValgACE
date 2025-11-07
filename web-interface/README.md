# ValgACE Dashboard - Веб-интерфейс для управления ACE

Современный веб-интерфейс для управления и мониторинга устройства Anycubic Color Engine Pro через Moonraker API.

## Описание

ValgACE Dashboard - это полнофункциональный веб-интерфейс, который предоставляет:

- ✅ **Мониторинг статуса** - отображение статуса устройства в реальном времени
- ✅ **Управление слотами** - загрузка/выгрузка филамента, парковка к хотэнду
- ✅ **Feed Assist** - включение/выключение feed assist для каждого слота с визуальной индикацией состояния
- ✅ **Управление сушкой** - запуск и остановка процесса сушки филамента
- ✅ **Подача/откат филамента** - ручное управление подачей и откатом
- ✅ **WebSocket подключение** - обновления статуса в реальном времени
- ✅ **Адаптивный дизайн** - работает на десктопе и мобильных устройствах

## Файлы

- `ace-dashboard.html` - основной HTML файл с Vue.js компонентом
- `ace-dashboard.css` - стили интерфейса
- `ace-dashboard.js` - логика работы с API и WebSocket
- `ace-dashboard-config.js` - файл конфигурации для настройки адреса Moonraker
- `nginx.conf.example` - пример конфигурации nginx для хостинга

## Установка

### Вариант 1: Локальный файл (для тестирования)

1. Скопируйте все файлы в одну папку:
   ```bash
   mkdir -p ~/ace-dashboard
   cp ~/ValgACE/web-interface/ace-dashboard.* ~/ace-dashboard/
   ```

2. Откройте `ace-dashboard.html` в браузере через веб-сервер (не через `file://`)

   **Важно:** Для работы через `file://` нужно настроить CORS или использовать веб-сервер.

### Вариант 2: Интеграция с Mainsail/Fluidd

#### Для Mainsail:

1. Скопируйте файлы в папку Mainsail:
   ```bash
   cp ace-dashboard.html ~/mainsail/src/dashboard/
   cp ace-dashboard.css ~/mainsail/src/dashboard/
   cp ace-dashboard.js ~/mainsail/src/dashboard/
   ```

2. Добавьте ссылку в навигацию Mainsail (требует модификации исходного кода)

#### Для Fluidd:

1. Скопируйте файлы в папку Fluidd:
   ```bash
   cp ace-dashboard.html ~/fluidd/dist/
   cp ace-dashboard.css ~/fluidd/dist/
   cp ace-dashboard.js ~/fluidd/dist/
   ```

2. Добавьте ссылку в навигацию Fluidd

### Вариант 3: Отдельный веб-сервер

1. Установите простой HTTP сервер:
   ```bash
   # Python 3
   python3 -m http.server 8080
   
   # Или Node.js
   npx http-server -p 8080
   ```

2. Откройте в браузере: `http://localhost:8080/ace-dashboard.html`

### Вариант 3: Nginx (рекомендуется для постоянного использования)

1. Скопируйте файлы в директорию веб-сервера:
   ```bash
   sudo cp ace-dashboard.* /var/www/ace-dashboard/
   ```

2. Используйте пример конфигурации из `nginx.conf.example`:
   ```bash
   sudo cp nginx.conf.example /etc/nginx/sites-available/ace-dashboard
   sudo nano /etc/nginx/sites-available/ace-dashboard  # Отредактируйте пути
   sudo ln -s /etc/nginx/sites-available/ace-dashboard /etc/nginx/sites-enabled/
   sudo nginx -t
   sudo systemctl reload nginx
   ```

Подробнее см. `nginx.conf.example` с комментариями.

## Использование

### Подключение

Интерфейс автоматически подключается к Moonraker API по адресу текущего хоста. Если вы открываете файл локально, убедитесь, что:

1. Moonraker запущен и доступен
2. Компонент `ace_status.py` установлен и загружен
3. Браузер может обращаться к Moonraker API (CORS настроен)

### Настройка адреса API

Отредактируйте файл `ace-dashboard-config.js`:

```javascript
const ACE_DASHBOARD_CONFIG = {
    // Укажите адрес Moonraker API
    apiBase: 'http://192.168.1.100:7125',  // Ваш IP адрес Moonraker
    
    // Или используйте автоматическое определение (по умолчанию)
    // apiBase: window.location.origin,
    
    // Остальные настройки...
};
```

Подробнее см. комментарии в файле `ace-dashboard-config.js`.

### Основные функции

#### Мониторинг статуса

- Статус устройства отображается в верхней части интерфейса
- Индикатор подключения показывает состояние WebSocket соединения
- Автоматическое обновление каждые 5 секунд

#### Управление слотами

- **Загрузить** - загружает филамент из слота (выполняет `ACE_CHANGE_TOOL`)
- **Парковка** - паркует филамент к хотэнду (выполняет `ACE_PARK_TO_TOOLHEAD`)
- **Асист** - включает/выключает feed assist для слота (`ACE_ENABLE_FEED_ASSIST` / `ACE_DISABLE_FEED_ASSIST`)
  - Кнопка зеленая с текстом "Асист ВКЛ" когда feed assist активен для этого слота
  - Кнопка с зеленой обводкой "Асист" когда неактивен
  - При включении нового слота автоматически выключается предыдущий
- **Подача** - открывает диалог для подачи филамента на заданную длину
- **Откат** - открывает диалог для отката филамента на заданную длину

#### Управление сушкой

1. Установите целевую температуру (20-55°C)
2. Установите длительность сушки (в минутах)
3. Нажмите "Запустить сушку"
4. Для остановки нажмите "Остановить"

#### Быстрые действия

- **Выгрузить филамент** - выгружает текущий филамент (`ACE_CHANGE_TOOL TOOL=-1`)
- **Обновить статус** - принудительно обновляет статус устройства

## API Эндпоинты

Интерфейс использует следующие эндпоинты Moonraker:

- `GET /server/ace/status` - получение статуса устройства
- `POST /server/ace/command` - выполнение команд ACE

## WebSocket

Интерфейс подключается к WebSocket Moonraker для получения обновлений в реальном времени:

```javascript
ws://your-moonraker-host:7125/websocket
```

Подписка на обновления статуса ACE выполняется автоматически при подключении.

## Требования

- Современный браузер с поддержкой ES6 и WebSocket
- Vue.js 3 (загружается из CDN)
- Доступ к Moonraker API
- Установленный компонент `ace_status.py`

## Устранение неполадок

### Интерфейс не подключается к API

1. Проверьте, что Moonraker запущен:
   ```bash
   systemctl status moonraker
   ```

2. Проверьте, что компонент `ace_status.py` загружен:
   ```bash
   grep -i "ace_status" ~/printer_data/logs/moonraker.log
   ```

3. Проверьте доступность API:
   ```bash
   curl http://localhost:7125/server/ace/status
   ```

### WebSocket не подключается

1. Проверьте, что Moonraker доступен по WebSocket:
   ```bash
   wscat -c ws://localhost:7125/websocket
   ```

2. Проверьте настройки CORS в `moonraker.conf`:
   ```ini
   [cors_domains]
   *.local
   *.lan
   *:*
   ```

### Команды не выполняются

1. Проверьте логи Moonraker:
   ```bash
   tail -f ~/printer_data/logs/moonraker.log
   ```

2. Проверьте логи Klipper:
   ```bash
   tail -f ~/printer_data/logs/klippy.log | grep -i ace
   ```

3. Убедитесь, что команды правильно формируются (проверьте консоль браузера)

### Включение отладки

Для диагностики проблем включите отладку в `ace-dashboard-config.js`:

```javascript
const ACE_DASHBOARD_CONFIG = {
    // ...
    debug: true,  // Включить отладочные сообщения
    // ...
};
```

После этого откройте консоль браузера (F12) и проверьте сообщения при загрузке статуса и выполнении команд.

## Кастомизация

### Изменение цветов

Отредактируйте `ace-dashboard.css` для изменения цветовой схемы:

```css
/* Основной цвет */
.btn-primary {
    background: #667eea;  /* Измените на свой цвет */
}
```

### Добавление новых функций

Отредактируйте `ace-dashboard.js` для добавления новых команд или функций.

## Безопасность

⚠️ **Важно:** Этот интерфейс выполняет команды напрямую через Moonraker API. Убедитесь, что:

1. Доступ к интерфейсу ограничен локальной сетью
2. Moonraker настроен с правильными настройками безопасности
3. Не используйте интерфейс в публичной сети без защиты

## Лицензия

Проект распространяется под лицензией [GNU GPL v3](../../LICENSE.md).

## Поддержка

При возникновении проблем:

1. Проверьте [документацию Moonraker API](../MOONRAKER_API.md)
2. Проверьте логи Moonraker и Klipper
3. Создайте issue на GitHub: https://github.com/agrloki/ValgACE/issues

---

*Последнее обновление: 2024*

