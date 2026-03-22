# ValgACE Commands Reference

Complete list of all available G-code commands for controlling the Anycubic Color Engine Pro device.

**Note:** This is a summary. For detailed documentation, see the full [Russian Commands Reference](../COMMANDS.md) or refer to the source code.

## Quick Reference

### Status Commands
- `ACE_STATUS` - Get device status (includes device status, dryer status, temperature, slot info, feed assist count, feed assist slot index, filament sensor status if configured, and slot mapping)
- `ACE_FILAMENT_INFO INDEX=<0-3>` - Get filament info (requires RFID)

### Tool Management
- `ACE_CHANGE_TOOL TOOL=<-1 to 3>` - Change tool (-1 = unload, 0-3 = load slot)
- `ACE_PARK_TO_TOOLHEAD INDEX=<0-3>` - Park filament to nozzle

### Filament Control
- `ACE_FEED INDEX=<0-3> LENGTH=<mm> SPEED=<mm/s>` - Feed filament
- `ACE_RETRACT INDEX=<0-3> LENGTH=<mm> SPEED=<mm/s> MODE=<0|1>` - Retract filament
- `ACE_STOP_FEED INDEX=<0-3>` - Stop feed
- `ACE_STOP_RETRACT INDEX=<0-3>` - Stop retract
- `ACE_UPDATE_FEEDING_SPEED INDEX=<0-3> SPEED=<mm/s>` - Change feed speed
- `ACE_UPDATE_RETRACT_SPEED INDEX=<0-3> SPEED=<mm/s>` - Change retract speed

### Feed Assist
- `ACE_ENABLE_FEED_ASSIST INDEX=<0-3>` - Enable feed assist
- `ACE_DISABLE_FEED_ASSIST INDEX=<0-3>` - Disable feed assist

### Drying
- `ACE_START_DRYING TEMP=<20-55> DURATION=<minutes>` - Start drying
- `ACE_STOP_DRYING` - Stop drying

### Connection
- `ACE_DISCONNECT` - Force disconnect from device
- `ACE_CONNECT` - Connect to device
- `ACE_RECONNECT` - Reset connection and clear error flags
- `ACE_CONNECTION_STATUS` - Check connection status (shows additional info about connection loss if applicable)
- `ACE_CHECK_FILAMENT_SENSOR` - Check filament sensor status (if configured)

### Debug
- `ACE_DEBUG METHOD=<method> PARAMS=<json>` - Debug command
- `ACE_GET_HELP` - Get help on available commands

### Index Management
- `ACE_GET_CURRENT_INDEX` - Get current tool index value
- `ACE_SET_CURRENT_INDEX INDEX=<-1 to 3>` - Set current tool index value (for error recovery)

### Slot Mapping
- `ACE_GET_SLOTMAPPING` - Get current slot mapping
- `ACE_SET_SLOTMAPPING INDEX=<0-3> SLOT=<0-3>` - Set slot mapping
- `ACE_RESET_SLOTMAPPING` - Reset slot mapping to defaults

### Infinity Spool
- `ACE_SET_INFINITY_SPOOL_ORDER ORDER="<order>"` - Set slot order (e.g., `"0,1,2,3"` or `"0,1,none,3"`)
- `ACE_INFINITY_SPOOL` - Auto change spool when empty (uses configured order)
- `RESET_INFINITY_SPOOL` - Reset position in order

### Infinity Spool Auto-trigger
When `infinity_spool_mode` is enabled, automatic monitoring of the active slot status works during printing:

**Algorithm:**
1. **Empty status detection:** Active slot status monitored every 0.5 seconds
2. **Debounce confirmation:** After `infinity_spool_debounce` seconds, status is rechecked
3. **Slot change:**
   - **With filament sensor:** Monitors sensor until triggered, then calls `ACE_INFINITY_SPOOL`
   - **Without sensor:**
     - If `infinity_spool_pause_on_no_sensor=True` - pause printing
     - If `infinity_spool_pause_on_no_sensor=False` - immediate `ACE_INFINITY_SPOOL` call

### Aliases
- `T0`, `T1`, `T2`, `T3` - Quick tool change (equivalent to `ACE_CHANGE_TOOL TOOL=0-3`)
- `TR` - Unload filament (equivalent to `ACE_CHANGE_TOOL TOOL=-1`)

## Full Documentation

For complete command documentation with examples, parameters, and usage notes, please refer to:
- **[Russian Commands Reference](../COMMANDS.md)** - Full documentation in Russian
- Source code: `extras/ace.py` - Implementation details

---

*Last updated: 2025*
