// ValgACE Dashboard JavaScript

const { createApp } = Vue;

createApp({
    data() {
        return {
            // Connection
            wsConnected: false,
            ws: null,
            apiBase: ACE_DASHBOARD_CONFIG?.apiBase || window.location.origin,
            
            // Device Status
            deviceStatus: {
                status: 'unknown',
                model: '',
                firmware: '',
                temp: 0,
                fan_speed: 0,
                enable_rfid: 0
            },
            
            // Dryer
            dryerStatus: {
                status: 'stop',
                target_temp: 0,
                duration: 0,
                remain_time: 0
            },
            dryingTemp: ACE_DASHBOARD_CONFIG?.defaults?.dryingTemp || 50,
            dryingDuration: ACE_DASHBOARD_CONFIG?.defaults?.dryingDuration || 240,
            
            // Slots
            slots: [],
            currentTool: -1,
            feedAssistSlot: -1,  // Индекс слота с активным feed assist (-1 = выключен)
            
            // Modals
            showFeedModal: false,
            showRetractModal: false,
            feedSlot: 0,
            feedLength: ACE_DASHBOARD_CONFIG?.defaults?.feedLength || 50,
            feedSpeed: ACE_DASHBOARD_CONFIG?.defaults?.feedSpeed || 25,
            retractSlot: 0,
            retractLength: ACE_DASHBOARD_CONFIG?.defaults?.retractLength || 50,
            retractSpeed: ACE_DASHBOARD_CONFIG?.defaults?.retractSpeed || 25,
            
            // Notifications
            notification: {
                show: false,
                message: '',
                type: 'info'
            }
        };
    },
    
    mounted() {
        this.connectWebSocket();
        this.loadStatus();
        
            // Auto-refresh
        const refreshInterval = ACE_DASHBOARD_CONFIG?.autoRefreshInterval || 5000;
        setInterval(() => {
            if (this.wsConnected) {
                this.loadStatus();
            }
        }, refreshInterval);
    },
    
    methods: {
        // WebSocket Connection
        connectWebSocket() {
            const wsUrl = getWebSocketUrl();
            
            this.ws = new WebSocket(wsUrl);
            
            this.ws.onopen = () => {
                this.wsConnected = true;
                this.showNotification('WebSocket подключен', 'success');
                this.subscribeToStatus();
            };
            
            this.ws.onmessage = (event) => {
                try {
                    const data = JSON.parse(event.data);
                    this.handleWebSocketMessage(data);
                } catch (e) {
                    console.error('Error parsing WebSocket message:', e);
                }
            };
            
            this.ws.onerror = (error) => {
                console.error('WebSocket error:', error);
                this.wsConnected = false;
            };
            
            this.ws.onclose = () => {
                this.wsConnected = false;
                this.showNotification('WebSocket отключен', 'error');
                // Reconnect after configured timeout
                const reconnectTimeout = ACE_DASHBOARD_CONFIG?.wsReconnectTimeout || 3000;
                setTimeout(() => this.connectWebSocket(), reconnectTimeout);
            };
        },
        
        subscribeToStatus() {
            if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return;
            
            this.ws.send(JSON.stringify({
                jsonrpc: "2.0",
                method: "printer.objects.subscribe",
                params: {
                    objects: {
                        "ace": null
                    }
                },
                id: 5434
            }));
        },
        
        handleWebSocketMessage(data) {
            if (data.method === "notify_status_update") {
                const aceData = data.params[0]?.ace;
                if (aceData) {
                    this.updateStatus(aceData);
                }
            }
        },
        
        // API Calls
        async loadStatus() {
            try {
                const response = await fetch(`${this.apiBase}/server/ace/status`);
                
                if (!response.ok) {
                    throw new Error(`HTTP ${response.status}: ${response.statusText}`);
                }
                
                const result = await response.json();
                
                if (ACE_DASHBOARD_CONFIG?.debug) {
                    console.log('Status response:', result);
                }
                
                if (result.error) {
                    console.error('API error:', result.error);
                    this.showNotification(`Ошибка API: ${result.error}`, 'error');
                    return;
                }
                
                if (result.result) {
                    this.updateStatus(result.result);
                } else {
                    console.warn('No result in response:', result);
                }
            } catch (error) {
                console.error('Error loading status:', error);
                this.showNotification(`Ошибка загрузки статуса: ${error.message}`, 'error');
            }
        },
        
        updateStatus(data) {
            if (!data || typeof data !== 'object') {
                console.warn('Invalid status data:', data);
                return;
            }
            
            if (ACE_DASHBOARD_CONFIG?.debug) {
                console.log('Updating status with data:', data);
            }
            
            // Обновляем статус устройства
            this.deviceStatus = {
                status: data.status || 'unknown',
                model: data.model || '',
                firmware: data.firmware || '',
                temp: data.temp || 0,
                fan_speed: data.fan_speed || 0,
                enable_rfid: data.enable_rfid || 0
            };
            
            // Обновляем статус сушилки
            const dryer = data.dryer || data.dryer_status || {};
            this.dryerStatus = {
                status: dryer.status || 'stop',
                target_temp: dryer.target_temp || 0,
                duration: dryer.duration || 0,
                remain_time: dryer.remain_time || 0
            };
            
            // Обновляем слоты
            if (Array.isArray(data.slots)) {
                this.slots = data.slots.map(slot => ({
                    index: slot.index !== undefined ? slot.index : -1,
                    status: slot.status || 'unknown',
                    type: slot.type || '',
                    color: Array.isArray(slot.color) ? slot.color : [0, 0, 0],
                    sku: slot.sku || '',
                    rfid: slot.rfid !== undefined ? slot.rfid : 0
                }));
            } else {
                console.warn('Slots data is not an array:', data.slots);
                this.slots = [];
            }
            
            // Обновляем состояние feed assist из статуса
            if (data.feed_assist_slot !== undefined) {
                this.feedAssistSlot = data.feed_assist_slot;
            } else if (data.feed_assist_count !== undefined && data.feed_assist_count > 0) {
                // Если feed_assist_slot не указан, но feed_assist_count > 0,
                // значит feed assist активен, но мы не знаем для какого слота
                // Оставляем текущее значение или пытаемся определить по другим признакам
                if (this.feedAssistSlot === -1) {
                    // Если не знаем, какой слот активен, но assist работает,
                    // можно попробовать определить по текущему инструменту
                    if (this.currentTool !== -1 && this.currentTool < 4) {
                        this.feedAssistSlot = this.currentTool;
                    }
                }
            } else {
                // Если feed_assist_count = 0, значит assist выключен
                this.feedAssistSlot = -1;
            }
            
            if (ACE_DASHBOARD_CONFIG?.debug) {
                console.log('Status updated:', {
                    deviceStatus: this.deviceStatus,
                    dryerStatus: this.dryerStatus,
                    slotsCount: this.slots.length,
                    feedAssistSlot: this.feedAssistSlot
                });
            }
        },
        
        async executeCommand(command, params = {}) {
            try {
                const response = await fetch(`${this.apiBase}/server/ace/command`, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({
                        command: command,
                        params: params
                    })
                });
                
                const result = await response.json();
                
                if (ACE_DASHBOARD_CONFIG?.debug) {
                    console.log('Command response:', result);
                }
                
                if (result.error) {
                    this.showNotification(`Ошибка API: ${result.error}`, 'error');
                    return false;
                }
                
                if (result.result) {
                    if (result.result.success !== false && !result.result.error) {
                        this.showNotification(`Команда ${command} выполнена успешно`, 'success');
                        // Reload status after command
                        setTimeout(() => this.loadStatus(), 1000);
                        return true;
                    } else {
                        const errorMsg = result.result.error || result.result.message || 'Неизвестная ошибка';
                        this.showNotification(`Ошибка: ${errorMsg}`, 'error');
                        return false;
                    }
                }
                
                // Если нет result, но и нет ошибки - считаем успехом
                this.showNotification(`Команда ${command} отправлена`, 'success');
                setTimeout(() => this.loadStatus(), 1000);
                return true;
            } catch (error) {
                console.error('Error executing command:', error);
                this.showNotification(`Ошибка выполнения команды: ${error.message}`, 'error');
                return false;
            }
        },
        
        // Device Actions
        async changeTool(tool) {
            const success = await this.executeCommand('ACE_CHANGE_TOOL', { TOOL: tool });
            if (success) {
                this.currentTool = tool;
            }
        },
        
        async unloadFilament() {
            await this.changeTool(-1);
        },
        
        async parkToToolhead(index) {
            await this.executeCommand('ACE_PARK_TO_TOOLHEAD', { INDEX: index });
        },
        
        // Feed Assist Actions
        async toggleFeedAssist(index) {
            if (this.feedAssistSlot === index) {
                // Выключаем feed assist для текущего слота
                await this.disableFeedAssist(index);
            } else {
                // Включаем feed assist для нового слота
                // Сначала выключаем предыдущий, если был активен
                if (this.feedAssistSlot !== -1) {
                    await this.disableFeedAssist(this.feedAssistSlot);
                }
                await this.enableFeedAssist(index);
            }
        },
        
        async enableFeedAssist(index) {
            const success = await this.executeCommand('ACE_ENABLE_FEED_ASSIST', { INDEX: index });
            if (success) {
                this.feedAssistSlot = index;
                this.showNotification(`Feed assist включен для слота ${index}`, 'success');
            }
        },
        
        async disableFeedAssist(index) {
            const success = await this.executeCommand('ACE_DISABLE_FEED_ASSIST', { INDEX: index });
            if (success) {
                this.feedAssistSlot = -1;
                this.showNotification(`Feed assist выключен для слота ${index}`, 'success');
            }
        },
        
        // Dryer Actions
        async startDrying() {
            if (this.dryingTemp < 20 || this.dryingTemp > 55) {
                this.showNotification('Температура должна быть от 20 до 55°C', 'error');
                return;
            }
            
            if (this.dryingDuration < 1) {
                this.showNotification('Длительность должна быть минимум 1 минута', 'error');
                return;
            }
            
            await this.executeCommand('ACE_START_DRYING', {
                TEMP: this.dryingTemp,
                DURATION: this.dryingDuration
            });
        },
        
        async stopDrying() {
            await this.executeCommand('ACE_STOP_DRYING');
        },
        
        // Feed/Retract Actions
        showFeedDialog(slot) {
            this.feedSlot = slot;
            this.feedLength = ACE_DASHBOARD_CONFIG?.defaults?.feedLength || 50;
            this.feedSpeed = ACE_DASHBOARD_CONFIG?.defaults?.feedSpeed || 25;
            this.showFeedModal = true;
        },
        
        closeFeedDialog() {
            this.showFeedModal = false;
        },
        
        async executeFeed() {
            if (this.feedLength < 1) {
                this.showNotification('Длина должна быть минимум 1 мм', 'error');
                return;
            }
            
            const success = await this.executeCommand('ACE_FEED', {
                INDEX: this.feedSlot,
                LENGTH: this.feedLength,
                SPEED: this.feedSpeed
            });
            
            if (success) {
                this.closeFeedDialog();
            }
        },
        
        showRetractDialog(slot) {
            this.retractSlot = slot;
            this.retractLength = ACE_DASHBOARD_CONFIG?.defaults?.retractLength || 50;
            this.retractSpeed = ACE_DASHBOARD_CONFIG?.defaults?.retractSpeed || 25;
            this.showRetractModal = true;
        },
        
        closeRetractDialog() {
            this.showRetractModal = false;
        },
        
        async executeRetract() {
            if (this.retractLength < 1) {
                this.showNotification('Длина должна быть минимум 1 мм', 'error');
                return;
            }
            
            const success = await this.executeCommand('ACE_RETRACT', {
                INDEX: this.retractSlot,
                LENGTH: this.retractLength,
                SPEED: this.retractSpeed
            });
            
            if (success) {
                this.closeRetractDialog();
            }
        },
        
        async refreshStatus() {
            await this.loadStatus();
            this.showNotification('Статус обновлен', 'success');
        },
        
        // Utility Functions
        getStatusText(status) {
            const statusMap = {
                'ready': 'Готов',
                'busy': 'Занят',
                'unknown': 'Неизвестно',
                'disconnected': 'Отключено'
            };
            return statusMap[status] || status;
        },
        
        getDryerStatusText(status) {
            const statusMap = {
                'stop': 'Остановлена',
                'drying': 'Сушка'
            };
            return statusMap[status] || status;
        },
        
        getSlotStatusText(status) {
            const statusMap = {
                'ready': 'Готов',
                'empty': 'Пуст',
                'busy': 'Занят'
            };
            return statusMap[status] || status;
        },
        
        getRfidStatusText(rfid) {
            const statusMap = {
                0: 'Не найдено',
                1: 'Ошибка',
                2: 'Идентифицировано',
                3: 'Идентификация...'
            };
            return statusMap[rfid] || 'Неизвестно';
        },
        
        getColorHex(color) {
            if (!color || !Array.isArray(color) || color.length < 3) {
                return '#000000';
            }
            const r = Math.max(0, Math.min(255, color[0])).toString(16).padStart(2, '0');
            const g = Math.max(0, Math.min(255, color[1])).toString(16).padStart(2, '0');
            const b = Math.max(0, Math.min(255, color[2])).toString(16).padStart(2, '0');
            return `#${r}${g}${b}`;
        },
        
        formatTime(minutes) {
            if (!minutes || minutes <= 0) return '0 мин';
            const hours = Math.floor(minutes / 60);
            const mins = minutes % 60;
            if (hours > 0) {
                return `${hours}ч ${mins}м`;
            }
            return `${mins} мин`;
        },
        
        showNotification(message, type = 'info') {
            this.notification = {
                show: true,
                message: message,
                type: type
            };
            
            setTimeout(() => {
                this.notification.show = false;
            }, 3000);
        }
    }
}).mount('#app');

