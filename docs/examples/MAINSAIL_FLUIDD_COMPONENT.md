# Кастомный компонент ACE Status для Mainsail/Fluidd

Этот компонент позволяет отображать статус ACE устройства прямо на главной панели Mainsail или Fluidd.

## Описание

Компонент `AceStatusCard.vue` - это Vue компонент, который:
- ✅ Отображает статус ACE устройства в реальном времени
- ✅ Показывает информацию о всех 4 слотах филамента
- ✅ Отображает статус сушки
- ✅ Позволяет быстро переключаться между слотами
- ✅ Поддерживает WebSocket для мгновенных обновлений
- ✅ Адаптивный дизайн для мобильных устройств

## Установка

### Для Mainsail

1. Скопируйте компонент в директорию Mainsail:
```bash
cp AceStatusCard.vue ~/mainsail/src/components/AceStatusCard.vue
```

2. Зарегистрируйте компонент в `~/mainsail/src/main.js`:
```javascript
import AceStatusCard from '@/components/AceStatusCard.vue'

// В секции components или глобально
Vue.component('AceStatusCard', AceStatusCard)
```

3. Добавьте компонент на главную страницу в `~/mainsail/src/pages/Dashboard.vue`:
```vue
<template>
  <v-container>
    <!-- Существующие компоненты -->
    
    <!-- ACE Status Card -->
    <v-row>
      <v-col cols="12" md="6" lg="4">
        <ace-status-card></ace-status-card>
      </v-col>
    </v-row>
  </v-container>
</template>

<script>
import AceStatusCard from '@/components/AceStatusCard.vue'

export default {
  components: {
    AceStatusCard
  }
}
</script>
```

### Для Fluidd

1. Скопируйте компонент в директорию Fluidd:
```bash
cp AceStatusCard.vue ~/fluidd/src/components/AceStatusCard.vue
```

2. Зарегистрируйте компонент в `~/fluidd/src/main.js`:
```javascript
import AceStatusCard from '@/components/AceStatusCard.vue'

Vue.component('AceStatusCard', AceStatusCard)
```

3. Добавьте компонент на главную страницу в `~/fluidd/src/pages/Dashboard.vue`:
```vue
<template>
  <div class="dashboard">
    <!-- Существующие компоненты -->
    
    <!-- ACE Status Card -->
    <ace-status-card></ace-status-card>
  </div>
</template>

<script>
import AceStatusCard from '@/components/AceStatusCard.vue'

export default {
  components: {
    AceStatusCard
  }
}
</script>
```

## Использование как плагин (рекомендуется)

Для более удобной установки можно создать плагин:

### Структура плагина

```
mainsail-ace-status/
├── package.json
├── index.js
└── components/
    └── AceStatusCard.vue
```

### package.json

```json
{
  "name": "mainsail-ace-status",
  "version": "1.0.0",
  "description": "ACE Status component for Mainsail",
  "main": "index.js"
}
```

### index.js

```javascript
import AceStatusCard from './components/AceStatusCard.vue'

export default {
  install(Vue) {
    Vue.component('AceStatusCard', AceStatusCard)
  }
}
```

### Использование плагина

В `main.js`:
```javascript
import AceStatusPlugin from './plugins/mainsail-ace-status'

Vue.use(AceStatusPlugin)
```

## Требования

1. **Moonraker API Extension** - компонент требует установки `ace_status.py` для работы через REST API
   
   См. [README.md](README.md) для инструкций по установке Moonraker API extension.

2. **Vue.js** - компонент использует Vue 2.x (совместим с Mainsail/Fluidd)

3. **Vuetify** - компонент использует компоненты Vuetify (уже включен в Mainsail/Fluidd)

## API эндпоинты

Компонент использует следующие эндпоинты:

- `GET /server/ace/status` - получение статуса ACE
- `POST /server/ace/command` - выполнение ACE команд
- WebSocket подписка на `printer.objects.subscribe` для real-time обновлений

## Настройка

### Изменение интервала обновления

В компоненте измените значение в `mounted()`:
```javascript
// Обновление каждые 10 секунд (по умолчанию)
this.updateInterval = setInterval(() => {
  this.loadStatus();
}, 10000); // Измените на нужное значение в миллисекундах
```

### Отключение WebSocket

Закомментируйте вызов в `mounted()`:
```javascript
// this.connectWebSocket();
```

### Кастомизация стилей

Измените стили в секции `<style scoped>` компонента для соответствия вашему дизайну.

## Примеры использования

### Базовое использование

```vue
<template>
  <ace-status-card></ace-status-card>
</template>
```

### С кастомными размерами

```vue
<template>
  <v-col cols="12" md="6" lg="4">
    <ace-status-card></ace-status-card>
  </v-col>
</template>
```

### В модальном окне

```vue
<template>
  <v-dialog v-model="showAceStatus" max-width="600">
    <v-card>
      <v-card-title>ACE Status</v-card-title>
      <v-card-text>
        <ace-status-card></ace-status-card>
      </v-card-text>
    </v-card>
  </v-dialog>
</template>
```

## Устранение неполадок

### Компонент не загружается

1. Проверьте, что Moonraker API extension установлен и работает
2. Проверьте консоль браузера на наличие ошибок
3. Убедитесь, что компонент правильно зарегистрирован

### Данные не обновляются

1. Проверьте WebSocket подключение в консоли браузера
2. Убедитесь, что эндпоинт `/server/ace/status` доступен
3. Проверьте логи Moonraker на наличие ошибок

### Ошибка "Failed to fetch"

1. Убедитесь, что Moonraker запущен
2. Проверьте настройки CORS в Moonraker
3. Проверьте, что API extension правильно установлен

## Дополнительные возможности

### Добавление дополнительных действий

Вы можете расширить компонент, добавив дополнительные кнопки действий:

```vue
<v-card-actions>
  <v-btn @click="startDrying">Запустить сушку</v-btn>
  <v-btn @click="parkFilament">Парковка</v-btn>
</v-card-actions>
```

### Интеграция с уведомлениями

Компонент уже использует систему уведомлений Mainsail/Fluidd:
```javascript
this.$store.dispatch('notifications/add', {
  type: 'success',
  title: 'Успех',
  message: 'Операция выполнена'
});
```

## Совместимость

- ✅ Mainsail 2.x
- ✅ Fluidd 1.x
- ✅ Vue 2.x
- ✅ Vuetify 2.x

## Лицензия

Этот компонент распространяется под той же лицензией, что и основной проект ValgACE (GPL v3).

---

*Для получения дополнительной информации см. [README.md](README.md)*

