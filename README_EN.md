# ValgACE - Driver for Anycubic Color Engine Pro

[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)

**ValgACE** - Klipper module providing full control over the Anycubic Color Engine Pro (ACE Pro) automatic filament changer device.

## 📋 Table of Contents

- [Description](#description)
- [Features](#features)
- [System Requirements](#system-requirements)
- [Quick Start](#quick-start)
- [Device Connection](#device-connection)
- [Documentation](#documentation)
- [Support](#support)
- [Acknowledgments](#acknowledgments)

## Description

ValgACE is a full-featured driver for controlling the Anycubic Color Engine Pro device through Klipper. The driver provides automatic filament switching between 4 slots, drying control, filament feed and retract, as well as RFID tag support.

### Project Status

**Status:** Stable  
**Confirmed on:** Creality K1, Kingroon KLP1, Kingroon KP3S Pro V2, custom Klipper 3D printers  
**Based on:** [DuckACE](https://github.com/utkabobr/DuckACE) and [BunnyACE](https://github.com/BlackFrogKok/BunnyACE)

## Features

✅ **Filament Management**
- Automatic tool change (4 slots)
- Filament feed and retract with adjustable speed
- Automatic filament parking to nozzle
- Infinity spool mode with configurable slot order

✅ **Drying Control**
- Programmable filament drying
- Temperature and time control
- Automatic fan management

✅ **Information Functions**
- Device status monitoring
- Filament information (RFID)
- Debug commands

✅ **Klipper Integration**
- Full G-code macro support
- Asynchronous command processing

✅ **Connection Management**
- Connection control commands (ACE_CONNECT, ACE_DISCONNECT, ACE_CONNECTION_STATUS)
- External filament sensor support
- Sensor status check command (ACE_CHECK_FILAMENT_SENSOR)

- Compatibility with existing configurations

## System Requirements

- **Klipper** - fresh installation (recommended)
- **Python 3** - for module operation
- **pyserial** - library for serial port communication
- **USB Connection** - to connect to ACE Pro

### Supported Printers

- ✅ Creality K1 / K1 Max
- ⚠️ Other Klipper printers (requires testing)


## Quick Start

### 1. Installation

```bash
# Clone repository
git clone https://github.com/agrloki/ValgACE.git
cd ValgACE

# Run installation
./install.sh
```

### 2. Configuration

Add to `printer.cfg`:

```ini
[include ace.cfg]
```

### 3. Connection Check

```gcode
ACE_STATUS
ACE_DEBUG METHOD=get_info
```

## Device Connection

### Connector Pinout

The ACE Pro device connects via a Molex connector to a standard USB:

![Molex](/.github/img/molex.png)

**Connector Pinout:**

- **1** - None (VCC, not required to work, ACE provides its own power)
- **2** - Ground
- **3** - D- (USB Data-)
- **4** - D+ (USB Data+)

**Connection:** Connect the Molex connector to a regular USB cable - no additional modifications are required.

For more details, see [Installation Guide](docs/en/INSTALLATION.md#device-connection).

## Documentation

Full documentation is available in the `docs/` folder:

- **[Installation](docs/en/INSTALLATION.md)** - detailed installation guide
- **[User Guide](docs/en/USER_GUIDE.md)** - how to use ValgACE
- **[Commands Reference](docs/en/COMMANDS.md)** - all available G-code commands
- **[Configuration](docs/en/CONFIGURATION.md)** - parameter configuration
- **[Troubleshooting](docs/en/TROUBLESHOOTING.md)** - common issues and solutions
- **[Protocol](docs/Protocol.md)** - technical protocol documentation

**Russian Documentation:** Available in `docs/` folder (Russian language)

## Web Interface
![Web](/.github/img/valgace-web-en.png)

A fully featured dashboard for ValgACE lives in the `web-interface/` directory. It includes a language toggle (English/Russian), live status updates, slot management, dryer controls, and quick actions.

### Quick setup

```bash
# Copy files to any host running Moonraker
mkdir -p ~/ace-dashboard
cp ~/ValgACE/web-interface/ace-dashboard.* ~/ace-dashboard/

# Start a simple HTTP server
cd ~/ace-dashboard
python3 -m http.server 8080
```

Open `http://<printer-ip>:8080/ace-dashboard.html`

For a production setup use nginx; see [`web-interface/nginx.conf.example`](web-interface/nginx.conf.example) and detailed installation steps in [`web-interface/README.md`](web-interface/README.md).

## Main Commands

```gcode
# Get device status
ACE_STATUS

# Tool change
ACE_CHANGE_TOOL TOOL=0    # Load slot 0
ACE_CHANGE_TOOL TOOL=-1   # Unload filament

# Filament parking
ACE_PARK_TO_TOOLHEAD INDEX=0

# Feed control
ACE_FEED INDEX=0 LENGTH=50 SPEED=25
ACE_RETRACT INDEX=0 LENGTH=50 SPEED=25

# Filament drying
ACE_START_DRYING TEMP=50 DURATION=120
ACE_STOP_DRYING

# Infinity spool mode
ACE_SET_INFINITY_SPOOL_ORDER ORDER="0,1,2,3"  # Set slot order
ACE_INFINITY_SPOOL  # Auto change spool when empty
```

Full command list available in [Commands Reference](docs/en/COMMANDS.md).

## Support

### Discussions

- **Main discussion:** [Telegram - perdoling3d](https://t.me/perdoling3d/45834)
- **General discussion:** [Telegram - ERCFcrealityACEpro](https://t.me/ERCFcrealityACEpro/21334)

### Video

- [Demonstration](https://youtu.be/hozubbjeEw8)

### GitHub

- **Repository:** https://github.com/agrloki/ValgACE
- **Issues:** Use GitHub Issues for bug reports

## Acknowledgments

Special thanks to **@Nefelim4ag** (Timofey Titovets) for help in development and guidance in the right direction. 🙂

Project based on:
- [DuckACE](https://github.com/utkabobr/DuckACE) by utkabobr
- [BunnyACE](https://github.com/BlackFrogKok/BunnyACE) by BlackFrogKok

## License

Project is distributed under [GNU GPL v3](LICENSE.md) license.


