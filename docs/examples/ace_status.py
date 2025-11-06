"""
Moonraker API Extension для ValgACE

Этот компонент расширяет Moonraker API для доступа к статусу ACE через REST API и WebSocket.

Установка:
1. Скопируйте в ~/moonraker/moonraker/components/ace_status.py
2. Добавьте в moonraker.conf:
   [components]
   ace_status: ace_status
"""

from __future__ import annotations
import logging
from typing import TYPE_CHECKING, Optional, Dict, Any
if TYPE_CHECKING:
    from confighelper import ConfigHelper

class AceStatus:
    def __init__(self, config: ConfigHelper):
        self.server = config.get_server()
        self.logger = logging.getLogger(__name__)
        
        # Регистрация API эндпоинтов
        self.server.register_endpoint(
            "/server/ace/status",
            ["GET"],
            self._handle_status_request
        )
        self.server.register_endpoint(
            "/server/ace/slots",
            ["GET"],
            self._handle_slots_request
        )
        self.server.register_endpoint(
            "/server/ace/command",
            ["POST"],
            self._handle_command_request
        )
        
        # Подписка на обновления статуса принтера
        self.server.register_event_handler(
            "klippy:status_update",
            self._handle_status_update
        )
        
        # Кэш последнего статуса
        self._last_status: Optional[Dict[str, Any]] = None
        
        self.logger.info("ACE Status API extension loaded")
    
    async def _handle_status_request(self, web_request) -> Dict[str, Any]:
        """Обработка запроса статуса ACE"""
        try:
            # Получаем статус через G-code команду
            klippy = self.server.lookup_component("klippy_connection")
            
            # Выполняем команду ACE_STATUS через G-code
            result = await klippy.request(
                {"method": "printer.gcode.script", "params": {"script": "ACE_STATUS"}}
            )
            
            # Парсим ответ и возвращаем структурированные данные
            # В реальной реализации нужно парсить вывод команды
            # или получать данные напрямую из модуля ace
            
            return {
                "result": self._parse_ace_status(result)
            }
        except Exception as e:
            self.logger.error(f"Error getting ACE status: {e}")
            return {"error": str(e)}
    
    async def _handle_slots_request(self, web_request) -> Dict[str, Any]:
        """Обработка запроса информации о слотах"""
        try:
            status = await self._handle_status_request(web_request)
            if "result" in status:
                return {
                    "result": {
                        "slots": status["result"].get("slots", [])
                    }
                }
            return status
        except Exception as e:
            self.logger.error(f"Error getting slots: {e}")
            return {"error": str(e)}
    
    async def _handle_command_request(self, web_request) -> Dict[str, Any]:
        """Обработка выполнения команды ACE"""
        try:
            command = web_request.get("command")
            params = web_request.get("params", {})
            
            if not command:
                return {"error": "Command is required"}
            
            # Формируем G-code команду
            gcode_cmd = command
            if params:
                param_str = " ".join([f"{k}={v}" for k, v in params.items()])
                gcode_cmd = f"{command} {param_str}"
            
            # Выполняем команду
            klippy = self.server.lookup_component("klippy_connection")
            result = await klippy.request(
                {"method": "printer.gcode.script", "params": {"script": gcode_cmd}}
            )
            
            return {
                "result": {
                    "success": True,
                    "message": "Command executed",
                    "response": result
                }
            }
        except Exception as e:
            self.logger.error(f"Error executing ACE command: {e}")
            return {"error": str(e)}
    
    def _handle_status_update(self, status: Dict[str, Any]) -> None:
        """Обработка обновления статуса принтера"""
        # Извлекаем данные ACE из статуса принтера
        # В реальной реализации нужно получить данные из модуля ace
        ace_data = self._extract_ace_data(status)
        
        if ace_data:
            self._last_status = ace_data
            # Отправляем обновление через WebSocket
            self.server.send_event("ace:status_update", ace_data)
    
    def _parse_ace_status(self, gcode_response: Dict[str, Any]) -> Dict[str, Any]:
        """Парсинг статуса ACE из ответа G-code команды"""
        # В реальной реализации нужно парсить вывод команды ACE_STATUS
        # или получать данные напрямую из модуля ace через printer object
        
        # Пример структуры данных
        return {
            "status": "ready",
            "model": "Anycubic Color Engine Pro",
            "firmware": "V1.3.84",
            "dryer": {
                "status": "stop",
                "target_temp": 0,
                "duration": 0,
                "remain_time": 0
            },
            "temp": 25,
            "fan_speed": 7000,
            "enable_rfid": 1,
            "slots": [
                {
                    "index": 0,
                    "status": "ready",
                    "type": "PLA",
                    "color": [255, 0, 0],
                    "sku": "",
                    "rfid": 2
                }
            ]
        }
    
    def _extract_ace_data(self, status: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Извлечение данных ACE из статуса принтера"""
        # В реальной реализации нужно получить данные из модуля ace
        # через printer.lookup_object('ace')
        return None

def load_component(config: ConfigHelper) -> AceStatus:
    return AceStatus(config)

