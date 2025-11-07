# Support for ACE device temperature sensor
#
# Copyright (C) 2025
#
# This file may be distributed under the terms of the GNU GPLv3 license.
#
# This module provides temperature sensor for Anycubic Color Engine (ACE)
# Reads temperature from ACE device status

import logging

ACE_REPORT_TIME = 1.0  # Report temperature every second

class TemperatureACE:
    """
    Temperature sensor that reads temperature from ACE device
    Integrates with Klipper's temperature monitoring system
    """
    
    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.name = config.get_name().split()[-1]
        
        # ACE module reference (will be set in handle_ready)
        self.ace = None
        
        # Temperature state
        self.temp = 0.0
        self.min_temp = 0.0
        self.max_temp = 70.0
        self.measured_min = 99999999.
        self.measured_max = 0.
        
        # Callback for temperature updates
        self._callback = None
        
        # Register object
        self.printer.add_object("temperature_ace " + self.name, self)
        
        # Skip timer setup if in debug mode
        if self.printer.get_start_args().get('debugoutput') is not None:
            return
        
        # Register timer for periodic temperature reading
        self.sample_timer = self.reactor.register_timer(
            self._sample_ace_temperature)
        
        # Register event handler to start after connection
        self.printer.register_event_handler("klippy:connect",
                                            self.handle_connect)
        self.printer.register_event_handler("klippy:ready",
                                            self.handle_ready)
    
    def handle_ready(self):
        """Get reference to ACE module when Klipper is ready"""
        try:
            self.ace = self.printer.lookup_object('ace')
            logging.info("ACE temperature sensor: ACE module found and linked")
        except self.printer.config_error:
            logging.warning("ACE temperature sensor: ACE module not found, sensor will report 0")
            self.ace = None
        except Exception as e:
            logging.error(f"ACE temperature sensor: Error linking to ACE module: {e}")
            self.ace = None
        
        # Start temperature sampling timer (if not in debug mode)
        if hasattr(self, 'sample_timer'):
            self.reactor.update_timer(self.sample_timer, self.reactor.NOW)
    
    def handle_connect(self):
        """Start temperature sampling when Klipper connects"""
        if hasattr(self, 'sample_timer'):
            self.reactor.update_timer(self.sample_timer, self.reactor.NOW)
    
    def setup_minmax(self, min_temp, max_temp):
        """Setup min/max temperature limits (required by heaters system)"""
        self.min_temp = min_temp
        self.max_temp = max_temp
    
    def setup_callback(self, cb):
        """Setup callback for temperature updates (required by heaters system)"""
        self._callback = cb
    
    def get_report_time_delta(self):
        """Return time interval between temperature reports (required by heaters system)"""
        return ACE_REPORT_TIME
    
    def _sample_ace_temperature(self, eventtime):
        """Periodic temperature sampling from ACE device"""
        # Log first successful sample
        if not hasattr(self, '_sample_logged'):
            self._sample_logged = False
        
        try:
            if self.ace and hasattr(self.ace, '_info'):
                # Get temperature from ACE device info
                ace_temp = self.ace._info.get('temp', 0.0)
                
                # Log first successful temperature reading
                if not self._sample_logged and ace_temp > 0:
                    logging.info(f"ACE temperature sensor: Started sampling, current temp={ace_temp}°C")
                    self._sample_logged = True
                
                self.temp = float(ace_temp)
                
                # Track min/max
                if self.temp > 0:  # Only track valid temperatures
                    self.measured_min = min(self.measured_min, self.temp)
                    self.measured_max = max(self.measured_max, self.temp)
                
                # Check temperature limits
                if self.temp < self.min_temp and self.temp > 0:
                    self.printer.invoke_shutdown(
                        "ACE temperature %.1f below minimum temperature of %.1f"
                        % (self.temp, self.min_temp))
                if self.temp > self.max_temp:
                    self.printer.invoke_shutdown(
                        "ACE temperature %.1f above maximum temperature of %.1f"
                        % (self.temp, self.max_temp))
            else:
                # ACE not available, report 0
                if not hasattr(self, '_warning_shown'):
                    logging.warning(f"temperature_ace: ACE module not available or _info not set (ace={self.ace}, has_info={hasattr(self.ace, '_info') if self.ace else False})")
                    self._warning_shown = True
                self.temp = 0.0
        except Exception:
            logging.exception("temperature_ace: Error reading temperature from ACE")
            self.temp = 0.0
        
        # Call temperature callback if set
        if self._callback:
            mcu = self.printer.lookup_object('mcu')
            measured_time = self.reactor.monotonic()
            self._callback(mcu.estimated_print_time(measured_time), self.temp)
        
        # Schedule next sample
        return eventtime + ACE_REPORT_TIME
    
    def get_temp(self, eventtime):
        """Get current temperature (required for temperature_sensor compatibility)"""
        return self.temp, 0.0
    
    def stats(self, eventtime):
        """Return statistics string for logging"""
        return False, 'temperature_ace %s: temp=%.1f' % (self.name, self.temp)
    
    def get_status(self, eventtime):
        """Return status for Moonraker/API"""
        return {
            'temperature': round(self.temp, 2),
            'measured_min_temp': round(self.measured_min, 2),
            'measured_max_temp': round(self.measured_max, 2),
        }


def load_config(config):
    """Register temperature_ace sensor factory"""
    # Register sensor factory with heaters system
    pheaters = config.get_printer().load_object(config, "heaters")
    pheaters.add_sensor_factory("temperature_ace", TemperatureACE)

