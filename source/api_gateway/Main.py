import json
import threading
import os
import requests
import pika
import asyncio
import time
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

# --- CONFIGURAZIONE ---
BROKER_HOST = os.getenv("BROKER_HOST", "aresguard_broker")
BROKER_USER = os.getenv("RABBITMQ_DEFAULT_USER", "ares")
BROKER_PASS = os.getenv("RABBITMQ_DEFAULT_PASS", "mars2036")
SIMULATOR_URL = os.getenv("SIMULATOR_URL", "http://mars_simulator:8080/api/actuators")

# Usiamo lo stesso Exchange definito nell'Ingestion Service
EXCHANGE_NAME = "sensor_broadcast"

app = FastAPI(title="AresGuard API Gateway")

# Configurazione CORS per permettere al frontend di connettersi
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Stato globale in memoria (cache dell'ultimo valore ricevuto)
current_state = {} 

# Riferimento al loop asincrono principale (per il thread-safe call)
manager_loop = None

# --- GESTORE WEBSOCKET ---
class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        # Invia subito lo stato attuale appena uno si connette
        await websocket.send_json({"type": "FULL_STATE", "data": current_state})
        print(f"[Gateway] Client connected. Active: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
            print(f"[Gateway] Client disconnected. Active: {len(self.active_connections)}")

    async def broadcast(self, message: dict):
        # Itera su una copia della lista per evitare errori durante la rimozione
        for connection in self.active_connections[:]:
            try:
                await connection.send_json(message)
            except Exception:
                self.disconnect(connection)

manager = ConnectionManager()

# --- CONSUMER RABBITMQ (Thread Separato) ---
def start_consumer(loop):
    """
    Si connette a RabbitMQ in modalità Fanout.
    Crea una coda temporanea esclusiva per ricevere una COPIA di tutti i messaggi.
    """
    credentials = pika.PlainCredentials(BROKER_USER, BROKER_PASS)
    
    while True:
        try:
            connection = pika.BlockingConnection(
                pika.ConnectionParameters(host=BROKER_HOST, credentials=credentials, heartbeat=60)
            )
            channel = connection.channel()

            # 1. Dichiara l'Exchange (deve corrispondere a quello dell'Ingestion)
            channel.exchange_declare(exchange=EXCHANGE_NAME, exchange_type='fanout')

            # 2. Crea una coda TEMPORANEA ed ESCLUSIVA
            # Lasciando queue='' RabbitMQ genera un nome random.
            # exclusive=True cancella la coda quando il Gateway si disconnette.
            result = channel.queue_declare(queue='', exclusive=True)
            queue_name = result.method.queue

            # 3. Collega la coda all'Exchange per ricevere tutto
            channel.queue_bind(exchange=EXCHANGE_NAME, queue=queue_name)

            print(f"[Gateway] Connected to Exchange '{EXCHANGE_NAME}' via queue '{queue_name}'")

            def callback(ch, method, properties, body):
                try:
                    event = json.loads(body)
                    sensor_id = event['source']['identifier']
                    
                    # Aggiorna la cache in memoria
                    current_state[sensor_id] = event
                    
                    # Invia al WebSocket in modo thread-safe (Immediato!)
                    asyncio.run_coroutine_threadsafe(
                        manager.broadcast({"type": "UPDATE", "data": {sensor_id: event}}), 
                        loop
                    )
                except Exception as e:
                    print(f"[Gateway] Error processing message: {e}")

            channel.basic_consume(queue=queue_name, on_message_callback=callback, auto_ack=True)
            channel.start_consuming()

        except Exception as e:
            print(f"[Gateway] Broker Connection Error: {e}. Retrying in 5s...")
            time.sleep(5)

# --- EVENTI DI AVVIO ---
@app.on_event("startup")
async def startup_event():
    global manager_loop
    manager_loop = asyncio.get_running_loop()
    # Avvia il consumer in un thread separato daemon
    threading.Thread(target=start_consumer, args=(manager_loop,), daemon=True).start()

# --- ENDPOINT API ---

@app.get("/")
def read_root():
    return {"status": "online", "service": "AresGuard Gateway"}

@app.get("/api/state")
def get_state():
    """Restituisce l'ultimo stato noto di tutti i sensori."""
    return current_state

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            # Keep-alive ping per evitare disconnessioni per inattività
            await asyncio.sleep(30)
            await websocket.send_json({"type": "PING"})
    except WebSocketDisconnect:
        manager.disconnect(websocket)

@app.post("/api/commands/{actuator_id}")
def send_command(actuator_id: str, command: dict):
    """Inoltra i comandi manuali al simulatore."""
    try:
        # Timeout breve per non bloccare la UI
        res = requests.post(f"{SIMULATOR_URL}/{actuator_id}", json=command, timeout=3)
        return {"status": "success", "code": res.status_code}
    except Exception as e:
        return {"status": "error", "message": str(e)}