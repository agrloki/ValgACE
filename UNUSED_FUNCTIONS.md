# Неиспользуемые функции в ace.py

## 🔍 Результаты анализа

В файле `ace.py` найдено **3 неиспользуемые функции**:

---

## 1. `_wait_for_slot_ready` (строка 893-897)

**Определение:**
```python
def _wait_for_slot_ready(self, index, on_ready, event_time):
    if self._info['slots'][index]['status'] == 'ready':
        on_ready()
        return self.reactor.NEVER
    return event_time + 0.5
```

**Статус:** ❌ **НИГДЕ НЕ ВЫЗЫВАЕТСЯ**

**Причина:** 
- Похоже на старую/упрощенную версию функции
- Вместо неё используется `_wait_for_slot_ready_async` (строка 786), которая имеет более полную реализацию с таймаутами и логированием

**Использование вместо неё:**
- `_wait_for_slot_ready_async` вызывается в `cmd_ACE_CHANGE_TOOL` (строка 759)

**Рекомендация:** ⚠️ Удалить, так как функциональность полностью покрыта `_wait_for_slot_ready_async`

---

## 2. `_start_initial_toolchange_timer` (строка 818-831)

**Определение:**
```python
def _start_initial_toolchange_timer(self, tool, was, gcmd):
    """Timer for initial tool change (when was == -1)"""
    def timer_handler(eventtime):
        # Wait for parking to complete (parking sets _park_in_progress to False when done)
        if not self._park_in_progress:
            self.gcode.run_script_from_command(f'_ACE_POST_TOOLCHANGE FROM={was} TO={tool}')
            if self.toolhead:
                self.toolhead.wait_moves()
            self._save_variable('ace_current_index', tool)
            gcmd.respond_info(f"Tool changed from {was} to {tool}")
            return self.reactor.NEVER  # Stop timer
        # Continue checking every second
        return eventtime + 1.0
    self.reactor.register_timer(timer_handler, self.reactor.monotonic() + 1.0)
```

**Статус:** ❌ **НИГДЕ НЕ ВЫЗЫВАЕТСЯ**

**Причина:**
- Похоже на старую реализацию для случая, когда `was == -1` (начальная смена инструмента)
- В текущей реализации `cmd_ACE_CHANGE_TOOL` для этого случая используется прямое `dwell` с фиксированным таймаутом (строка 784)

**Текущая реализация:**
```python
# В cmd_ACE_CHANGE_TOOL, строка 773-784:
else:
    # Use G-code command for consistency
    self.gcode.run_script_from_command(f'ACE_PARK_TO_TOOLHEAD INDEX={tool}')
    
    def after_park_delay():
        self.gcode.run_script_from_command(f'_ACE_POST_TOOLCHANGE FROM={was} TO={tool}')
        if self.toolhead:
            self.toolhead.wait_moves()
        gcmd.respond_info(f"Tool changed from {was} to {tool}")
    
    # Wait 15 seconds for parking to complete
    self.dwell(15.0, after_park_delay)
```

**Рекомендация:** ⚠️ Удалить или использовать вместо фиксированного `dwell(15.0)` для более надежного ожидания завершения парковки

---

## 3. `_wait_for_park_completion_async` (строка 833-865)

**Определение:**
```python
def _wait_for_park_completion_async(self, tool, was, gcmd):
    """Asynchronous waiting for park to complete"""
    start_time = self.reactor.monotonic()
    max_wait_time = 30.0  # Maximum 30 seconds to wait
    
    def timer_handler(eventtime):
        # Check if parking failed
        if self._park_error:
            self.logger.error(f"Parking failed for slot {tool}, aborting toolchange")
            gcmd.respond_raw(f"ACE Error: Feed assist for slot {tool} not working")
            self._park_error = False
            return self.reactor.NEVER
        
        if not self._park_in_progress:
            self.logger.info(f"Parking completed for slot {tool}, executing post-toolchange")
            self.gcode.run_script_from_command(f'_ACE_POST_TOOLCHANGE FROM={was} TO={tool}')
            if self.toolhead:
                self.toolhead.wait_moves()
            gcmd.respond_info(f"Tool changed from {was} to {tool}")
            return self.reactor.NEVER
        
        # Timeout check
        elapsed = eventtime - start_time
        if elapsed > max_wait_time:
            self.logger.error(f"Parking timeout for slot {tool} after {elapsed:.1f}s")
            self._park_in_progress = False
            self._park_error = True
            gcmd.respond_raw(f"Parking timeout for slot {tool}")
            return self.reactor.NEVER
        
        # Continue checking
        return eventtime + 0.5
    self.reactor.register_timer(timer_handler, self.reactor.monotonic() + 0.5)
```

**Статус:** ❌ **НИГДЕ НЕ ВЫЗЫВАЕТСЯ**

**Причина:**
- Похоже на более продвинутую реализацию ожидания парковки с обработкой ошибок и таймаутами
- В текущей реализации `_on_slot_ready_callback` используется простой `dwell(10.0)` вместо этой функции

**Текущая реализация:**
```python
# В _on_slot_ready_callback, строка 879-891:
else:
    # Park new tool using G-code command (like in working version)
    self.logger.info(f"Starting parking of new tool {tool} using G-code command")
    self.gcode.run_script_from_command(f'ACE_PARK_TO_TOOLHEAD INDEX={tool}')
    
    def after_park_delay():
        self.logger.info(f"Parking delay complete for slot {tool}, executing post-toolchange")
        self.gcode.run_script_from_command(f'_ACE_POST_TOOLCHANGE FROM={was} TO={tool}')
        if self.toolhead:
            self.toolhead.wait_moves()
        gcmd.respond_info(f"Tool changed from {was} to {tool}")
    
    # Wait 10 seconds for parking to complete (like in working version)
    self.dwell(10.0, after_park_delay)
```

**Рекомендация:** 🔴 **ИСПОЛЬЗОВАТЬ** вместо `dwell(10.0)` — эта функция более надежна, так как:
- Проверяет состояние парковки, а не просто ждет фиксированное время
- Обрабатывает ошибки парковки (`_park_error`)
- Имеет таймаут с логированием
- Динамически проверяет завершение парковки

---

## 📊 Сводная таблица

| Функция | Строка | Статус | Действие |
|---------|--------|--------|----------|
| `_wait_for_slot_ready` | 893 | ❌ Не используется | Удалить |
| `_start_initial_toolchange_timer` | 818 | ❌ Не используется | Удалить или использовать вместо `dwell(15.0)` |
| `_wait_for_park_completion_async` | 833 | ❌ Не используется | **Использовать** вместо `dwell(10.0)` |

---

## 💡 Рекомендации

### Немедленные действия:

1. **Удалить `_wait_for_slot_ready`** — полностью покрыта функциональностью `_wait_for_slot_ready_async`

2. **Заменить `dwell(10.0)` на `_wait_for_park_completion_async`** в `_on_slot_ready_callback`:
   ```python
   # Вместо:
   self.dwell(10.0, after_park_delay)
   
   # Использовать:
   self._wait_for_park_completion_async(tool, was, gcmd)
   ```

3. **Рассмотреть использование `_start_initial_toolchange_timer`** вместо `dwell(15.0)` в `cmd_ACE_CHANGE_TOOL`:
   ```python
   # Вместо:
   self.dwell(15.0, after_park_delay)
   
   # Использовать:
   self._start_initial_toolchange_timer(tool, was, gcmd)
   ```

### Преимущества использования неиспользуемых функций:

- ✅ **Более надежная обработка** — проверка состояния вместо фиксированных задержек
- ✅ **Обработка ошибок** — реакция на `_park_error`
- ✅ **Динамические таймауты** — адаптация к реальному времени выполнения
- ✅ **Логирование** — подробная информация о процессе

---

*Дата анализа: 2024*  
*Версия файла: ace.py (1006 строк)*

