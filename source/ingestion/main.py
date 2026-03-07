import time, json, os, uuid, datetime, requests, pika

# --- CONFIGURAZIONE ROBUSTA ---
# Pulisce l'URL se contiene per sbaglio "/sensors" o slash finali
raw_url = os.getenv("SIMULATOR_URL", "http://mars_simulator:8080/api")
base_url = raw_url.replace("/sensors", "").replace("/actuators", "").rstrip("/")

SENSORS_URL = f"{base_url}/sensors"
ACTUATORS_URL = f"{base_url}/actuators"

BROKER_HOST = os.getenv("BROKER_HOST", "aresguard_broker")
BROKER_USER = os.getenv("RABBITMQ_DEFAULT_USER", "ares")
BROKER_PASS = os.getenv("RABBITMQ_DEFAULT_PASS", "mars2036")

# NUOVO NOME per evitare conflitti con vecchi test
EXCHANGE_NAME = "ares_telemetry_stream"

def get_rabbitmq_connection():
    credentials = pika.PlainCredentials(BROKER_USER, BROKER_PASS)
    while True:
        try:
            return pika.BlockingConnection(pika.ConnectionParameters(host=BROKER_HOST, credentials=credentials))
        except: time.sleep(5)

def build_event(sid, val, unit=""):
    return {
        "event_id": str(uuid.uuid4()),
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "source": { "identifier": sid, "protocol": "rest_polling" },
        "payload": { "value": val, "unit": unit, "category": "telemetry" }
    }

def process_sensor_data(sid, data):
    events = []
    # Logica specifica per sensori complessi
    if 'level_pct' in data:
        events.append(build_event('water_tank_level', data.get('level_pct'), '%'))
        events.append(build_event('water_tank_liters', data.get('level_liters'), 'L'))
    elif 'pm25_ug_m3' in data:
        events.append(build_event('air_quality_pm1', data.get('pm1_ug_m3'), 'µg/m³'))
        events.append(build_event('air_quality_pm25', data.get('pm25_ug_m3'), 'µg/m³'))
        events.append(build_event('air_quality_pm10', data.get('pm10_ug_m3'), 'µg/m³'))
    elif 'measurements' in data and isinstance(data['measurements'], list):
        for m in data['measurements']:
            metric = m.get('metric', '')
            val = m.get('value')
            target = 'air_quality_voc' if 'voc' in metric else ('air_quality_co2e' if 'co2e' in metric else ('hydroponic_ph' if 'ph' in metric else f"{sid}_{metric}"))
            events.append(build_event(target, val, m.get('unit', '')))
    else:
        # Fallback per sensori semplici
        val = data.get('value')
        if val is None:
            for k in ['temperature', 'humidity', 'pressure', 'co2_level']:
                if k in data: val = data[k]; break
        events.append(build_event(sid, val, data.get('unit', '')))
    return events

def fetch_and_process_actuators(channel):
    try:
        r = requests.get(ACTUATORS_URL, timeout=3)
        if r.status_code == 200:
            actuators = r.json()
            # Gestione sia lista ID che dizionario completo
            if isinstance(actuators, list):
                for aid in actuators:
                    try:
                        ar = requests.get(f"{ACTUATORS_URL}/{aid}", timeout=2).json()
                        state = ar.get('state', 'OFF')
                        event = build_event(aid, state, "")
                        channel.basic_publish(exchange=EXCHANGE_NAME, routing_key='', body=json.dumps(event))
                    except: pass
            elif isinstance(actuators, dict):
                for aid, data in actuators.items():
                    state = data.get('state', 'OFF')
                    event = build_event(aid, state, "")
                    channel.basic_publish(exchange=EXCHANGE_NAME, routing_key='', body=json.dumps(event))
    except Exception as e:
        print(f"[INGESTION] Actuator Poll Error: {e}")

def main():
    print(f"[INGESTION] Starting... Target: {SENSORS_URL}")
    conn = get_rabbitmq_connection()
    ch = conn.channel()
    ch.exchange_declare(exchange=EXCHANGE_NAME, exchange_type='fanout')
    
    while True:
        try:
            # 1. Sensors
            r = requests.get(SENSORS_URL, timeout=5)
            if r.status_code == 200:
                sensors = r.json().get("sensors", [])
                print(f"[INGESTION] Processing {len(sensors)} sensors...")
                for sid in sensors:
                    try:
                        rd = requests.get(f"{SENSORS_URL}/{sid}", timeout=5).json()
                        evs = process_sensor_data(sid, rd)
                        for e in evs:
                            ch.basic_publish(exchange=EXCHANGE_NAME, routing_key='', body=json.dumps(e))
                    except: pass
            
            # 2. Actuators
            fetch_and_process_actuators(ch)
            
        except Exception as e: 
            print(f"[INGESTION] Critical Loop Error: {e}")
        time.sleep(2)

if __name__ == "__main__":
    main()