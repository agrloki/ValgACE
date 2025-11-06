<template>
  <v-card class="ace-status-card" :class="cardClass">
    <v-card-title class="d-flex align-center">
      <v-icon :color="statusColor" class="mr-2">mdi-printer-3d</v-icon>
      <span>ACE Status</span>
      <v-spacer></v-spacer>
      <v-chip :color="statusChipColor" small text-color="white">
        {{ deviceStatus }}
      </v-chip>
    </v-card-title>

    <v-card-text v-if="loading" class="text-center py-4">
      <v-progress-circular indeterminate color="primary"></v-progress-circular>
      <div class="mt-2">Загрузка статуса ACE...</div>
    </v-card-text>

    <v-card-text v-else-if="error" class="text-center py-4">
      <v-icon color="error" large>mdi-alert-circle</v-icon>
      <div class="mt-2 text-error">{{ error }}</div>
      <v-btn small color="primary" @click="loadStatus" class="mt-2">
        Повторить
      </v-btn>
    </v-card-text>

    <v-card-text v-else>
      <!-- Device Info -->
      <div class="mb-4">
        <div class="text-caption text--secondary mb-1">Устройство</div>
        <div class="text-body-2">
          <strong>{{ deviceInfo.model || 'Unknown' }}</strong>
          <span class="text--secondary ml-2">{{ deviceInfo.firmware || '' }}</span>
        </div>
      </div>

      <!-- Dryer Status -->
      <div class="mb-4">
        <div class="d-flex align-center justify-space-between mb-1">
          <span class="text-caption text--secondary">Сушка</span>
          <v-chip :color="dryerChipColor" x-small>
            {{ dryerStatusText }}
          </v-chip>
        </div>
        <div class="text-body-2">
          Температура: <strong>{{ deviceInfo.temp || 0 }}°C</strong>
          <span v-if="isDrying" class="ml-2">
            → {{ dryerInfo.target_temp }}°C
          </span>
        </div>
        <div v-if="isDrying" class="text-caption text--secondary mt-1">
          Осталось: {{ formatTime(dryerInfo.remain_time) }}
        </div>
      </div>

      <!-- Slots Grid -->
      <div>
        <div class="text-caption text--secondary mb-2">Слоты филамента</div>
        <div class="slots-grid">
          <div
            v-for="slot in slots"
            :key="slot.index"
            class="slot-item"
            :class="slot.status"
          >
            <div class="slot-header">
              <span class="slot-number">#{{ slot.index }}</span>
              <v-chip
                :color="getSlotChipColor(slot.status)"
                x-small
                text-color="white"
              >
                {{ getStatusText(slot.status) }}
              </v-chip>
            </div>
            <div
              v-if="slot.color && slot.color.length >= 3"
              class="color-indicator"
              :style="`background-color: rgb(${slot.color[0]}, ${slot.color[1]}, ${slot.color[2]})`"
            ></div>
            <div v-if="slot.type" class="text-caption mt-1">
              {{ slot.type }}
            </div>
            <div v-if="slot.sku" class="text-caption text--secondary">
              {{ slot.sku }}
            </div>
          </div>
        </div>
      </div>

      <!-- Quick Actions -->
      <v-divider class="my-3"></v-divider>
      <div class="d-flex justify-space-between">
        <v-btn
          small
          color="primary"
          @click="changeTool(0)"
          :disabled="!isSlotReady(0)"
        >
          Слот 0
        </v-btn>
        <v-btn
          small
          color="primary"
          @click="changeTool(1)"
          :disabled="!isSlotReady(1)"
        >
          Слот 1
        </v-btn>
        <v-btn
          small
          color="primary"
          @click="changeTool(2)"
          :disabled="!isSlotReady(2)"
        >
          Слот 2
        </v-btn>
        <v-btn
          small
          color="primary"
          @click="changeTool(3)"
          :disabled="!isSlotReady(3)"
        >
          Слот 3
        </v-btn>
      </div>
    </v-card-text>

    <v-card-actions v-if="!loading && !error">
      <v-spacer></v-spacer>
      <v-btn small text @click="loadStatus">
        <v-icon small class="mr-1">mdi-refresh</v-icon>
        Обновить
      </v-btn>
    </v-card-actions>
  </v-card>
</template>

<script>
export default {
  name: 'AceStatusCard',
  data() {
    return {
      loading: true,
      error: null,
      deviceInfo: {},
      slots: [],
      dryerInfo: {},
      updateInterval: null,
      wsConnection: null
    };
  },
  computed: {
    deviceStatus() {
      return this.deviceInfo.status || 'unknown';
    },
    statusColor() {
      const status = this.deviceStatus.toLowerCase();
      if (status === 'ready') return 'success';
      if (status === 'busy') return 'warning';
      return 'error';
    },
    statusChipColor() {
      const status = this.deviceStatus.toLowerCase();
      if (status === 'ready') return 'success';
      if (status === 'busy') return 'warning';
      return 'error';
    },
    cardClass() {
      return {
        'ace-status-ready': this.deviceStatus === 'ready',
        'ace-status-busy': this.deviceStatus === 'busy',
        'ace-status-error': this.deviceStatus !== 'ready' && this.deviceStatus !== 'busy'
      };
    },
    isDrying() {
      return this.dryerInfo.status === 'drying';
    },
    dryerStatusText() {
      return this.isDrying ? 'Сушка' : 'Остановлено';
    },
    dryerChipColor() {
      return this.isDrying ? 'warning' : 'grey';
    }
  },
  mounted() {
    this.loadStatus();
    // Обновление каждые 10 секунд
    this.updateInterval = setInterval(() => {
      this.loadStatus();
    }, 10000);
    
    // Подключение к WebSocket для real-time обновлений
    this.connectWebSocket();
  },
  beforeDestroy() {
    if (this.updateInterval) {
      clearInterval(this.updateInterval);
    }
    if (this.wsConnection) {
      this.wsConnection.close();
    }
  },
  methods: {
    async loadStatus() {
      try {
        this.loading = true;
        this.error = null;
        
        // Попытка получить через Moonraker API
        const response = await fetch('/server/ace/status');
        
        if (!response.ok) {
          throw new Error(`HTTP ${response.status}`);
        }
        
        const data = await response.json();
        
        if (data.error) {
          throw new Error(data.error);
        }
        
        if (data.result) {
          this.deviceInfo = data.result;
          this.slots = data.result.slots || [];
          this.dryerInfo = data.result.dryer || data.result.dryer_status || {};
        }
      } catch (error) {
        console.error('Error loading ACE status:', error);
        this.error = `Ошибка загрузки: ${error.message}`;
        
        // Fallback: попытка через G-code команду
        await this.loadStatusViaGcode();
      } finally {
        this.loading = false;
      }
    },
    
    async loadStatusViaGcode() {
      try {
        const response = await fetch('/server/printer/gcode/script', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ script: 'ACE_STATUS' })
        });
        
        const data = await response.json();
        
        if (data.error) {
          throw new Error(data.error);
        }
        
        // Парсинг текстового ответа (упрощенный)
        // В реальной реализации нужен полноценный парсер
        this.error = 'Используйте Moonraker API extension для полной функциональности';
      } catch (error) {
        console.error('Error loading via G-code:', error);
      }
    },
    
    connectWebSocket() {
      try {
        const wsUrl = `ws://${window.location.hostname}:7125/websocket`;
        this.wsConnection = new WebSocket(wsUrl);
        
        this.wsConnection.onopen = () => {
          console.log('ACE Status: WebSocket connected');
          
          // Подписка на обновления статуса принтера
          this.wsConnection.send(JSON.stringify({
            jsonrpc: "2.0",
            method: "printer.objects.subscribe",
            params: {
              objects: {
                "ace": null
              }
            },
            id: 5434
          }));
        };
        
        this.wsConnection.onmessage = (event) => {
          const data = JSON.parse(event.data);
          
          if (data.method === "notify_status_update") {
            const aceData = data.params[0]?.ace;
            if (aceData) {
              this.deviceInfo = { ...this.deviceInfo, ...aceData };
              if (aceData.slots) {
                this.slots = aceData.slots;
              }
              if (aceData.dryer || aceData.dryer_status) {
                this.dryerInfo = aceData.dryer || aceData.dryer_status;
              }
            }
          }
        };
        
        this.wsConnection.onerror = (error) => {
          console.error('ACE Status: WebSocket error:', error);
        };
        
        this.wsConnection.onclose = () => {
          console.log('ACE Status: WebSocket closed');
          // Переподключение через 5 секунд
          setTimeout(() => this.connectWebSocket(), 5000);
        };
      } catch (error) {
        console.error('Error connecting WebSocket:', error);
      }
    },
    
    async changeTool(tool) {
      try {
        const response = await fetch('/server/ace/command', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            command: 'ACE_CHANGE_TOOL',
            params: { TOOL: tool }
          })
        });
        
        const data = await response.json();
        
        if (data.error) {
          this.$store.dispatch('notifications/add', {
            type: 'error',
            title: 'Ошибка смены инструмента',
            message: data.error
          });
        } else {
          this.$store.dispatch('notifications/add', {
            type: 'success',
            title: 'Смена инструмента',
            message: `Переключение на слот ${tool}...`
          });
          
          // Обновить статус через 2 секунды
          setTimeout(() => this.loadStatus(), 2000);
        }
      } catch (error) {
        this.$store.dispatch('notifications/add', {
          type: 'error',
          title: 'Ошибка',
          message: error.message
        });
      }
    },
    
    isSlotReady(index) {
      const slot = this.slots.find(s => s.index === index);
      return slot && slot.status === 'ready';
    },
    
    getSlotChipColor(status) {
      if (status === 'ready') return 'success';
      if (status === 'empty') return 'grey';
      return 'warning';
    },
    
    getStatusText(status) {
      const statusMap = {
        'ready': 'Готов',
        'empty': 'Пусто',
        'busy': 'Занят'
      };
      return statusMap[status] || status;
    },
    
    formatTime(minutes) {
      if (!minutes) return '0м';
      const hours = Math.floor(minutes / 60);
      const mins = minutes % 60;
      if (hours > 0) {
        return `${hours}ч ${mins}м`;
      }
      return `${mins}м`;
    }
  }
};
</script>

<style scoped>
.ace-status-card {
  transition: all 0.3s ease;
}

.ace-status-ready {
  border-left: 4px solid #4caf50;
}

.ace-status-busy {
  border-left: 4px solid #ff9800;
}

.ace-status-error {
  border-left: 4px solid #f44336;
}

.slots-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 8px;
  margin-top: 8px;
}

.slot-item {
  padding: 8px;
  border-radius: 4px;
  background-color: rgba(0, 0, 0, 0.02);
  border: 1px solid rgba(0, 0, 0, 0.1);
  transition: all 0.2s ease;
}

.slot-item:hover {
  background-color: rgba(0, 0, 0, 0.05);
  transform: translateY(-2px);
}

.slot-item.ready {
  border-color: #4caf50;
}

.slot-item.empty {
  border-color: #9e9e9e;
  opacity: 0.7;
}

.slot-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 4px;
}

.slot-number {
  font-weight: bold;
  font-size: 14px;
}

.color-indicator {
  width: 100%;
  height: 24px;
  border-radius: 4px;
  margin: 4px 0;
  border: 1px solid rgba(0, 0, 0, 0.1);
}

@media (max-width: 960px) {
  .slots-grid {
    grid-template-columns: repeat(2, 1fr);
  }
}

@media (max-width: 600px) {
  .slots-grid {
    grid-template-columns: 1fr;
  }
}
</style>

