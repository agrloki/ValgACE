# ValgACE Configuration Guide

Complete guide to configuring the ValgACE module parameters.

**Note:** This is a summary. For detailed documentation, see the full [Russian Configuration Guide](../CONFIGURATION.md).

## Quick Configuration

**Note:** The `ace.cfg.sample` file contains configuration examples and ready-to-use G-code macros that can be used as a starting point for your configuration. It's recommended to copy `ace.cfg.sample` to `ace.cfg` and adapt it to your printer.

### Basic Setup

```ini
[ace]
serial: /dev/serial/by-id/usb-ANYCUBIC_ACE_1-if00
baud: 115200
feed_speed: 25
retract_speed: 25
park_hit_count: 5
```

## Main Parameters

### Connection
- `serial` - Serial port path (auto-detected if not specified)
- `baud` - Baud rate (default: 115200)

### Operation
- `feed_speed` - Default feed speed in mm/s (10-25, default: 25)
- `retract_speed` - Default retract speed in mm/s (10-25, default: 25)
- `retract_mode` - Retract mode (0=normal, 1=enhanced, default: 0)
- `toolchange_retract_length` - Retract length on tool change in mm (default: 100)
- `park_hit_count` - Number of stable checks for parking completion (default: 5)
- `max_dryer_temperature` - Maximum dryer temperature in °C (default: 55)
- `disable_assist_after_toolchange` - Disable feed assist after tool change (default: True)
- `infinity_spool_mode` - Enable infinity spool mode (default: False)
  - Requires setting slot order via `ACE_SET_INFINITY_SPOOL_ORDER ORDER="..."`
- `filament_sensor` - External filament sensor name for integration with ACE module (default: not set)

### Status Fields
The ACE module returns additional status fields through the `get_status` method:
- `feed_assist_slot` - Index of slot with active feed assist (-1 if disabled)
- `filament_sensor` - Status of external filament sensor if configured
- `slot_mapping` - Index to slot mapping information

### Aggressive Parking
- `aggressive_parking` - Enable aggressive parking mode (default: False)
  - Uses filament sensor for parking detection
  - Two algorithms available: sensor-based (when filament sensor is configured) and distance-based (when no sensor is available)
- `max_parking_distance` - Maximum parking distance in mm (default: 100)
- `parking_speed` - Filament feed speed during parking in mm/s (default: 10)
- `extended_park_time` - Additional time for sensor-based parking in seconds (default: 10)
- `max_parking_timeout` - Maximum parking timeout in seconds (default: 60)
- `max_parking_distance` - Maximum parking distance in mm for aggressive parking (default: 100)
- `parking_speed` - Filament feed speed during parking in mm/s for aggressive parking (default: 10)

### Error Handling
- `set_pause_macro_name` - Name of macro to call when connection is lost during printing (default: PAUSE)

### Timeouts
- `response_timeout` - Response timeout in seconds (default: 2.0)
- `read_timeout` - Read timeout in seconds (default: 0.1)
- `write_timeout` - Write timeout in seconds (default: 0.5)
- `max_queue_size` - Maximum command queue size (default: 20)

### Logging
- `disable_logging` - Disable logging (default: False)
- `log_level` - Log level: DEBUG, INFO, WARNING, ERROR (default: INFO)
- `log_dir` - Log directory (default: ~/printer_data/logs)
- `max_log_size` - Max log file size in MB (default: 10)
- `log_backup_count` - Number of rotated log files (default: 3)

## Full Documentation

For complete configuration documentation with examples and recommendations, please refer to:
- **[Russian Configuration Guide](../CONFIGURATION.md)** - Full documentation in Russian

---

*Last updated: 2025*
