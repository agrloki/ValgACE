# Руководство по установке ValgACE

## Содержание

1. [Предварительные требования](#предварительные-требования)
2. [Подключение устройства](#подключение-устройства)
3. [Автоматическая установка](#автоматическая-установка)
4. [Ручная установка](#ручная-установка)
5. [Проверка установки](#проверка-установки)
6. [Настройка Moonraker](#настройка-moonraker)
7. [Обновление](#обновление)
8. [Удаление](#удаление)

---

## Предварительные требования

### 1. Установленный Klipper

Убедитесь, что у вас установлен и работает Klipper. Модуль требует доступ к:
- `~/klipper/klippy/extras/` - директория для модулей Klipper
- `~/printer_data/config/` - директория конфигурации
- Moonraker для автоматических обновлений (опционально)

### 2. Python зависимости

Модуль требует библиотеку `pyserial`:

```bash
# Установка через pip (обычно выполняется автоматически скриптом install.sh)
pip3 install pyserial
```

### 3. USB подключение

Убедитесь, что устройство ACE Pro подключено по USB к системе, где работает Klipper.

---

## Подключение устройства

### Pinout разъема

Устройство ACE Pro подключается через разъем Molex к стандартному USB:

![Molex](/.github/img/molex.png)

**Распиновка разъема:**

- **1** - None (VCC, не требуется для работы, ACE обеспечивает собственное питание)
- **2** - Ground (Земля)
- **3** - D- (USB Data-)
- **4** - D+ (USB Data+)

### Подключение

Подключите разъем Molex к обычному USB кабелю - никаких дополнительных манипуляций не требуется.

**Важно:**
- Используйте качественный USB кабель
- Убедитесь в надежности подключения
- Рекомендуется использовать USB порт непосредственно на плате управления (не через USB хаб)

### Проверка подключения

После физического подключения проверьте, что система видит устройство:

```bash
# Проверить USB устройства
lsusb | grep -i anycubic

# Должно показать устройство с VID:PID 28e9:018a
# Пример: Bus 001 Device 003: ID 28e9:018a Anycubic ACE
```

Если устройство не видно:
- Проверьте USB кабель
- Попробуйте другой USB порт
- Убедитесь, что устройство включено
- Проверьте питание устройства ACE

---

## Автоматическая установка

### Шаг 1: Клонирование репозитория

```bash
cd ~
git clone https://github.com/agrloki/ValgACE.git
cd ValgACE
```

### Шаг 2: Запуск скрипта установки

```bash
# Убедитесь, что скрипт исполняемый
chmod +x install.sh

# Запуск установки
./install.sh
```

### Что делает скрипт установки:

1. ✅ Проверяет наличие необходимых директорий Klipper
2. ✅ Создает символическую ссылку на модуль `ace.py`
3. ✅ Копирует файл конфигурации `ace.cfg` (если его еще нет)
4. ✅ Устанавливает зависимости Python (`pyserial`)
5. ✅ Добавляет секцию обновления в `moonraker.conf`
6. ✅ Перезапускает сервисы Klipper и Moonraker

### Опции скрипта установки

```bash
# Показать версию
./install.sh -v

# Показать справку
./install.sh -h

# Удаление (см. раздел ниже)
./install.sh -u
```

---

## Ручная установка

Если автоматическая установка не подходит для вашей системы, выполните следующие шаги:

### 1. Копирование модуля

```bash
# Создайте символическую ссылку на модуль
ln -sf ~/ValgACE/extras/ace.py ~/klipper/klippy/extras/ace.py
```

### 2. Копирование конфигурации

```bash
# Скопируйте файл конфигурации
cp ~/ValgACE/ace.cfg.sample ~/printer_data/config/ace.cfg

# Отредактируйте файл конфигурации
nano ~/printer_data/config/ace.cfg
```

### 3. Установка зависимостей

```bash
# Определите путь к pip вашего окружения Klipper
# Обычно это: ~/klippy-env/bin/pip3
pip3 install -r ~/ValgACE/requirements.txt
```

### 4. Добавление в printer.cfg

Добавьте в `printer.cfg`:

```ini
[include ace.cfg]
```

### 5. Перезапуск Klipper

```bash
sudo systemctl restart klipper
```

---

## Проверка установки

### 1. Проверка логов Klipper

```bash
# Просмотр логов Klipper
tail -f ~/printer_data/logs/klippy.log
```

Должны появиться сообщения:
- `Connected to ACE at /dev/serial/...`
- `Device info: Anycubic Color Engine Pro V1.x.x`

### 2. Проверка команд G-code

Через веб-интерфейс (Mainsail/Fluidd) или консоль:

```gcode
ACE_STATUS
```

Должен вернуться статус устройства.

### 3. Проверка подключения

```gcode
ACE_DEBUG METHOD=get_info
```

Должна вернуться информация о модели и версии прошивки устройства.

### 4. Проверка модуля Python

```bash
# Проверка, что модуль доступен
python3 -c "import serial; print('pyserial OK')"
```

---

## Настройка Moonraker

### 1) Автоматическая интеграция ACE Status API (рекомендуется)

Скрипт установки `install.sh` автоматически:
- создаёт симлинк компонента `ace_status.py` в `~/moonraker/moonraker/components/ace_status.py`
- добавляет секцию `[ace_status]` в `moonraker.conf` (если её ещё нет)
- перезапускает Moonraker

После установки доступны REST-эндпоинты:
- `GET /server/ace/status` — статус ACE
- `GET /server/ace/slots` — информация о слотах
- `POST /server/ace/command` — выполнение команд `ACE_*`

Пример запроса:
```bash
curl -X POST http://<HOST>:7125/server/ace/command \
  -H "Content-Type: application/json" \
  -d '{"command":"ACE_PARK_TO_TOOLHEAD","params":{"INDEX":0}}'
```

### 2) Установка веб-интерфейса ValgACE Dashboard

ValgACE Dashboard — это готовый веб-интерфейс для управления ACE через браузер. Он предоставляет удобный графический интерфейс для всех операций управления устройством.

#### Вариант A: Простой HTTP сервер (для тестирования)

1. Скопируйте файлы dashboard:
   ```bash
   mkdir -p ~/ace-dashboard
   cp ~/ValgACE/web-interface/ace-dashboard.* ~/ace-dashboard/
   ```

2. Запустите простой HTTP сервер:
   ```bash
   cd ~/ace-dashboard
   python3 -m http.server 8080
   ```

3. Откройте в браузере: `http://<IP-адрес-принтера>:8080/ace-dashboard.html`

**Примечание:** Этот вариант подходит для тестирования. Для постоянного использования рекомендуется установка через nginx.

#### Вариант B: Nginx (рекомендуется для постоянного использования)

1. **Скопируйте файлы в директорию веб-сервера:**
   ```bash
   sudo mkdir -p /var/www/ace-dashboard
   sudo cp ~/ValgACE/web-interface/ace-dashboard.* /var/www/ace-dashboard/
   sudo chown -R www-data:www-data /var/www/ace-dashboard
   ```

2. **Создайте конфигурацию nginx:**
   ```bash
   sudo nano /etc/nginx/sites-available/ace-dashboard
   ```

3. **Используйте пример конфигурации:**
   ```bash
   # Скопируйте пример
   sudo cp ~/ValgACE/web-interface/nginx.conf.example /etc/nginx/sites-available/ace-dashboard
   
   # Отредактируйте конфигурацию
   sudo nano /etc/nginx/sites-available/ace-dashboard
   ```
   
   В конфигурации укажите:
   - `server_name` — ваш домен или IP адрес
   - `root` — путь к файлам (`/var/www/ace-dashboard`)

4. **Активируйте конфигурацию:**
   ```bash
   sudo ln -s /etc/nginx/sites-available/ace-dashboard /etc/nginx/sites-enabled/
   sudo nginx -t  # Проверка синтаксиса
   sudo systemctl reload nginx
   ```

5. **Откройте в браузере:**
   ```
   http://<ваш-домен-или-IP>/ace-dashboard.html
   ```

#### Настройка адреса Moonraker

Если Moonraker находится на другом хосте или порту, отредактируйте `ace-dashboard-config.js`:

```bash
nano ~/ace-dashboard/ace-dashboard-config.js
```

Измените:
```javascript
const ACE_DASHBOARD_CONFIG = {
    // Укажите адрес Moonraker API
    apiBase: 'http://192.168.1.100:7125',  // Замените на ваш IP
    
    // Остальные настройки...
};
```

#### Проверка установки Dashboard

1. **Проверьте доступность файлов:**
   ```bash
   ls -la ~/ace-dashboard/ace-dashboard.*
   # или
   ls -la /var/www/ace-dashboard/ace-dashboard.*
   ```

2. **Проверьте доступность через браузер:**
   - Откройте `http://<IP>:8080/ace-dashboard.html` (для HTTP сервера)
   - Или `http://<домен>/ace-dashboard.html` (для nginx)

3. **Проверьте подключение:**
   - Индикатор подключения должен быть зеленым
   - Статус устройства должен загрузиться

#### Дополнительные настройки

**Включение отладки:**
Отредактируйте `ace-dashboard-config.js`:
```javascript
debug: true,  // Включить отладочные сообщения в консоль
```

**Настройка значений по умолчанию:**
```javascript
defaults: {
    feedLength: 50,      // Длина подачи по умолчанию (мм)
    feedSpeed: 25,       // Скорость подачи по умолчанию (мм/с)
    retractLength: 50,   // Длина отката по умолчанию (мм)
    retractSpeed: 25,    // Скорость отката по умолчанию (мм/с)
    dryingTemp: 50,      // Температура сушки по умолчанию (°C)
    dryingDuration: 240  // Длительность сушки по умолчанию (мин)
}
```

Подробнее см. [README веб-интерфейса](../web-interface/README.md) и [пример конфигурации nginx](../web-interface/nginx.conf.example).

### 3) Автоматические обновления (update_manager)

Для автоматических обновлений добавьте в `moonraker.conf`:

```ini
[update_manager ValgACE]
type: git_repo
path: ~/ValgACE
origin: https://github.com/agrloki/ValgACE.git
primary_branch: main
managed_services: klipper
```

Скрипт `install.sh` добавляет этот блок автоматически.

---

## Конфигурация после установки

### 1. Настройка порта устройства

Отредактируйте `ace.cfg`:

```ini
[ace]
serial: /dev/serial/by-id/usb-ANYCUBIC_ACE_1-if00
baud: 115200
```

**Примечание:** Модуль автоматически определяет устройство по VID/PID. Если автопоиск работает, можно не указывать `serial` явно.

### 2. Настройка параметров

Основные параметры для настройки:

```ini
feed_speed: 25                    # Скорость подачи (10-25 мм/с)
retract_speed: 25                 # Скорость отката (10-25 мм/с)
park_hit_count: 5                 # Количество проверок для парковки
toolchange_retract_length: 100    # Длина отката при смене инструмента
```

Подробнее см. [Руководство по конфигурации](CONFIGURATION.md).

---

## Обновление

### Автоматическое обновление (через Moonraker)

Если настроен `update_manager`, обновление доступно через веб-интерфейс:
- Mainsail: Settings → Machine → Update Manager
- Fluidd: Settings → Machine → Update Manager

### Ручное обновление

```bash
cd ~/ValgACE
git pull
./install.sh
```

Или просто перезапустите Klipper:

```bash
sudo systemctl restart klipper
```

---

## Удаление

### Автоматическое удаление

```bash
cd ~/ValgACE
./install.sh -u
```

### Ручное удаление

1. **Удаление модуля:**
```bash
rm ~/klipper/klippy/extras/ace.py
```

2. **Удаление конфигурации:**
```bash
# Удалите строку из printer.cfg:
# [include ace.cfg]

# Удалите файл конфигурации (опционально):
rm ~/printer_data/config/ace.cfg
```

3. **Удаление из Moonraker:**
```bash
# Удалите секцию из moonraker.conf:
# [update_manager ValgACE]
```

4. **Перезапуск:**
```bash
sudo systemctl restart klipper
sudo systemctl restart moonraker
```

---

## Решение проблем при установке

### Проблема: "Klipper installation not found"

**Решение:**
- Убедитесь, что Klipper установлен в стандартной директории `~/klipper`
- Для MIPS систем используйте ручную установку

### Проблема: "pyserial not found"

**Решение:**
```bash
# Установите вручную
pip3 install pyserial

# Или для виртуального окружения Klipper:
~/klippy-env/bin/pip3 install pyserial
```

### Проблема: "Permission denied"

**Решение:**
- Не запускайте скрипт от root
- Убедитесь, что у пользователя есть права на запись в директории Klipper

### Проблема: Устройство не определяется

**Решение:**
- Проверьте подключение USB
- Убедитесь, что устройство включено
- Проверьте `lsusb` для поиска устройства
- Укажите порт явно в конфигурации

---

## Следующие шаги

После успешной установки:

1. ✅ Прочитайте [Руководство пользователя](USER_GUIDE.md)
2. ✅ Изучите [Справочник команд](COMMANDS.md)
3. ✅ Настройте параметры в [Конфигурации](CONFIGURATION.md)
4. ✅ Установите [веб-интерфейс Dashboard](../web-interface/README.md) для удобного управления
5. ✅ Протестируйте базовые команды

---

*Дата последнего обновления: 2024*

