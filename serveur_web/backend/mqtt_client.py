import datetime
import json
import os
import socket
import ssl
import threading
import time
import paho.mqtt.client as mqtt
from sqlalchemy.orm import Session

try:
    from .models import SessionLocal, EdgeNode, DataLocation, StreamSession
except ImportError:
    from models import SessionLocal, EdgeNode, DataLocation, StreamSession

MQTT_CLIENT_ID_PREFIX = os.getenv("MQTT_CLIENT_ID_PREFIX", "Backend_Controller")
MQTT_CLIENT_BASE_ID = os.getenv("MQTT_CLIENT_ID") or f"{MQTT_CLIENT_ID_PREFIX}_{socket.gethostname()}_{os.getpid()}"

# Dictionnaire des clients et statuts par zone (Zone 1: PC Robin, Zone 2: Jetson)
_mqtt_clients: dict[str, mqtt.Client] = {}
_mqtt_status: dict[str, bool] = {}
_mqtt_lock = threading.Lock()
_running = True


def get_zone_broker_configs() -> dict[str, dict]:
    """Retourne la configuration des brokers pour chaque zone de l'architecture (Zone_1, Zone_2)."""
    default_host = os.getenv("BROKER_HOST") or os.getenv("LAN_IP_BROKER") or os.getenv("BROKER_IP") or "127.0.0.1"
    default_port = int(os.getenv("BROKER_PORT") or 1883)
    default_tls = os.getenv("BROKER_TLS", "false").lower() in ("true", "1")
    default_ca = os.getenv("BROKER_CA_CERT", "")

    host_z1 = os.getenv("BROKER_HOST_ZONE_1") or default_host
    port_z1 = int(os.getenv("BROKER_PORT_ZONE_1") or (8883 if os.getenv("BROKER_TLS_ZONE_1", "false").lower() in ("true", "1") else default_port))
    tls_z1 = os.getenv("BROKER_TLS_ZONE_1", str(default_tls)).lower() in ("true", "1")
    ca_z1 = os.getenv("BROKER_CA_CERT_ZONE_1", default_ca)

    host_z2 = os.getenv("BROKER_HOST_ZONE_2") or os.getenv("BROKER_HOST_Z2") or default_host
    port_z2 = int(os.getenv("BROKER_PORT_ZONE_2") or os.getenv("BROKER_PORT_Z2") or (8883 if os.getenv("BROKER_TLS_ZONE_2", "false").lower() in ("true", "1") else default_port))
    tls_z2 = os.getenv("BROKER_TLS_ZONE_2", "false").lower() in ("true", "1")
    ca_z2 = os.getenv("BROKER_CA_CERT_ZONE_2", "")

    configs = {
        "Zone_1": {
            "host": host_z1,
            "port": port_z1,
            "tls": tls_z1,
            "ca_cert": ca_z1,
        },
        "Zone_2": {
            "host": host_z2,
            "port": port_z2,
            "tls": tls_z2,
            "ca_cert": ca_z2,
        },
    }
    return configs


def is_mqtt_connected(zone_id: str | None = None) -> bool:
    """Retourne True si le broker de la zone (ou au moins un broker) est connecté."""
    if zone_id:
        return _mqtt_status.get(zone_id, False)
    return any(_mqtt_status.values()) if _mqtt_status else False


def get_mqtt_health() -> dict[str, str]:
    """Rapporte l'état de chaque broker de zone pour le healthcheck FastAPI."""
    configs = get_zone_broker_configs()
    report = {}
    for z in configs.keys():
        report[z] = "connected" if _mqtt_status.get(z, False) else "disconnected"
    return report


def make_on_connect(zone_id: str):
    def on_connect(client, userdata, flags, rc, *args):
        if rc == 0:
            _mqtt_status[zone_id] = True
            print(f"[Backend MQTT] Connecté avec succès au Broker {zone_id} (Client: {client._client_id.decode() if isinstance(client._client_id, bytes) else client._client_id})", flush=True)
            client.subscribe("telemetrie/#", qos=1)
        else:
            _mqtt_status[zone_id] = False
            print(f"[Backend MQTT] Échec de connexion au Broker {zone_id} (Code {rc})", flush=True)
    return on_connect


def make_on_disconnect(zone_id: str):
    def on_disconnect(client, userdata, rc, *args):
        _mqtt_status[zone_id] = False
        if rc != 0:
            print(f"[Backend MQTT] Déconnexion inattendue du broker {zone_id} (Code {rc})", flush=True)
    return on_disconnect


def make_on_message(zone_id: str):
    def on_message(client, userdata, msg):
        try:
            topic_parts = msg.topic.split("/")
            payload = json.loads(msg.payload.decode())
            db: Session = SessionLocal()

            try:
                # Topic global : telemetrie/{ZONE_ID}/{NODE_ID}
                if len(topic_parts) == 3:
                    msg_zone_id = topic_parts[1] or zone_id
                    node_id = topic_parts[2]
                    detected_zone = payload.get("zone") or payload.get("zone_id") or msg_zone_id or zone_id

                    node = db.query(EdgeNode).filter(EdgeNode.node_id == node_id).first()
                    if not node:
                        node = EdgeNode(node_id=node_id, zone_id=detected_zone)
                        db.add(node)
                    else:
                        node.zone_id = detected_zone

                    node.ip_address = payload.get("ip_address")
                    node.wss_port = payload.get("port_wss")
                    node.storage_available_gb = payload.get("stockage_dispo_gb")
                    node.cpu_usage_pct = payload.get("cpu_usage_pct")
                    node.last_seen = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
                    db.commit()

                # Topic granulaire : telemetrie/{ZONE_ID}/{NODE_ID}/clients/{client_id}
                elif len(topic_parts) == 5 and topic_parts[3] == "clients":
                    msg_zone_id = topic_parts[1] or zone_id
                    node_id = topic_parts[2]
                    client_id = topic_parts[4]
                    detected_zone = payload.get("zone") or payload.get("zone_id") or msg_zone_id or zone_id

                    node = db.query(EdgeNode).filter(EdgeNode.node_id == node_id).first()
                    if node:
                        node.zone_id = detected_zone
                    else:
                        node = EdgeNode(node_id=node_id, zone_id=detected_zone)
                        db.add(node)

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
                        session.last_active = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
                        if detected_zone:
                            session.zone_id = detected_zone
                            session.current_zone = detected_zone

                    db.commit()

            finally:
                db.close()

        except Exception as e:
            print(f"[Backend MQTT {zone_id}] Erreur traitement télémétrie: {e}", flush=True)
    return on_message


def _reconnect_loop(client, zone_id: str, host: str, port: int):
    """Boucle de surveillance et reconnexion résiliente par zone."""
    backoff = 2
    max_backoff = 30
    while _running:
        if not _mqtt_status.get(zone_id, False):
            try:
                print(f"[Backend MQTT] Tentative reconnexion au broker {zone_id} ({host}:{port})...", flush=True)
                client.reconnect()
                time.sleep(3)
                backoff = 2
            except Exception:
                try:
                    client.connect(host, port, 60)
                    time.sleep(3)
                    backoff = 2
                except Exception:
                    time.sleep(backoff)
                    backoff = min(backoff * 2, max_backoff)
        else:
            time.sleep(5)


def start_mqtt():
    """Démarre les clients MQTT pour toutes les zones configurées (Zone 1 + Zone 2)."""
    global _mqtt_clients, _running
    _running = True

    with _mqtt_lock:
        if _mqtt_clients:
            return _mqtt_clients.get("Zone_1")

        zone_configs = get_zone_broker_configs()

        for z_id, conf in zone_configs.items():
            client_id = f"{MQTT_CLIENT_BASE_ID}_{z_id}_{int(time.time())}"
            try:
                client = mqtt.Client(
                    callback_api_version=mqtt.CallbackAPIVersion.VERSION1,
                    client_id=client_id,
                )
            except (AttributeError, TypeError):
                client = mqtt.Client(client_id=client_id)

            if conf.get("tls", False):
                ca = conf.get("ca_cert")
                client.tls_set(ca_certs=ca if (ca and os.path.exists(ca)) else None, cert_reqs=ssl.CERT_REQUIRED if (ca and os.path.exists(ca)) else ssl.CERT_NONE)
                print(f"[Backend MQTT] MQTTS (TLS) activé pour {z_id} sur port {conf['port']}", flush=True)

            client.on_connect = make_on_connect(z_id)
            client.on_disconnect = make_on_disconnect(z_id)
            client.on_message = make_on_message(z_id)

            try:
                client.connect_async(conf["host"], conf["port"], 60)
            except Exception as e:
                print(f"[Backend MQTT] Broker {z_id} non joignable immédiatement ({e}). Reconnexion en cours...", flush=True)

            client.loop_start()
            _mqtt_clients[z_id] = client
            _mqtt_status[z_id] = False

            threading.Thread(
                target=_reconnect_loop,
                args=(client, z_id, conf["host"], conf["port"]),
                daemon=True,
            ).start()

        return _mqtt_clients.get("Zone_1")


def stop_mqtt():
    """Arrête proprement tous les clients MQTT et stoppe les boucles de reconnexion."""
    global _running, _mqtt_clients
    _running = False
    with _mqtt_lock:
        for z_id, cl in list(_mqtt_clients.items()):
            try:
                cl.loop_stop()
                cl.disconnect()
            except Exception:
                pass
            _mqtt_status[z_id] = False
        _mqtt_clients.clear()


def publish_event(topic: str, payload: dict | str, target_zone: str | None = None):
    """Publie un message / commande MQTT vers le broker d'une zone ou en diffusion générale."""
    body = json.dumps(payload) if isinstance(payload, dict) else str(payload)
    if target_zone and target_zone in _mqtt_clients:
        try:
            _mqtt_clients[target_zone].publish(topic, body, qos=1)
        except Exception as e:
            print(f"[Backend MQTT {target_zone}] Erreur publish sur {topic}: {e}", flush=True)
    else:
        for z_id, cl in _mqtt_clients.items():
            try:
                cl.publish(topic, body, qos=1)
            except Exception as e:
                print(f"[Backend MQTT {z_id}] Erreur publish sur {topic}: {e}", flush=True)
