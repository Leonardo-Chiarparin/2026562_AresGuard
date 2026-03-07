import json, threading, os, requests, pika, asyncio, time
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

# --- CONFIGURAZIONE ---
BROKER_HOST = os.getenv("BROKER_HOST", "aresguard_broker")
BROKER_USER = os.getenv("RABBITMQ_DEFAULT_USER", "ares")
BROKER_PASS = os.getenv("RABBITMQ_DEFAULT_PASS", "mars2036")

# Fix URL: punta direttamente alla root API per ricostruire i path corretti
raw_url = os.getenv("SIMULATOR_URL", "http://mars_simulator:8080/api")
API_BASE = raw_url.replace("/sensors", "").replace("/actuators", "").rstrip("/")
ACTUATORS_URL = f"{API_BASE}/actuators"

# NUOVO NOME EXCHANGE
EXCHANGE_NAME = "ares_telemetry_stream"

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

current_state = {} 
manager_loop = None

class ConnectionManager:
    def __init__(self): self.active_connections = []
    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        await websocket.send_json({"type": "FULL_STATE", "data": current_state})
    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections: self.active_connections.remove(websocket)
    async def broadcast(self, message: dict):
        for connection in self.active_connections[:]:
            try: await connection.send_json(message)
            except: self.disconnect(connection)

manager = ConnectionManager()

def start_consumer(loop):
    credentials = pika.PlainCredentials(BROKER_USER, BROKER_PASS)
    while True:
        try:
            connection = pika.BlockingConnection(pika.ConnectionParameters(host=BROKER_HOST, credentials=credentials, heartbeat=60))
            channel = connection.channel()
            
            # Fanout Setup
            channel.exchange_declare(exchange=EXCHANGE_NAME, exchange_type='fanout')
            result = channel.queue_declare(queue='', exclusive=True)
            queue_name = result.method.queue
            channel.queue_bind(exchange=EXCHANGE_NAME, queue=queue_name)
            
            print(f"[Gateway] Connected to {EXCHANGE_NAME}")

            def callback(ch, method, properties, body):
                try:
                    event = json.loads(body)
                    sid = event['source']['identifier']
                    current_state[sid] = event
                    asyncio.run_coroutine_threadsafe(manager.broadcast({"type": "UPDATE", "data": {sid: event}}), loop)
                except Exception as e: print(f"GW Error: {e}")

            channel.basic_consume(queue=queue_name, on_message_callback=callback, auto_ack=True)
            channel.start_consuming()
        except Exception as e:
            print(f"[Gateway] Reconnecting: {e}")
            time.sleep(5)

@app.on_event("startup")
async def startup_event():
    global manager_loop
    manager_loop = asyncio.get_running_loop()
    threading.Thread(target=start_consumer, args=(manager_loop,), daemon=True).start()

@app.get("/api/state")
def get_state(): return current_state

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True: await asyncio.sleep(30); await websocket.send_json({"type": "PING"})
    except WebSocketDisconnect: manager.disconnect(websocket)

@app.post("/api/commands/{actuator_id}")
def send_command(actuator_id: str, command: dict):
    # Usa l'URL corretto calcolato all'inizio
    try:
        requests.post(f"{ACTUATORS_URL}/{actuator_id}", json=command, timeout=3)
        return {"status": "success"}
    except Exception as e: return {"status": "error", "message": str(e)}