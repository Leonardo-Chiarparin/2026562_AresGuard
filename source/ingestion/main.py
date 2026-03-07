import time, json, os, uuid, datetime, requests, pika

SIMULATOR_URL = os.getenv("SIMULATOR_URL", "http://mars_simulator:8080/api/sensors")
BROKER_HOST = os.getenv("BROKER_HOST", "aresguard_broker")
BROKER_USER = os.getenv("RABBITMQ_DEFAULT_USER", "ares")
BROKER_PASS = os.getenv("RABBITMQ_DEFAULT_PASS", "mars2036")

# CAMBIAMENTO: Usiamo un Exchange Fanout
EXCHANGE_NAME = "sensor_broadcast"

def get_rabbitmq_connection():
    credentials = pika.PlainCredentials(BROKER_USER, BROKER_PASS)
    while True:
        try:
            connection = pika.BlockingConnection(pika.ConnectionParameters(host=BROKER_HOST, credentials=credentials))
            return connection
        except: time.sleep(5)

def build_event(sid, val, unit):
    return {
        "event_id": str(uuid.uuid4()),
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "source": { "identifier": sid, "protocol": "rest_polling" },
        "payload": { "value": val, "unit": unit, "category": "telemetry" }
    }

def process_data(sid, data):
    events = []
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
        val = data.get('value')
        if val is None:
            for k in ['temperature', 'humidity', 'pressure', 'co2_level']:
                if k in data: val = data[k]; break
        events.append(build_event(sid, val, data.get('unit', '')))
    return events

def main():
    conn = get_rabbitmq_connection()
    ch = conn.channel()
    
    # DICHIARIAMO L'EXCHANGE FANOUT
    ch.exchange_declare(exchange=EXCHANGE_NAME, exchange_type='fanout')
    
    while True:
        try:
            r = requests.get(SIMULATOR_URL, timeout=5)
            sensors = r.json().get("sensors", [])
            for sid in sensors:
                rd = requests.get(f"{SIMULATOR_URL}/{sid}", timeout=5).json()
                evs = process_data(sid, rd)
                for e in evs:
                    # PUBBLICHIAMO SULL'EXCHANGE (routing_key vuota per fanout)
                    ch.basic_publish(exchange=EXCHANGE_NAME, routing_key='', body=json.dumps(e))
                    print(f"[INGESTION] Broadcast: {e['source']['identifier']}")
        except Exception as e: print(f"Error: {e}")
        time.sleep(5)

if __name__ == "__main__":
    main()