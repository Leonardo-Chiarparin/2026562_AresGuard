/**
 * ARESGUARD MISSION CONTROL - DASHBOARD LOGIC
 * File: source/frontend/js/script.js
 */

const ENDPOINTS = {
    WS: "ws://localhost:8000/ws",
    API: "http://localhost:8000/api/commands"
};

// --- REGISTRO SENSORI (Mapping 1:1 con Ingestion Service) ---
const SENSORS_REGISTRY = [
    // Riga 1: Parametri Ambientali Base
    { id: 'greenhouse_temperature', label: 'Greenhouse Temp', unit: '°C' },
    { id: 'entrance_humidity', label: 'Entrance Humidity', unit: '%' },
    { id: 'co2_hall', label: 'CO2 Hall Level', unit: 'ppm' },
    { id: 'corridor_pressure', label: 'Corridor Pressure', unit: 'hPa' },
    
    // Riga 2: Risorse & Chimica
    { id: 'water_tank_level', label: 'Water Tank Level', unit: '%' },
    { id: 'water_tank_liters', label: 'Water Tank Vol', unit: 'L' },
    { id: 'hydroponic_ph', label: 'Hydroponic pH', unit: 'pH' },
    { id: 'air_quality_pm25', label: 'PM 2.5 Level', unit: 'µg' },
    
    // Riga 3: Qualità dell'Aria Avanzata
    { id: 'air_quality_pm1', label: 'PM 1.0 Level', unit: 'µg' },
    { id: 'air_quality_pm10', label: 'PM 10 Level', unit: 'µg' },
    { id: 'air_quality_voc', label: 'Volatile Org. Comp', unit: 'ppb' },
    { id: 'air_quality_co2e', label: 'CO2 Equivalent', unit: 'ppm' }
];

let actuatorStates = {};

/**
 * Inizializzazione della Dashboard
 * Genera le card HTML in base al registro sensori.
 */
function initMissionControl() {
    const grid = document.getElementById('sensor-grid');
    if (!grid) {
        console.error("Errore: Elemento #sensor-grid non trovato nell'HTML.");
        return;
    }
    
    // Generazione dinamica delle 12 card
    grid.innerHTML = SENSORS_REGISTRY.map(s => `
        <div class="sensor-card" id="card-${s.id}">
            <span class="status-label status-ok" id="status-text-${s.id}">● Status: Normal</span>
            <div class="sensor-value-group">
                <h2 id="val-${s.id}">--.-</h2>
                <span class="unit">${s.unit}</span>
            </div>
            <p class="sensor-label">${s.label}</p>
        </div>
    `).join('');
    
    addLog("System Interface Loaded. Initializing Uplink...", "#3b82f6");
    connect();
}

/**
 * Gestione WebSocket
 * Si connette al Gateway e ascolta i dati in tempo reale.
 */
function connect() {
    const socket = new WebSocket(ENDPOINTS.WS);

    socket.onopen = () => {
        setOnlineStatus(true);
    };

    socket.onmessage = (event) => {
        setOnlineStatus(true);
        try {
            const message = JSON.parse(event.data);

            // Ignora i messaggi di heartbeat/ping
            if (message.type === "PING") return;

            // Il payload può essere dentro 'data' (formato gateway) o alla radice
            const telemetry = message.data || message;

            // Itera su tutte le chiavi ricevute (es. greenhouse_temperature, co2_hall...)
            Object.keys(telemetry).forEach(key => {
                const entry = telemetry[key];
                
                // Estrazione sicura del valore (Unified Schema: entry.payload.value)
                let value = null;
                if (entry && entry.payload && entry.payload.value !== undefined) {
                    value = entry.payload.value;
                } else if (entry && entry.value !== undefined) {
                    value = entry.value;
                } else if (typeof entry === 'number') {
                    value = entry;
                }

                if (value !== null) {
                    updateUI(key, value);
                }
            });

        } catch (err) {
            console.warn("Errore parsing WebSocket:", err);
        }
    };

    socket.onclose = () => {
        setOnlineStatus(false);
        // Tenta la riconnessione dopo 3 secondi
        setTimeout(connect, 3000);
    };
    
    socket.onerror = (err) => {
        console.error("WebSocket Error:", err);
        socket.close();
    };
}

/**
 * Aggiorna la UI per un singolo sensore
 */
function updateUI(id, value) {
    // Cerca se l'ID ricevuto esiste nel nostro registro
    const sensor = SENSORS_REGISTRY.find(s => s.id === id);
    
    if (sensor) {
        const valueElement = document.getElementById(`val-${sensor.id}`);
        if (valueElement) {
            // Formattazione: 1 decimale per i numeri
            valueElement.innerText = (typeof value === 'number') ? value.toFixed(1) : value;
            
            // Controlla se il valore è critico
            evaluateSafetyThresholds(sensor.id, value);
        }
    }
}

/**
 * Logica Soglie di Sicurezza & Allarmi
 */
function evaluateSafetyThresholds(id, val) {
    const card = document.getElementById(`card-${id}`);
    const statusText = document.getElementById(`status-text-${id}`);
    const banner = document.getElementById('critical-banner');
    
    if (!card || !statusText) return;

    let isCritical = false;

    // --- REGOLE DI ALLARME ---
    if (id.includes('temperature') && val > 30.0) isCritical = true; // Caldo eccessivo
    if (id.includes('humidity') && val < 20.0) isCritical = true;    // Troppo secco
    if (id.includes('co2') && val > 1000) isCritical = true;         // Aria viziata
    if (id === 'water_tank_level' && val < 15.0) isCritical = true;  // Acqua bassa
    if (id.includes('voc') && val > 500) isCritical = true;          // Contaminanti

    // Applicazione stili
    if (isCritical) {
        if (!card.classList.contains('card-alert')) {
            card.classList.add('card-alert');
            statusText.className = "status-label status-crit";
            statusText.innerText = "● Status: WARNING";
        }
        if (banner) banner.style.display = 'block';
    } else {
        if (card.classList.contains('card-alert')) {
            card.classList.remove('card-alert');
            statusText.className = "status-label status-ok";
            statusText.innerText = "● Status: Normal";
        }
    }

    // Gestione Banner Globale: nascondilo solo se NESSUNA card è in allarme
    if (banner) {
        const anyAlert = document.querySelector('.card-alert');
        if (!anyAlert) banner.style.display = 'none';
    }
}

/**
 * Gestione Stato Connessione (UI Header)
 */
function setOnlineStatus(isOnline) {
    const statusEl = document.getElementById('conn-status');
    if (statusEl) {
        if (isOnline) {
            if (statusEl.innerText !== "ONLINE") {
                statusEl.innerText = "ONLINE";
                statusEl.style.color = "#22c55e"; // Verde
                addLog("Data Stream Active.", "#22c55e");
            }
        } else {
            statusEl.innerText = "OFFLINE";
            statusEl.style.color = "#ef4444"; // Rosso
        }
    }
}

/**
 * Invio Comandi Attuatori (API)
 */
async function toggleActuator(id, isChecked) {
    const state = isChecked ? "ON" : "OFF";
    // Evita chiamate duplicate
    if (actuatorStates[id] === state) return;

    addLog(`Sending CMD: ${id} -> ${state}...`, "#fbbf24"); // Giallo attesa

    try {
        const res = await fetch(`${ENDPOINTS.API}/${id}`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ state: state })
        });

        if (res.ok) {
            actuatorStates[id] = state;
            addLog(`ACK: ${id} set to ${state}`, "#3b82f6"); // Blu conferma
        } else {
            throw new Error(`HTTP ${res.status}`);
        }
    } catch (e) {
        addLog(`ERR: Cmd failed (${id})`, "#ef4444");
        // Revert visivo dello switch se il comando fallisce (opzionale)
        // document.querySelector(`input[onchange*="${id}"]`).checked = !isChecked;
    }
}

/**
 * Utility: Scrittura Log nel Terminale
 */
function addLog(msg, color) {
    const log = document.getElementById('log-console');
    if (!log) return;
    
    const entry = document.createElement('div');
    entry.style.color = color || "#4ade80";
    // Timestamp + Messaggio
    entry.innerHTML = `<span>[${new Date().toLocaleTimeString()}]</span> > ${msg}`;
    
    // Inserisce in cima (i nuovi messaggi spingono giù i vecchi)
    log.prepend(entry);
}

// Avvio applicazione al caricamento pagina
window.onload = initMissionControl;