# ValgACE Configuration Guide

Complete guide to configuring the ValgACE module parameters.

**Note:** This is a summary. For detailed documentation, see the full [Russian Configuration Guide](../CONFIGURATION.md).

## Quick Configuration

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

*Last updated: 2024*

