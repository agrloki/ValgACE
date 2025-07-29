# ValgACE
## Отдельная благодарность @Nefelim4ag ака Timofey Titovets за волшебный пендель в правильном направлении.:))
## A driver for Anycubic Color Engine Pro for Klipper

Обсуждение основное: https://t.me/perdoling3d/45834  - здесь обсуждается конкретно данная реализация драйвера, а также различные MMU.

Обсуждение общее: https://t.me/ERCFcrealityACEpro/21334 - здесь можно задать вопросы как по ValgACE так и по BunnyACE

## Драйвер для Anycubic Color Engine Pro под Klipper, на данный момент статус тестирование.

Based on https://github.com/utkabobr/DuckACE
and https://github.com/BlackFrogKok/BunnyACE

Основной функционал работает, на чистом клиппере работает стабильно, на кастомизированных версиях от производителей принтеров - как повезет.

На данный момент подтверждена работа на принтерах  Creality К1.

Драйвер обеспечивает основной функционал Anycubic Color Engine Pro без привязки к конкретной конструкции принтера, все процессы до и после смены филамента задаются 

макросами в ace.cfg и в настройках слайсеров.

Ключевые моменты:

Все индексы (порты) изменяются от 0 до 3

Для выбора инструмента допустимые значения от -1 (выгрузить пластик из хотенда) до 3

Команды подачи/отката требуют параметров длины и скорости

Информация RFID доступна только для маркированных нитей

## English

The core functionality works, and it operates stably on a clean Klipper installation. On customized versions from printer manufacturers, the stability may vary.

As of now, its operation has been confirmed on Creality K1 printers.

The driver provides the main functionality of the Anycubic Color Engine Pro without being tied to a specific printer design; all processes before and after filament 

changes are defined by macros in ace.cfg and slicer settings.

## Pinout

![Molex](/.github/img/molex.png)

- 1 - None (VCC, not required to work, ACE provides it's own power)
- 2 - Ground
- 3 - D-
- 4 - D+

Connect them to a regular USB, no dark magic is required.

## Установка

- Клонируем репо: git clone https://github.com/agrloki/ValgACE.git
- Заходим в каталог: cd ~/ValgACE
- Запускаем установку: ./install.sh
- В файл printer.cfg добавляем: [include ace.cfg]

Скрипт выполнит все необходимые действия. 

## Installation

- Clone the repository:
    git clone https://github.com/agrloki/ValgACE.git

- Navigate to the directory:
    cd ~/ValgACE

- Run the installation:
    ./install.sh

- Add this include statement to printer.cfg:
     [include ace.cfg]

The script will perform all necessary actions. 

## Доступные команды:
- ACE_STATUS                               Получить статус
- ACE_START_DRYING TEMP=50 DURATION=120    Сушить 2 часа при 50°C
- ACE_STOP_DRYING                          Остановить сушку
- ACE_DEBUG  METHOD=<запрос> (get_status, get_info) PARAMS=<параметры>  Проверить подключение см. Protocol.md
- ACE_ENABLE_FEED_ASSIST INDEX=0 - 3       Включить помощь подачи филамента для конкретного порта
- ACE_DISABLE_FEED_ASSIST INDEX=0 - 3      Выключить помощь подачи филамента для конкретного порта
- ACE_PARK_TO_TOOLHEAD INDEX=0 - 3         Припарковать филамент к голове индекс указывает какой порт будет припаркован
- ACE_FEED INDEX=0-3 LENGTH=<длина подачи> SPEED=<Скорость подачи>     Подача филамента
- ACE_UPDATE_FEEDING_SPEED INDEX=0-3 SPEED=<скорость подачи> - изменить скорость подачи на лету.
- ACE_RETRACT INDEX=0-3 LENGTH=<длина подачи> SPEED=<Скорость подачи> MODE=<0 (normal mode), 1 (enhanced mode)> Откат филамента
- ACE_CHANGE_TOOL TOOL=-1 - 0 - 3          Смена инструмента. 
- ACE_FILAMENT_INFO                        Информация о филаменте если есть rfid метка

## Available Commands:

- ACE_STATUS - Get device status

- ACE_START_DRYING TEMP=50 DURATION=120 - Dry filament for 2 hours at 50°C

- ACE_STOP_DRYING - Stop drying process

- ACE_DEBUG METHOD=<query> (get_status, get_info) PARAMS=<request params>- Check connection, see Protocol.md

- ACE_ENABLE_FEED_ASSIST INDEX=0-3 - Enable filament feed assist for specified port

- ACE_DISABLE_FEED_ASSIST INDEX=0-3 - Disable filament feed assist for specified port

- ACE_PARK_TO_TOOLHEAD INDEX=0-3 - Park filament to toolhead (specify port index)

- ACE_FEED INDEX=0-3 LENGTH=<feed_length> SPEED=<feed_speed> - Feed filament

- ACE_UPDATE_FEEDING_SPEED INDEX=0-3 SPEED=<feed_speed> - Change the feed speed on the fly

- ACE_RETRACT INDEX=0-3 LENGTH=<retract_length> SPEED=<retract_speed> MODE=<0 (normal mode), 1 (enhanced mode)> - Retract filament

- ACE_CHANGE_TOOL TOOL=-1/0/1/2/3 - Change tool (use -1 for no tool)

- ACE_FILAMENT_INFO - Show filament information (if RFID tag is present)

Key notes:

All indexes (ports) range from 0 to 3

Tool selection accepts values from -1 (no tool) to 3

Feed/retract commands require length and speed parameters

RFID information is only available for tagged filaments
