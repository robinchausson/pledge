import asyncio
from collections import defaultdict, deque
from dataclasses import dataclass, field
import json
import logging
import logging.handlers
import os
from pathlib import Path
import shutil
import ssl
import struct
import threading
import time
import uuid

import paho.mqtt.client as mqtt
import psutil
import websockets


BROKER_IP = os.getenv("BROKER_IP", "127.0.0.1")
BROKER_PORT = int(os.getenv("BROKER_PORT", "1883"))
BROKER_KEEPALIVE = int(os.getenv("BROKER_KEEPALIVE", "30"))
MQTT_USERNAME = os.getenv("MQTT_USERNAME")
MQTT_PASSWORD = os.getenv("MQTT_PASSWORD")
NODE_ID = os.getenv("NODE_ID", "PC_Julian")
ZONE_ID = os.getenv("ZONE_ID", "Zone_1")
PORT_WSS = int(os.getenv("PORT_WSS", "8444"))
NODE_IP = os.getenv("NODE_IP", "127.0.0.1")
STORAGE_ROOT = Path(os.getenv("STORAGE_ROOT", "/donnees_isolees"))
QUEUE_SIZE = int(os.getenv("STREAM_QUEUE_SIZE", "128"))
STORAGE_QUEUE_SIZE = int(os.getenv("STORAGE_QUEUE_SIZE", str(QUEUE_SIZE * 4)))
STORAGE_BATCH_SIZE = int(os.getenv("STORAGE_BATCH_SIZE", "8"))
STORAGE_FLUSH_SECONDS = float(os.getenv("STORAGE_FLUSH_SECONDS", "0.25"))
MAX_MESSAGE_SIZE = int(os.getenv("MAX_MESSAGE_SIZE", str(4 * 1024 * 1024)))
STORAGE_SEGMENT_BYTES = int(os.getenv("STORAGE_SEGMENT_BYTES", str(64 * 1024 * 1024)))
STORAGE_SEGMENT_SECONDS = float(os.getenv("STORAGE_SEGMENT_SECONDS", "2"))
TELEMETRY_INTERVAL = float(os.getenv("TELEMETRY_INTERVAL", "10"))
UPLOAD_ACK_EVERY = int(os.getenv("UPLOAD_ACK_EVERY", "0"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()


class JsonFormatter(logging.Formatter):
    def format(self, record):
        payload = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(record.created)),
            "level": record.levelname,
            "event": record.getMessage(),
            "node_id": NODE_ID,
        }
        for key in ("client_id", "stream_id", "sequence", "direction", "error"):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        return json.dumps(payload, separators=(",", ":"))


logger = logging.getLogger("edge")
handler = logging.StreamHandler()
handler.setFormatter(JsonFormatter())
logger.addHandler(handler)
logger.setLevel(LOG_LEVEL)
logger.propagate = False

metrics_lock = threading.Lock()
metrics = defaultdict(lambda: defaultdict(int))


def metric(client_id, name, value=1):
    with metrics_lock:
        metrics[client_id][name] += value


def metric_snapshot():
    with metrics_lock:
        return {client_id: dict(values) for client_id, values in metrics.items()}


def safe_client_id(client_id):
    if not client_id or client_id in {".", ".."} or Path(client_id).name != client_id:
        raise ValueError("client_id invalide")
    return client_id


def write_frame(path, sequence, timestamp_ns, data):
    # Length-prefix each frame so a segment can be replayed without one file per packet.
    header = struct.pack("!QQI", sequence, timestamp_ns, len(data))
    with path.open("ab") as output:
        output.write(header)
        output.write(data)


@dataclass
class StreamState:
    client_id: str
    subscribers: dict = field(default_factory=dict)
    recorder_queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=STORAGE_QUEUE_SIZE))
    history: deque = field(default_factory=lambda: deque(maxlen=QUEUE_SIZE))
    sequence: int = 0
    segment_number: int = 0
    segment_bytes: int = 0
    segment_started: float = field(default_factory=time.monotonic)
    last_activity: float = field(default_factory=time.monotonic)
    upload_active: bool = False
    recorder_task: asyncio.Task | None = None

    def subscribe(self):
        subscriber_id = uuid.uuid4().hex
        queue = asyncio.Queue(maxsize=QUEUE_SIZE)
        for packet in self.history:
            try:
                queue.put_nowait(packet)
            except asyncio.QueueFull:
                break
        self.subscribers[subscriber_id] = queue
        self.last_activity = time.monotonic()
        return subscriber_id, queue

    def unsubscribe(self, subscriber_id):
        self.subscribers.pop(subscriber_id, None)
        self.last_activity = time.monotonic()

    def publish(self, data):
        timestamp_ns = time.time_ns()
        packet = (self.sequence, timestamp_ns, data)
        self.sequence += 1
        self.history.append(packet)
        self.last_activity = time.monotonic()
        metric(self.client_id, "received_packets")
        metric(self.client_id, "received_bytes", len(data))

        for queue in self.subscribers.values():
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                metric(self.client_id, "dropped_packets")
            queue.put_nowait(packet)

        if self.recorder_queue.full():
            metric(self.client_id, "storage_dropped_packets")
            logger.warning("storage_queue_full", extra={"client_id": self.client_id, "sequence": packet[0]})
        else:
            self.recorder_queue.put_nowait(packet)
        return packet[0]


streams = {}


async def record_stream(state):
    directory = STORAGE_ROOT / state.client_id
    await asyncio.to_thread(directory.mkdir, parents=True, exist_ok=True)
    output = None
    current_path = None
    pending = []
    last_flush = time.monotonic()
    while True:
        try:
            pending.append(await asyncio.wait_for(
                state.recorder_queue.get(),
                timeout=STORAGE_FLUSH_SECONDS,
            ))
        except asyncio.TimeoutError:
            pass
        if not pending:
            continue

        sequence, timestamp_ns, data = pending[0]
        now = time.monotonic()
        if (output is None or current_path is None or
                state.segment_bytes + sum(20 + len(item[2]) for item in pending) > STORAGE_SEGMENT_BYTES or
                now - state.segment_started >= STORAGE_SEGMENT_SECONDS):
            if output is not None:
                await asyncio.to_thread(output.close)
            state.segment_number += 1
            state.segment_bytes = 0
            state.segment_started = now
            current_path = directory / f"segment_{state.segment_number:08d}.dat"
            output = await asyncio.to_thread(current_path.open, "ab")
        try:
            batch = pending[:STORAGE_BATCH_SIZE]
            pending = pending[STORAGE_BATCH_SIZE:]
            payload = b"".join(
                struct.pack("!QQI", item[0], item[1], len(item[2])) + item[2]
                for item in batch
            )
            await asyncio.to_thread(output.write, payload)
            await asyncio.to_thread(output.flush)
            state.segment_bytes += len(payload)
            for item in batch:
                metric(state.client_id, "stored_bytes", len(item[2]))
        except OSError as exc:
            metric(state.client_id, "storage_errors")
            logger.error("storage_write_failed", extra={"client_id": state.client_id, "sequence": sequence, "error": str(exc)})


def get_stream(client_id):
    state = streams.get(client_id)
    if state is None:
        state = StreamState(client_id)
        state.recorder_task = asyncio.create_task(record_stream(state))
        streams[client_id] = state
    return state


async def reception_flux_video(websocket, client_id):
    state = get_stream(client_id)
    state.upload_active = True
    metric(client_id, "active_uploads")
    metric(client_id, "upload_connections")
    logger.info("stream_connected", extra={"client_id": client_id, "direction": "upload"})
    try:
        async for message in websocket:
            if not isinstance(message, bytes):
                await websocket.close(code=1003, reason="Binary frames required")
                return
            sequence = state.publish(message)
            if UPLOAD_ACK_EVERY > 0 and sequence % UPLOAD_ACK_EVERY == 0:
                await websocket.send(json.dumps({"status": "accepted", "sequence": sequence}))
    except websockets.exceptions.ConnectionClosed:
        pass
    finally:
        state.upload_active = False
        metric(client_id, "active_uploads", -1)
        logger.info("stream_disconnected", extra={"client_id": client_id, "direction": "upload"})


async def envoi_flux_video(websocket, client_id):
    state = get_stream(client_id)
    subscriber_id, queue = state.subscribe()
    metric(client_id, "download_connections")
    logger.info("stream_connected", extra={"client_id": client_id, "direction": "download"})
    try:
        while True:
            sequence, timestamp_ns, data = await queue.get()
            await websocket.send(data)
            metric(client_id, "sent_packets")
            metric(client_id, "sent_bytes", len(data))
    except websockets.exceptions.ConnectionClosed:
        pass
    finally:
        state.unsubscribe(subscriber_id)
        logger.info("stream_disconnected", extra={"client_id": client_id, "direction": "download"})


async def routeur_wss(websocket, path):
    parts = path.strip("/").split("/")
    if len(parts) != 2 or parts[0] not in {"upload", "download"}:
        await websocket.close(code=1008, reason="Use /upload/<client_id> or /download/<client_id>")
        return
    try:
        client_id = safe_client_id(parts[1])
    except ValueError:
        await websocket.close(code=1008, reason="Invalid client_id")
        return
    if parts[0] == "upload":
        await reception_flux_video(websocket, client_id)
    else:
        await envoi_flux_video(websocket, client_id)


def mqtt_tls(client):
    ca_file = os.getenv("MQTT_TLS_CA")
    if ca_file:
        client.tls_set(ca_certs=ca_file, certfile=os.getenv("MQTT_TLS_CERT"), keyfile=os.getenv("MQTT_TLS_KEY"))


def thread_telemetrie_mqtt():
    client = mqtt.Client(client_id=NODE_ID)
    if MQTT_USERNAME:
        client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)
    mqtt_tls(client)

    def on_connect(_, __, ___, rc):
        if rc == 0:
            logger.info("mqtt_connected")
        else:
            logger.error("mqtt_connection_failed", extra={"error": str(rc)})

    client.on_connect = on_connect
    try:
        client.connect(BROKER_IP, BROKER_PORT, BROKER_KEEPALIVE)
        client.loop_start()
    except Exception as exc:
        logger.error("mqtt_start_failed", extra={"error": str(exc)})
        return

    while True:
        disk = shutil.disk_usage(STORAGE_ROOT)
        snapshot = metric_snapshot()
        total = defaultdict(int)
        for values in snapshot.values():
            for name, value in values.items():
                total[name] += value
        payload = {
            "node_id": NODE_ID,
            "zone": ZONE_ID,
            "ip_address": NODE_IP,
            "port_wss": PORT_WSS,
            "storage_free_gb": round(disk.free / (1024 ** 3), 2),
            "cpu_usage_pct": psutil.cpu_percent(interval=None),
            "active_streams": sum(values.get("active_uploads", 0) for values in snapshot.values()),
            "metrics": dict(total),
        }
        client.publish(
            f"telemetrie/{ZONE_ID}/{NODE_ID}",
            json.dumps(payload),
            qos=0,
        )
        for client_id, values in snapshot.items():
            client.publish(
                f"telemetrie/{ZONE_ID}/{NODE_ID}/clients/{client_id}",
                json.dumps({"client_id": client_id, "metrics": values}),
                qos=0,
            )
        time.sleep(TELEMETRY_INTERVAL)


def build_ssl_context():
    certificate = os.getenv("TLS_CERT_FILE")
    private_key = os.getenv("TLS_KEY_FILE")
    if not certificate or not private_key:
        logger.warning("tls_disabled")
        return None
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certificate, private_key)
    return context


async def boucle_principale_wss():
    ssl_context = build_ssl_context()
    async with websockets.serve(
        routeur_wss,
        "0.0.0.0",
        PORT_WSS,
        ssl=ssl_context,
        max_size=MAX_MESSAGE_SIZE,
        compression=None,
        ping_interval=20,
        ping_timeout=20,
    ):
        logger.info("server_started", extra={"direction": "wss" if ssl_context else "ws"})
        await asyncio.Future()


if __name__ == "__main__":
    STORAGE_ROOT.mkdir(parents=True, exist_ok=True)
    logger.info("node_started")
    threading.Thread(target=thread_telemetrie_mqtt, daemon=True).start()
    asyncio.run(boucle_principale_wss())
