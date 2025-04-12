class FluiddAcePlugin {
    constructor() {
      this.deviceStatus = {};
      this.filamentSlots = [];
      this.pollInterval = null;
    }
  
    // Инициализация плагина
    init() {
      console.log("Initializing Fluidd ACE Plugin...");
  
      // Создаем элементы интерфейса
      this.createUI();
  
      // Загружаем данные при загрузке страницы
      this.loadData();
  
      // Устанавливаем интервал для периодического обновления данных
      this.pollInterval = setInterval(() => this.loadData(), 5000); // Обновление каждые 5 секунд
    }
  
    // Создание пользовательского интерфейса
    createUI() {
      const panel = document.createElement("div");
      panel.id = "ace-plugin-panel";
      panel.innerHTML = `
        <h3>ACE Device Status</h3>
        <div id="ace-status"></div>
        <h3>Filament Slots</h3>
        <div id="ace-filament-slots"></div>
      `;
      document.querySelector(".content").appendChild(panel);
    }
  
    // Загрузка данных с принтера
    loadData() {
      this.getDeviceStatus();
      this.getFilamentInfo();
    }
  
    // Получение статуса устройства
    getDeviceStatus() {
      fetch("/printer/gcode/script", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ script: "ACE_STATUS" }),
      })
        .then((response) => response.json())
        .then((data) => {
          if (data && data.result) {
            this.deviceStatus = JSON.parse(data.result);
            this.updateStatusUI();
          }
        })
        .catch((error) => console.error("Error fetching ACE status:", error));
    }
  
    // Получение информации о филаментах
    getFilamentInfo() {
      const slots = [];
      for (let i = 0; i < 4; i++) {
        fetch("/printer/gcode/script", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ script: `ACE_FILAMENT_INFO INDEX=${i}` }),
        })
          .then((response) => response.json())
          .then((data) => {
            if (data && data.result) {
              slots.push(JSON.parse(data.result));
              if (slots.length === 4) {
                this.filamentSlots = slots;
                this.updateFilamentUI();
              }
            }
          })
          .catch((error) => console.error(`Error fetching filament info for slot ${i}:`, error));
      }
    }
  
    // Обновление UI для статуса устройства
    updateStatusUI() {
      const statusDiv = document.getElementById("ace-status");
      statusDiv.innerHTML = `
        <p>Status: ${this.deviceStatus.status || "Unknown"}</p>
        <p>Temperature: ${this.deviceStatus.temp || "N/A"}°C</p>
        <p>Fan Speed: ${this.deviceStatus.fan_speed || "N/A"}</p>
      `;
    }
  
    // Обновление UI для слотов филамента
    updateFilamentUI() {
      const slotsDiv = document.getElementById("ace-filament-slots");
      slotsDiv.innerHTML = this.filamentSlots
        .map(
          (slot, index) => `
          <div class="filament-slot">
            <h4>Slot ${index}</h4>
            <p>Status: ${slot.status || "Unknown"}</p>
            <p>Type: ${slot.type || "N/A"}</p>
            <p>Color: ${slot.color ? `RGB(${slot.color.join(", ")})` : "N/A"}</p>
            <p>RFID: ${slot.rfid || "N/A"}</p>
          </div>
        `
        )
        .join("");
    }
  
    // Очистка ресурсов при выгрузке плагина
    destroy() {
      if (this.pollInterval) {
        clearInterval(this.pollInterval);
      }
      document.querySelector("#ace-plugin-panel")?.remove();
    }
  }
  
  // Экспорт плагина
  window.FluiddAcePlugin = new FluiddAcePlugin();
  window.FluiddAcePlugin.init();