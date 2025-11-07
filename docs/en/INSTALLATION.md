# ValgACE Installation Guide

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [Device Connection](#device-connection)
3. [Automatic Installation](#automatic-installation)
4. [Manual Installation](#manual-installation)
5. [Installation Verification](#installation-verification)
6. [Moonraker Setup](#moonraker-setup)
7. [Updating](#updating)
8. [Uninstallation](#uninstallation)

---

## Prerequisites

### 1. Klipper Installation

Ensure you have Klipper installed and running. The module requires access to:
- `~/klipper/klippy/extras/` - Klipper modules directory
- `~/printer_data/config/` - Configuration directory
- Moonraker for automatic updates (optional)

### 2. Python Dependencies

The module requires the `pyserial` library:

```bash
# Installation via pip (usually done automatically by install.sh script)
pip3 install pyserial
```

### 3. USB Connection

Ensure the ACE Pro device is connected via USB to the system running Klipper.

---

## Device Connection

### Connector Pinout

The ACE Pro device connects via a Molex connector to a standard USB:

![Molex](/.github/img/molex.png)

**Connector Pinout:**

- **1** - None (VCC, not required to work, ACE provides its own power)
- **2** - Ground
- **3** - D- (USB Data-)
- **4** - D+ (USB Data+)

### Connection

Connect the Molex connector to a regular USB cable - no additional modifications are required.

**Important:**
- Use a quality USB cable
- Ensure reliable connection
- It's recommended to use a USB port directly on the control board (not through a USB hub)

### Connection Verification

After physical connection, verify that the system detects the device:

```bash
# Check USB devices
lsusb | grep -i anycubic

# Should show device with VID:PID 28e9:018a
# Example: Bus 001 Device 003: ID 28e9:018a Anycubic ACE
```

If the device is not visible:
- Check the USB cable
- Try another USB port
- Ensure the device is powered on
- Check ACE device power supply

---

## Automatic Installation

### Step 1: Clone Repository

```bash
cd ~
git clone https://github.com/agrloki/ValgACE.git
cd ValgACE
```

### Step 2: Run Installation Script

```bash
# Make sure script is executable
chmod +x install.sh

# Run installation
./install.sh
```

### What the Installation Script Does:

1. ✅ Checks for required Klipper directories
2. ✅ Creates symbolic link to `ace.py` module
3. ✅ Copies `ace.cfg` configuration file (if it doesn't exist)
4. ✅ Installs Python dependencies (`pyserial`)
5. ✅ Adds update section to `moonraker.conf`
6. ✅ Restarts Klipper and Moonraker services

### Installation Script Options

```bash
# Show version
./install.sh -v

# Show help
./install.sh -h

# Uninstall (see section below)
./install.sh -u
```

---

## Manual Installation

If automatic installation doesn't work for your system, follow these steps:

### 1. Copy Module

```bash
# Create symbolic link to module
ln -sf ~/ValgACE/extras/ace.py ~/klipper/klippy/extras/ace.py
```

### 2. Copy Configuration

```bash
# Copy configuration file
cp ~/ValgACE/ace.cfg.sample ~/printer_data/config/ace.cfg

# Edit configuration file
nano ~/printer_data/config/ace.cfg
```

### 3. Install Dependencies

```bash
# Determine path to pip for your Klipper environment
# Usually: ~/klippy-env/bin/pip3
pip3 install -r ~/ValgACE/requirements.txt
```

### 4. Add to printer.cfg

Add to `printer.cfg`:

```ini
[include ace.cfg]
```

### 5. Restart Klipper

```bash
sudo systemctl restart klipper
```

---

## Installation Verification

### 1. Check Klipper Logs

```bash
# View Klipper logs
tail -f ~/printer_data/logs/klippy.log
```

You should see messages:
- `Connected to ACE at /dev/serial/...`
- `Device info: Anycubic Color Engine Pro V1.x.x`

### 2. Test G-code Commands

Via web interface (Mainsail/Fluidd) or console:

```gcode
ACE_STATUS
```

Should return device status.

### 3. Test Connection

```gcode
ACE_DEBUG METHOD=get_info
```

Should return model and firmware version information.

### 4. Check Python Module

```bash
# Verify module is available
python3 -c "import serial; print('pyserial OK')"
```

---

## Moonraker Setup

For automatic updates, add to `moonraker.conf`:

```ini
[update_manager ValgACE]
type: git_repo
path: ~/ValgACE
origin: https://github.com/agrloki/ValgACE.git
primary_branch: main
managed_services: klipper
```

The `install.sh` script does this automatically.

---

## Post-Installation Configuration

### 1. Configure Device Port

Edit `ace.cfg`:

```ini
[ace]
serial: /dev/serial/by-id/usb-ANYCUBIC_ACE_1-if00
baud: 115200
```

**Note:** The module automatically detects the device by VID/PID. If auto-detection works, you don't need to specify `serial` explicitly.

### 2. Configure Parameters

Main parameters to configure:

```ini
feed_speed: 25                    # Feed speed (10-25 mm/s)
retract_speed: 25                 # Retract speed (10-25 mm/s)
park_hit_count: 5                 # Number of checks for parking
toolchange_retract_length: 100    # Retract length on tool change
```

For more details, see [Configuration Guide](CONFIGURATION.md).

---

## Web Interface Dashboard

ValgACE includes a ready-to-use dashboard located in the `web-interface/` directory. It provides live status, slot control, feed assist actions, dryer control and a bilingual (English/Russian) UI.

### Option A: Simple HTTP server (testing)

```bash
mkdir -p ~/ace-dashboard
cp ~/ValgACE/web-interface/ace-dashboard.* ~/ace-dashboard/

cd ~/ace-dashboard
python3 -m http.server 8080
```

Open `http://<printer-ip>:8080/ace-dashboard.html`

### Option B: nginx (recommended for permanent setups)

```bash
sudo mkdir -p /var/www/ace-dashboard
sudo cp ~/ValgACE/web-interface/ace-dashboard.* /var/www/ace-dashboard/
sudo chown -R www-data:www-data /var/www/ace-dashboard

sudo cp ~/ValgACE/web-interface/nginx.conf.example /etc/nginx/sites-available/ace-dashboard
sudo nano /etc/nginx/sites-available/ace-dashboard  # adjust paths/hostnames
sudo ln -s /etc/nginx/sites-available/ace-dashboard /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

The dashboard README provides additional details: [`web-interface/README.md`](../../web-interface/README.md)

---

## Updating

### Automatic Update (via Moonraker)

If `update_manager` is configured, updates are available through the web interface:
- Mainsail: Settings → Machine → Update Manager
- Fluidd: Settings → Machine → Update Manager

### Manual Update

```bash
cd ~/ValgACE
git pull
./install.sh
```

Or simply restart Klipper:

```bash
sudo systemctl restart klipper
```

---

## Uninstallation

### Automatic Uninstallation

```bash
cd ~/ValgACE
./install.sh -u
```

### Manual Uninstallation

1. **Remove Module:**
```bash
rm ~/klipper/klippy/extras/ace.py
```

2. **Remove Configuration:**
```bash
# Remove line from printer.cfg:
# [include ace.cfg]

# Remove configuration file (optional):
rm ~/printer_data/config/ace.cfg
```

3. **Remove from Moonraker:**
```bash
# Remove section from moonraker.conf:
# [update_manager ValgACE]
```

4. **Restart:**
```bash
sudo systemctl restart klipper
sudo systemctl restart moonraker
```

---

## Troubleshooting Installation Issues

### Issue: "Klipper installation not found"

**Solution:**
- Ensure Klipper is installed in the standard directory `~/klipper`
- For MIPS systems, use manual installation

### Issue: "pyserial not found"

**Solution:**
```bash
# Install manually
pip3 install pyserial

# Or for Klipper virtual environment:
~/klippy-env/bin/pip3 install pyserial
```

### Issue: "Permission denied"

**Solution:**
- Don't run the script as root
- Ensure the user has write permissions to Klipper directories

### Issue: Device Not Detected

**Solution:**
- Check USB connection
- Ensure device is powered on
- Check `lsusb` to find the device
- Specify port explicitly in configuration

---

## Next Steps

After successful installation:

1. ✅ Read the [User Guide](USER_GUIDE.md)
2. ✅ Study the [Commands Reference](COMMANDS.md)
3. ✅ Configure parameters in [Configuration](CONFIGURATION.md)
4. ✅ Test basic commands

---

*Last updated: 2025*

