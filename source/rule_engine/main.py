import pika, json, psycopg2, requests, os, time

DB_CONFIG = os.getenv("DATABASE_URL", "host=aresguard_db dbname=aresguard user=ares password=mars2036")
BROKER_HOST = os.getenv("BROKER_HOST", "aresguard_broker")
RABBIT_USER = os.getenv("RABBITMQ_DEFAULT_USER", "ares")
RABBIT_PASS = os.getenv("RABBITMQ_DEFAULT_PASS", "mars2036")

# URL Fix anche qui
raw_url = os.getenv("SIMULATOR_URL", "http://mars_simulator:8080/api")
API_BASE = raw_url.replace("/sensors", "").replace("/actuators", "").rstrip("/")
ACTUATORS_URL = f"{API_BASE}/actuators"

# NUOVO NOME EXCHANGE
EXCHANGE_NAME = "ares_telemetry_stream"
QUEUE_NAME = "rule_engine_queue_v2" # Nome nuovo per pulizia

def check_condition(val, op, thresh):
    if op == '>': return val > thresh
    if op == '<': return val < thresh
    if op == '>=': return val >= thresh
    if op == '<=': return val <= thresh
    if op == '=': return val == thresh
    return False

def process_event(event):
    sid = event['source']['identifier']
    val = event['payload']['value']
    
    try:
        # DB Save
        conn = psycopg2.connect(DB_CONFIG)
        cur = conn.cursor()
        cur.execute("INSERT INTO public.sensor_data (sensor_id, value, unit, timestamp) VALUES (%s, %s, %s, %s)",
                    (sid, val, event['payload']['unit'], event['timestamp']))
        
        # Rule Check
        cur.execute("SELECT operator, threshold, actuator_id, action_value FROM rules WHERE sensor_id = %s", (sid,))
        rules = cur.fetchall()
        for op, thresh, act, act_val in rules:
            if check_condition(val, op, thresh):
                print(f"[RuleEngine] TRIGGER: {sid} -> {act}={act_val}")
                requests.post(f"{ACTUATORS_URL}/{act}", json={"state": act_val})
        
        conn.commit(); cur.close(); conn.close()
    except Exception as e: print(f"[RuleEngine] Error: {e}")

def main():
    creds = pika.PlainCredentials(RABBIT_USER, RABBIT_PASS)
    while True:
        try:
            conn = pika.BlockingConnection(pika.ConnectionParameters(host=BROKER_HOST, credentials=creds))
            ch = conn.channel()
            
            ch.exchange_declare(exchange=EXCHANGE_NAME, exchange_type='fanout')
            ch.queue_declare(queue=QUEUE_NAME, durable=True)
            ch.queue_bind(exchange=EXCHANGE_NAME, queue=QUEUE_NAME)
            
            ch.basic_consume(queue=QUEUE_NAME, on_message_callback=lambda ch, m, p, b: process_event(json.loads(b)), auto_ack=True)
            print("[RuleEngine] Active.")
            ch.start_consuming()
        except: time.sleep(5)

if __name__ == "__main__":
    main()