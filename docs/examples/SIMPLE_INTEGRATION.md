# Упрощенная версия компонента для быстрой интеграции

Если вы хотите быстро добавить статус ACE без полной установки компонента, используйте этот упрощенный вариант.

## Встраивание через iframe

Самый простой способ - использовать веб-интерфейс через iframe:

```vue
<template>
  <v-card>
    <v-card-title>ACE Status</v-card-title>
    <v-card-text style="padding: 0;">
      <iframe
        src="/ace_dashboard.html"
        style="width: 100%; height: 600px; border: none;"
        frameborder="0"
      ></iframe>
    </v-card-text>
  </v-card>
</template>
```

## Минимальный компонент для Dashboard

Если нужен минимальный компонент только для отображения статуса:

```vue
<template>
  <v-card>
    <v-card-title>ACE Status</v-card-title>
    <v-card-text>
      <div v-if="loading">Загрузка...</div>
      <div v-else>
        <div>Статус: {{ status }}</div>
        <div v-for="slot in slots" :key="slot.index">
          Слот {{ slot.index }}: {{ slot.status }}
        </div>
      </div>
    </v-card-text>
  </v-card>
</template>

<script>
export default {
  data() {
    return {
      loading: true,
      status: 'unknown',
      slots: []
    };
  },
  async mounted() {
    await this.loadStatus();
  },
  methods: {
    async loadStatus() {
      try {
        const response = await fetch('/server/ace/status');
        const data = await response.json();
        this.status = data.result.status;
        this.slots = data.result.slots || [];
      } catch (error) {
        console.error('Error:', error);
      } finally {
        this.loading = false;
      }
    }
  }
};
</script>
```

## Использование через G-code команды (без API extension)

Если Moonraker API extension не установлен, можно использовать напрямую через G-code:

```vue
<script>
export default {
  methods: {
    async getAceStatus() {
      const response = await fetch('/server/printer/gcode/script', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ script: 'ACE_STATUS' })
      });
      const data = await response.json();
      // Парсинг текстового ответа
      return this.parseAceStatus(data.result);
    },
    
    parseAceStatus(text) {
      // Простой парсер для текстового ответа ACE_STATUS
      // В реальной реализации нужен более сложный парсер
      const lines = text.split('\n');
      const status = {};
      // ... парсинг ...
      return status;
    }
  }
};
</script>
```

---

*Для полной функциональности рекомендуется использовать основной компонент [AceStatusCard.vue](AceStatusCard.vue)*

