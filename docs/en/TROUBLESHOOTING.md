# ValgACE Troubleshooting Guide

Common issues and solutions when using ValgACE.

**Note:** This is a summary. For detailed troubleshooting, see the full [Russian Troubleshooting Guide](../TROUBLESHOOTING.md).

## Common Issues

### Device Not Connecting

**Symptoms:** Status shows `disconnected`, commands don't work

**Solutions:**
1. Check USB connection: `lsusb | grep -i anycubic`
2. Check port: `ls -la /dev/serial/by-id/`
3. Verify configuration in `ace.cfg`
4. Check permissions: `sudo usermod -a -G dialout $USER`

### Parking Not Completing

**Solutions:**
1. Check filament path is clear
2. Reduce `park_hit_count` in config (try 3 instead of 5)
3. Check nozzle endstop switch
4. Verify `feed_assist_count` increases during parking

### Tool Change Hanging

**Solutions:**
1. Check slot readiness: `ACE_STATUS`
2. Increase wait time for slot to be ready
3. Check parking completion
4. Review macros `_ACE_PRE_TOOLCHANGE` and `_ACE_POST_TOOLCHANGE`

### Feed/Retract Not Working

**Solutions:**
1. Check slot is not empty
2. Check filament path for obstructions
3. Verify speeds (10-25 mm/s recommended)
4. Try different retract mode: `MODE=1`

### Drying Not Starting

**Solutions:**
1. Check `max_dryer_temperature` setting
2. Verify temperature range (20-55°C)
3. Check duration (1-240 minutes)

## Diagnostics

### Check Device Status
```gcode
ACE_STATUS
```

### Check Connection
```bash
tail -f ~/printer_data/logs/klippy.log | grep -i ace
```

### Debug Commands
```gcode
ACE_DEBUG METHOD=get_info
ACE_DEBUG METHOD=get_status
```

## Full Documentation

For complete troubleshooting guide with detailed solutions and diagnostics, please refer to:
- **[Russian Troubleshooting Guide](../TROUBLESHOOTING.md)** - Full documentation in Russian

---

*Last updated: 2025*

