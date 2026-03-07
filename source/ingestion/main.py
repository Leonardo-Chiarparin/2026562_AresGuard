import time
import json
import os
import uuid
import datetime
import requests
import pika

# --- CONFIGURATION ---
# Hostnames match the 'container_name' defined in docker-compose.yml
SIMULATOR_URL = os.getenv("SIMULATOR_URL", "http://mars_simulator:8080/api/sensors")
BROKER_HOST = os.getenv("BROKER_HOST", "aresguard_broker")
BROKER_USER = os.getenv("RABBITMQ_DEFAULT_USER", "ares")
BROKER_PASS = os.getenv("RABBITMQ_DEFAULT_PASS", "mars2036")
QUEUE_NAME = "sensor_events"
POLLING_INTERVAL = 5  # Seconds

def get_rabbitmq_connection():
    """Handles RabbitMQ connection with a retry loop to ensure startup resilience."""
    credentials = pika.PlainCredentials(BROKER_USER, BROKER_PASS)
    parameters = pika.ConnectionParameters(host=BROKER_HOST, credentials=credentials)
    
    while True:
        try:
            connection = pika.BlockingConnection(parameters)
            print(f"[INGESTION] Successfully connected to RabbitMQ at {BROKER_HOST}")
            return connection
        except pika.exceptions.AMQPConnectionError:
            print("[INGESTION] RabbitMQ not ready yet. Retrying in 5s...")
            time.sleep(5)

def build_event_schema(sensor_id, value, unit):
    """Constructs the exact JSON structure defined in the Event Schema of input.md."""
    return {
        "event_id": str(uuid.uuid4()),
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "source": {
            "identifier": sensor_id,
            "protocol": "rest_polling"
        },
        "payload": {
            "value": value,
            "unit": unit,
            "category": "telemetry"
        },
        "metadata": {
            "version": "1.0",
            "tags": ["polling", "normalized"]
        }
    }

def process_raw_data(sensor_id, raw_data):
    """
    Unpacks data based on type (scalar vs chemistry) as required by input.md
    and returns a list of normalized events.
    """
    events = []
    
    # CASE 1: Chemistry sensor with a 'measurements' array (Unpacking rule applied)
    if 'measurements' in raw_data and isinstance(raw_data['measurements'], list):
        for measurement in raw_data['measurements']:
            val = measurement.get('value')
            un = measurement.get('unit', '')
            metric_name = measurement.get('name', '')
            
            # Create a specific ID if multiple metrics exist within the same sensor
            specific_id = f"{sensor_id}_{metric_name}" if metric_name else sensor_id
            events.append(build_event_schema(specific_id, val, un))
            
    # CASE 2: Standard REST sensor (scalar, level, etc.)
    else:
        # Extract value based on the possible keys used by the simulator
        val = raw_data.get('value') or raw_data.get('level') or raw_data.get('concentration') or raw_data.get('ph')
        if val is None:
            val = 0 # Safety fallback
        un = raw_data.get('unit', '')
        
        events.append(build_event_schema(sensor_id, val, un))
        
    return events

def fetch_sensor_list():
    """Fetches the list of available sensor IDs from the simulator."""
    try:
        response = requests.get(SIMULATOR_URL, timeout=3)
        if response.status_code == 200:
            data = response.json()
            # The simulator returns {"sensors": ["id1", "id2", ...]}
            # We safely extract the list using .get()
            return data.get("sensors", [])
        else:
            print(f"[INGESTION] Simulator error fetching sensor list: HTTP {response.status_code}")
            return None
    except requests.exceptions.RequestException as e:
        print(f"[INGESTION] Cannot contact simulator for sensor list ({e}).")
        return None

def fetch_sensor_data(sensor_id):
    """Fetches raw JSON data for a specific sensor."""
    try:
        response = requests.get(f"{SIMULATOR_URL}/{sensor_id}", timeout=2)
        if response.status_code == 200:
            return response.json()
        return None
    except requests.exceptions.RequestException as e:
        print(f"[INGESTION] Cannot fetch data for {sensor_id} ({e}).")
        return None

def main():
    print("[INGESTION] Starting AresGuard Ingestion Service...")
    
    # 1. Connect to the Message Broker
    connection = get_rabbitmq_connection()
    channel = connection.channel()
    
    # Declare the queue to ensure it exists before publishing
    channel.queue_declare(queue=QUEUE_NAME, durable=True)

    # 2. Infinite Polling Loop
    try:
        while True:
            # A. Get the list of available sensors
            sensors_list = fetch_sensor_list()
            
            if sensors_list and isinstance(sensors_list, list):
                
                # B. Fetch data for each sensor individually
                for sensor_id in sensors_list:
                    raw_data = fetch_sensor_data(sensor_id)
                    
                    if raw_data:
                        # C. Normalize the raw data (handles array unpacking)
                        normalized_events = process_raw_data(sensor_id, raw_data)
                        
                        # D. Publish each normalized event to the queue
                        for event in normalized_events:
                            body = json.dumps(event)
                            
                            channel.basic_publish(
                                exchange='',
                                routing_key=QUEUE_NAME,
                                body=body,
                                properties=pika.BasicProperties(
                                    delivery_mode=2,  # Makes the message persistent across broker restarts
                                )
                            )
                            print(f"[INGESTION] Normalized & Published: {event['source']['identifier']} -> {event['payload']['value']} {event['payload']['unit']}")
            else:
                print("[INGESTION] No sensors found or simulator is currently unreachable.")
            
            # E. Wait before initiating the next polling cycle
            time.sleep(POLLING_INTERVAL)

    except KeyboardInterrupt:
        print("\n[INGESTION] Manual stop requested. Shutting down gracefully...")
        connection.close()
    except Exception as e:
        print(f"[INGESTION] Critical error encountered: {e}")
        if not connection.is_closed:
            connection.close()

if __name__ == "__main__":
    main()