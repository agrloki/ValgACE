# ValgACE - Драйвер для Anycubic Color Engine Pro

[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)

**ValgACE** - модуль для Klipper, обеспечивающий полное управление устройством автоматической смены филамента Anycubic Color Engine Pro (ACE Pro).

**ace-solo** [ace-solo](https://github.com/agrloki/ace-solo) Автономное приложение на Python для управления Anycubic ACE Pro без использования Klipper.

**acepro-mmu-dashboard** [acepro-mmu-dashboard](https://github.com/ducati1198/acepro-mmu-dashboard) Альтернативный вэб интерфейс от @ducati1198
## 📋 Содержание

- [Описание](#описание)
- [Возможности](#возможности)
- [Системные требования](#системные-требования)
- [Быстрый старт](#быстрый-старт)
- [Подключение устройства](#подключение-устройства)
- [Документация](#документация)
- [Поддержка](#поддержка)
- [Благодарности](#благодарности)

## Описание

ValgACE представляет собой полнофункциональный драйвер для управления устройством Anycubic Color Engine Pro через Klipper. Драйвер обеспечивает автоматическую смену филамента между 4 слотами, управление сушкой, подачу и откат филамента, а также поддержку RFID меток.

### Статус проекта

**Статус:** Стабильная версия

**Подтверждено на:** Sovol SV08, Kingroon KLP1, Kingroon KP3S Pro V2, custom klipper 3d printers.

**Основан на:** [DuckACE](https://github.com/utkabobr/DuckACE) 

**Известные проблемы:** 
- Не работает режим бесконечной катушки. (В принципе он работает, но чтоб им пользоваться нужно ну очень сильно по танцевать с бубном)

**Планы на будущее:**
- Комбинированный режим парковки. (Комбинация из фид+фид ассист)Для принтеров с большой дистанцией от сплиттера до головы и не имеющих датчика филамента в голове.
- Починить бесконечную катушку Ж:)

## Возможности

✅ **Управление филаментом**
- Автоматическая смена инструмента (4 слота)
- Подача и откат филамента с настраиваемой скоростью
- Автоматическая парковка филамента к соплу
- Режим бесконечной катушки (infinity spool) с настраиваемым порядком слотов

✅ **Управление сушкой**
- Программируемая сушка филамента
- Контроль температуры и времени
- Автоматическое управление вентиляторами

✅ **Информационные функции**
- Мониторинг состояния устройства
- Информация о филаменте (RFID)
- Отладочные команды

✅ **Интеграция с Klipper**
- Полная поддержка макросов G-code
- Асинхронная обработка команд

✅ **Управление соединением**
- Команды управления подключением (ACE_CONNECT, ACE_DISCONNECT, ACE_CONNECTION_STATUS)
- Поддержка внешнего датчика филамента
- Команда проверки статуса датчика (ACE_CHECK_FILAMENT_SENSOR)
- Команда переподключения при ошибках (ACE_RECONNECT)
- Настраиваемый макрос паузы

✅ **Маппинг слотов**
- Переназначение индексов Klipper (T0-T3) на физические слоты устройства
- Команды получения, установки и сброса маппинга
- Макрос для массовой настройки слотов

✅ **Агрессивная парковка**
- Альтернативный алгоритм парковки с использованием датчика филамента
- Настраиваемые параметры: максимальная дистанция, скорость, таймаут
- Подходит для принтеров с длинным трактом подачи

- Совместимость с существующими конфигурациями

✅ **REST API через Moonraker**
- Получение статуса ACE через HTTP API
- Выполнение команд через REST эндпоинты
- WebSocket подписка на обновления статуса


## Системные требования

- **Klipper** - свежая установка (рекомендуется)
- **Python 3** - для работы модуля
- **pyserial** - библиотека для работы с последовательным портом
- **USB-соединение** - для подключения к ACE Pro

### Поддерживаемые принтеры

- ✅ Creality K1 / K1 Max
- ⚠️ Другие принтеры с Klipper (требует тестирования)

## Быстрый старт

### 1. Установка

```bash
# Клонируем репозиторий
git clone https://github.com/agrloki/ValgACE.git
cd ValgACE

# Запускаем установку
./install.sh
```

### 2. Настройка

Добавьте в `printer.cfg`:

```ini
[include ace.cfg]
```

### 3. Проверка подключения

```gcode
ACE_STATUS
ACE_DEBUG METHOD=get_info
```

## Подключение устройства

### Pinout разъема

Устройство ACE Pro подключается через разъем Molex к стандартному USB:

![Molex](/.github/img/molex.png)

**Распиновка разъема:**

- **1** - None (VCC, не требуется для работы, ACE обеспечивает собственное питание)
- **2** - Ground (Земля)
- **3** - D- (USB Data-)
- **4** - D+ (USB Data+)

**Подключение:** Подключите разъем Molex к обычному USB кабелю - никаких дополнительных манипуляций не требуется.

Подробнее см. [Руководство по установке](docs/INSTALLATION.md#подключение-устройства).

## Документация

Полная документация доступна в папке `docs/`:

**Русская документация:**
- **[Установка](docs/INSTALLATION.md)** - подробное руководство по установке
- **[Руководство пользователя](docs/USER_GUIDE.md)** - как использовать ValgACE
- **[Справочник команд](docs/COMMANDS.md)** - все доступные команды G-code
- **[Конфигурация](docs/CONFIGURATION.md)** - настройка параметров
- **[Решение проблем](docs/TROUBLESHOOTING.md)** - типичные проблемы и решения
- **[Протокол](docs/Protocol.md)** - техническая документация протокола (English)
- **[Протокол (русский)](docs/Protocol_ru.md)** - техническая документация протокола
- **[Moonraker API](docs/MOONRAKER_API.md)** - интеграция с Moonraker API и REST эндпоинты

**English Documentation:**
- **[Installation](docs/en/INSTALLATION.md)** - detailed installation guide
- **[User Guide](docs/en/USER_GUIDE.md)** - how to use ValgACE
- **[Commands Reference](docs/en/COMMANDS.md)** - all available G-code commands
- **[Configuration](docs/en/CONFIGURATION.md)** - parameter configuration
- **[Troubleshooting](docs/en/TROUBLESHOOTING.md)** - common issues and solutions
- **[Protocol](docs/Protocol.md)** - technical protocol documentation (English)
- **[Moonraker API](docs/MOONRAKER_API.md)** - Moonraker API integration and REST endpoints (Russian)

## Основные команды

```gcode
# Получить статус устройства
ACE_STATUS

# Смена инструмента
ACE_CHANGE_TOOL TOOL=0    # Загрузить слот 0
ACE_CHANGE_TOOL TOOL=-1   # Выгрузить филамент

# Парковка филамента
ACE_PARK_TO_TOOLHEAD INDEX=0

# Управление подачей
ACE_FEED INDEX=0 LENGTH=50 SPEED=25
ACE_RETRACT INDEX=0 LENGTH=50 SPEED=25

# Сушка филамента
ACE_START_DRYING TEMP=50 DURATION=120
ACE_STOP_DRYING

# Режим бесконечной катушки
ACE_SET_INFINITY_SPOOL_ORDER ORDER="0,1,2,3"  # Установить порядок слотов
ACE_INFINITY_SPOOL  # Автоматическая смена при окончании филамента

# Маппинг слотов
ACE_GET_SLOTMAPPING                 # Получить текущий маппинг
ACE_SET_SLOTMAPPING KLIPPER_INDEX=0 ACE_INDEX=1  # Назначить T0 -> слот 1
ACE_RESET_SLOTMAPPING               # Сбросить на значения по умолчанию
SET_ALL_SLOTMAPPING S0=0 S1=1 S2=2 S3=3  # Массовая настройка

# Управление соединением
ACE_RECONNECT                       # Переподключиться при ошибках

# Справка
ACE_GET_HELP                        # Вывести список всех команд
```

Полный список команд см. в [Справочнике команд](docs/COMMANDS.md).

## REST API

После установки доступны REST API эндпоинты через Moonraker:

```bash
# Получить статус ACE
curl http://localhost:7125/server/ace/status

# Получить информацию о слотах
curl http://localhost:7125/server/ace/slots

# Выполнить команду ACE
curl -X POST http://localhost:7125/server/ace/command \
  -H "Content-Type: application/json" \
  -d '{"command":"ACE_PARK_TO_TOOLHEAD","params":{"INDEX":0}}'
```

Подробная документация по REST API: [Moonraker API](docs/MOONRAKER_API.md)

## Веб-интерфейс
![Web](/.github/img/valgace-web.png)

Готовый веб-интерфейс для управления ACE доступен в `web-interface/`:

- **[ValgACE Dashboard](web-interface/README.md)** - полнофункциональный веб-интерфейс с Vue.js
- Отображение статуса устройства в реальном времени
- Управление слотами филамента (загрузка, парковка, feed assist, подача, откат)
- Управление сушкой
- WebSocket подключение для обновлений в реальном времени

### Быстрая установка Dashboard

```bash
# Скопируйте файлы
mkdir -p ~/ace-dashboard
cp ~/ValgACE/web-interface/ace-dashboard.* ~/ace-dashboard/

# Запустите HTTP сервер
cd ~/ace-dashboard
python3 -m http.server 8080
```

Откройте в браузере: `http://<IP-принтера>:8080/ace-dashboard.html`

**Для постоянного использования рекомендуется установка через nginx** — см. [инструкции по установке](docs/INSTALLATION.md#2-установка-веб-интерфейса-valgace-dashboard) и [пример конфигурации nginx](web-interface/nginx.conf.example).

Файлы:
- `ace-dashboard.html` - основной интерфейс
- `ace-dashboard.css` - стили
- `ace-dashboard.js` - логика работы с API
- `ace-dashboard-config.js` - конфигурация адреса Moonraker

## Поддержка

### Обсуждения

- **Основное обсуждение:** [Telegram - perdoling3d](https://t.me/perdoling3d/45834)
- **Общее обсуждение:** [Telegram - ERCFcrealityACEpro](https://t.me/ERCFcrealityACEpro/21334)

### Видео

- [Демонстрация работы](https://youtu.be/hozubbjeEw8)

### GitHub

- **Репозиторий:** https://github.com/agrloki/ValgACE
- **Issues:** Используйте GitHub Issues для сообщений об ошибках

## Благодарности

Отдельная благодарность **@Nefelim4ag** (Timofey Titovets) за волшебный пендель в правильном направлении. 🙂

Проект основан на:
- [DuckACE](https://github.com/utkabobr/DuckACE) от utkabobr
- [BunnyACE](https://github.com/BlackFrogKok/BunnyACE) от BlackFrogKok

## Лицензия

Проект распространяется под лицензией [GNU GPL v3](LICENSE.md).

