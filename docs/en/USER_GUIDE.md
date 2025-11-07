# ValgACE User Guide

Complete guide to using the ValgACE module.

**Note:** This is a summary. For detailed documentation, see the full [Russian User Guide](../USER_GUIDE.md).

## Quick Start

### 1. Check Connection

```gcode
ACE_STATUS
ACE_DEBUG METHOD=get_info
```

### 2. Load Filament

```gcode
ACE_CHANGE_TOOL TOOL=0  # Load slot 0
# Or use alias:
T0
```

### 3. Check Status

```gcode
ACE_STATUS
```

## Basic Concepts

### Slots
- 4 slots available (indices 0-3)
- Each slot can be `ready` or `empty`

### Tools
- `TOOL=-1`: Unload filament (no tool)
- `TOOL=0-3`: Load filament from corresponding slot

### Parking
Process of feeding filament from ACE to nozzle. Success is determined by stabilizing the feed assist counter.

## Common Tasks

### Tool Change

```gcode
# Load slot 0
ACE_CHANGE_TOOL TOOL=0
# Or: T0

# Unload current filament
ACE_CHANGE_TOOL TOOL=-1
# Or: TR
```

### Feed/Retract

```gcode
# Feed 50mm from slot 0
ACE_FEED INDEX=0 LENGTH=50 SPEED=25

# Retract 50mm back
ACE_RETRACT INDEX=0 LENGTH=50 SPEED=25
```

### Drying

```gcode
# Start drying for 2 hours at 50°C
ACE_START_DRYING TEMP=50 DURATION=120

# Stop drying
ACE_STOP_DRYING
```

### Infinity Spool

```gcode
# 1. First, set slot order
ACE_SET_INFINITY_SPOOL_ORDER ORDER="0,1,2,3"

# Or with empty slot skip:
ACE_SET_INFINITY_SPOOL_ORDER ORDER="0,1,none,3"

# 2. When filament runs out during printing
ACE_INFINITY_SPOOL

# Automatically switches to next slot according to order
```

## Full Documentation

For complete user guide with examples, scenarios, and integration guides, please refer to:
- **[Russian User Guide](../USER_GUIDE.md)** - Full documentation in Russian

---

*Last updated: 2025*

