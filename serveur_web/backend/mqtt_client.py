import datetime
import json
import os
import socket
import threading
import time
import paho.mqtt.client as mqtt
from sqlalchemy.orm import Session

try:
    from .models import SessionLocal, EdgeNode, DataLocation, StreamSession
except ImportError:
    from models import SessionLocal, EdgeNode, DataLocation, StreamSession

BROKER_HOST = os.getenv("BROKER_HOST") or os.getenv("LAN_IP_BROKER") or os.getenv("BROKER_IP") or "127.0.0.1"
BROKER_PORT = int(os.getenv("BROKER_PORT", 1883))
MQTT_CLIENT_ID_PREFIX = os.getenv("MQTT_CLIENT_ID_PREFIX", "Backend_Controller")

# Garantit un identifiant unique par instance de backend pour éviter l'éviction mutuelle
MQTT_CLIENT_ID = os.getenv("MQTT_CLIENT_ID") or f"{MQTT_CLIENT_ID_PREFIX}_{socket.gethostname()}_{os.getpid()}_{int(time.time())}"

_mqtt_client = None
_mqtt_connected = False
_mqtt_lock = threading.Lock()


def is_mqtt_connected() -> bool:
    """Retourne l'état de connectivité du client MQTT pour le healthcheck."""
    return _mqtt_connected


def on_connect(client, userdata, flags, rc, *args):
    global _mqtt_connected
    if rc == 0:
        _mqtt_connected = True
        print(f"[Backend MQTT] Connecté avec succès au Broker {BROKER_HOST}:{BROKER_PORT} (ID: {MQTT_CLIENT_ID})", flush=True)
        client.subscribe("telemetrie/#", qos=1)
    else:
        _mqtt_connected = False
        print(f"[Backend MQTT] Échec de connexion au Broker MQTT (Code {rc})", flush=True)


def on_disconnect(client, userdata, rc, *args):
    global _mqtt_connected
    _mqtt_connected = False
    if rc != 0:
        print(f"[Backend MQTT] Déconnexion inattendue du broker MQTT (Code {rc})", flush=True)


def on_message(client, userdata, msg):
    try:
        topic_parts = msg.topic.split("/")
        payload = json.loads(msg.payload.decode())
        db: Session = SessionLocal()

        try:
            # Topic global : telemetrie/{ZONE_ID}/{NODE_ID}
            if len(topic_parts) == 3:
                zone_id = topic_parts[1]
                node_id = topic_parts[2]

                node = db.query(EdgeNode).filter(EdgeNode.node_id == node_id).first()
                if not node:
                    node = EdgeNode(node_id=node_id, zone_id=zone_id)
                    db.add(node)

                node.ip_address = payload.get("ip_address")
                node.wss_port = payload.get("port_wss")
                node.storage_available_gb = payload.get("stockage_dispo_gb")
                node.cpu_usage_pct = payload.get("cpu_usage_pct")
                node.last_seen = datetime.datetime.utcnow()
                db.commit()

            # Topic granulaire : telemetrie/{ZONE_ID}/{NODE_ID}/clients/{client_id}
            elif len(topic_parts) == 5 and topic_parts[3] == "clients":
                node_id = topic_parts[2]
                client_id = topic_parts[4]

                location = db.query(DataLocation).filter(
                    DataLocation.node_id == node_id,
                    DataLocation.client_id == client_id,
                ).first()

                if not location:
                    location = DataLocation(node_id=node_id, client_id=client_id)
                    db.add(location)

                location.blocks_count = payload.get("blocs_stockes", 0)
                location.total_size_kb = payload.get("taille_ko", 0.0)

                # Mettre à jour l'activité de la session si présente
                session = db.query(StreamSession).filter(StreamSession.session_id == client_id).first()
                if session and payload.get("active", True):
                    session.last_active = datetime.datetime.utcnow()

                db.commit()

        finally:
            db.close()

    except Exception as e:
        print(f"[Backend MQTT] Erreur traitement télémétrie: {e}", flush=True)


def _reconnect_loop(client):
    """Boucle de surveillance et reconnexion en arrière-plan."""
    backoff = 2
    max_backoff = 30
    while True:
        if not _mqtt_connected:
            try:
                print(f"[Backend MQTT] Tentative de connexion au broker {BROKER_HOST}:{BROKER_PORT}...", flush=True)
                client.connect(BROKER_HOST, BROKER_PORT, 60)
                time.sleep(3)  # Laisse le temps au thread réseau de recevoir CONNACK
                backoff = 2
            except Exception as e:
                print(f"[Backend MQTT] Broker inaccessible ({e}), nouvelle tentative dans {backoff}s...", flush=True)
                time.sleep(backoff)
                backoff = min(backoff * 2, max_backoff)
        else:
            time.sleep(5)


def start_mqtt():
    """Démarre le client MQTT de manière thread-safe et protégée contre la double initialisation."""
    global _mqtt_client

    with _mqtt_lock:
        if _mqtt_client is not None:
            return _mqtt_client

        # Compatibilité paho-mqtt v1 et v2
        try:
            client = mqtt.Client(
                callback_api_version=mqtt.CallbackAPIVersion.VERSION1,
                client_id=MQTT_CLIENT_ID
            )
        except (AttributeError, TypeError):
            client = mqtt.Client(client_id=MQTT_CLIENT_ID)

        client.on_connect = on_connect
        client.on_disconnect = on_disconnect
        client.on_message = on_message

        try:
            client.connect_async(BROKER_HOST, BROKER_PORT, 60)
        except Exception as e:
            print(f"[Backend MQTT] Broker non joignable immédiatement ({e}). Reconnexion en cours...", flush=True)

        client.loop_start()
        _mqtt_client = client

        # Thread de surveillance pour reconnexion robuste si déconnecté
        def monitor_reconnect():
            time.sleep(3)  # Laisse d'abord le temps à connect_async
            _reconnect_loop(client)

        threading.Thread(target=monitor_reconnect, daemon=True).start()
        return _mqtt_client
