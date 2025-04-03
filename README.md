# ValgACE

A Work-In-Progress driver for Anycubic Color Engine Pro for Klipper


Драйвер для Anycubic Color Engine Pro под Klipper, на данный момент статус в разработке.

Based on https://github.com/utkabobr/DuckACE
and https://github.com/BlackFrogKok/BunnyACE

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
- Выполняем: chmod +x ./install.sh
- Запускаем установку: ./install.sh

Скрипт выполнит все необходимые действия. На данный момент прописывание в апдейт менеджер moonraker отключено. 
Поскольку драйвер в процессе отладки и возможно много изменений версий которые ставить совсем не надо:)

##Installation
- Clone the repository:
    git clone https://github.com/agrloki/ValgACE.git

- Navigate to the directory:
    cd ~/ValgACE

- Make the script executable:
    chmod +x ./install.sh

- Run the installation:
    ./install.sh

The script will perform all necessary actions. Currently, automatic registration with Moonraker's update manager is disabled since the driver is under active development and may undergo frequent version changes that shouldn't necessarily be installed automatically :)

## Доступные команды:
- ACE_STATUS                               Получить статус
- ACE_START_DRYING TEMP=50 DURATION=120    Сушить 2 часа при 50°C
- ACE_STOP_DRYING                          Остановить сушку
- ACE_DEBUG                                Проверить подключение
- ACE_ENABLE_FEED_ASSIST INDEX=0 - 3       Включить помощь подачи филамента для конкретного порта
- ACE_DISABLE_FEED_ASSIST INDEX=0 - 3      Выключить помощь подачи филамента для конкретного порта
- ACE_PARK_TO_TOOLHEAD INDEX=0 - 3         Припарковать филамент к голове индекс указывает какой порт будет припаркован
- ACE_FEED INDEX=0-3 LENGTH=<длина подачи> SPEED=<Скорость подачи>     Подача филамента
- ACE_RETRACT INDEX=0-3 LENGTH=<длина подачи> SPEED=<Скорость подачи>  Откат филамента
- ACE_CHANGE_TOOL TOOL=-1 - 0 - 3          Смена инструмента. 
- ACE_FILAMENT_INFO                        Информация о филаменте если есть rfid метка