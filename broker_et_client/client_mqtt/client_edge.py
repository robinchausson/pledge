import asyncio
import concurrent.futures
import json
import os
import psutil
import re
import shutil
import ssl
import threading
import time
import websockets
import paho.mqtt.client as mqtt

# --- CONFIGURATION RÉSEAU & IDENTITÉ ---
NODE_ID = os.getenv("NODE_ID", "Edge_Node_01")
ZONE_ID = os.getenv("ZONE_ID", "Zone_1")

# Hôte annoncé et port WS/WSS
NODE_HOST = os.getenv("NODE_HOST") or os.getenv("LAN_IP_NODE") or os.getenv("NODE_IP") or "127.0.0.1"
EDGE_WS_PORT = int(os.getenv("EDGE_WS_PORT") or os.getenv("PORT_WSS") or 8444)
EDGE_WS_SCHEME = (os.getenv("EDGE_WS_SCHEME") or "ws").lower()

# Broker MQTT
BROKER_HOST = os.getenv("BROKER_HOST") or os.getenv("LAN_IP_BROKER") or os.getenv("BROKER_IP") or "127.0.0.1"
BROKER_PORT = int(os.getenv("BROKER_PORT", 1883))

# Stockage et Télémétrie
DOSSIER_RACINE_STOCKAGE = os.getenv("EDGE_DATA_DIR", "/donnees_isolees")
os.makedirs(DOSSIER_RACINE_STOCKAGE, exist_ok=True)
TELEMETRY_INTERVAL_SEC = int(os.getenv("TELEMETRY_INTERVAL_SEC", 5))

# Certificats TLS optionnels pour WSS
TLS_CERT_FILE = os.getenv("TLS_CERT_FILE", "")
TLS_KEY_FILE = os.getenv("TLS_KEY_FILE", "")

# ThreadPool pour les écritures disque asynchrones non bloquantes
io_executor = concurrent.futures.ThreadPoolExecutor(max_workers=4)

# Suivi en mémoire des statistiques clients/sessions pour éviter les rescans disque
session_stats_lock = threading.Lock()
session_stats = {}  # session_id -> {"blocks": int, "bytes": int, "active": bool}


def sanitize_id(raw_id: str) -> str:
    """Assainit un identifiant pour éviter toute traversée de chemin (path traversal)."""
    sanitized = re.sub(r'[^a-zA-Z0-9_\-.]', '_', raw_id.strip())
    if not sanitized or sanitized.startswith('.'):
        sanitized = f"session_{int(time.time())}"
    return sanitized


def purge_session_directory(session_dir: str):
    """Purge les blocs résiduels d'une session précédente lors d'un nouvel upload."""
    if os.path.exists(session_dir):
        try:
            for item in os.listdir(session_dir):
                if item.endswith('.dat'):
                    try:
                        os.remove(os.path.join(session_dir, item))
                    except OSError:
                        pass
        except Exception as e:
            print(f"[{NODE_ID}] Avertissement purge session: {e}", flush=True)


# ==========================================
# 1. PLAN DE CONTRÔLE : TÉLÉMÉTRIE MQTT
# ==========================================
def thread_telemetrie_mqtt():
    mqtt_client_id = f"{NODE_ID}_telemetry"
    client = mqtt.Client(client_id=mqtt_client_id)

    def on_connect(c, userdata, flags, rc):
        if rc == 0:
            print(f"[{NODE_ID}] Connecté au Broker MQTT {BROKER_HOST}:{BROKER_PORT}", flush=True)
        else:
            print(f"[{NODE_ID}] Erreur de connexion MQTT (Code {rc})", flush=True)

    client.on_connect = on_connect

    while True:
        try:
            client.connect(BROKER_HOST, BROKER_PORT, 60)
            client.loop_start()
            break
        except Exception as e:
            print(f"[{NODE_ID}] Attente du Broker MQTT ({BROKER_HOST}:{BROKER_PORT}): {e}", flush=True)
            time.sleep(3)

    while True:
        try:
            # 1. Télémétrie Globale
            charge_cpu = psutil.cpu_percent(interval=1)
            
            # Mesure réelle sur le dossier de données monté
            try:
                _, _, free = shutil.disk_usage(DOSSIER_RACINE_STOCKAGE)
                stockage_dispo_gb = round(free / (1024 ** 3), 2)
            except Exception:
                stockage_dispo_gb = 0.0

            payload_global = {
                "noeud_id": NODE_ID,
                "zone": ZONE_ID,
                "ip_address": NODE_HOST,
                "port_wss": EDGE_WS_PORT,
                "scheme": EDGE_WS_SCHEME,
                "stockage_dispo_gb": stockage_dispo_gb,
                "cpu_usage_pct": charge_cpu,
                "timestamp": time.time(),
            }
            client.publish(f"telemetrie/{ZONE_ID}/{NODE_ID}", json.dumps(payload_global), qos=1)

            # 2. Télémétrie Granulaire par Session (lecture mémoire instantanée)
            with session_stats_lock:
                current_sessions = dict(session_stats)

            for s_id, stats in current_sessions.items():
                if stats.get("blocks", 0) > 0 or stats.get("active", False):
                    payload_client = {
                        "client_id": s_id,
                        "blocs_stockes": stats.get("blocks", 0),
                        "taille_ko": round(stats.get("bytes", 0) / 1024, 2),
                        "active": stats.get("active", False)
                    }
                    topic_client = f"telemetrie/{ZONE_ID}/{NODE_ID}/clients/{s_id}"
                    client.publish(topic_client, json.dumps(payload_client), qos=1)

        except Exception as err:
            print(f"[{NODE_ID}] Erreur boucle télémétrie: {err}", flush=True)

        time.sleep(TELEMETRY_INTERVAL_SEC)


# ==========================================
# 2. PLAN DE DONNÉES : STREAMING WEBSOCKET
# ==========================================
def write_file_sync(file_path: str, data: bytes):
    """Écriture synchrone exécutée dans le pool de threads dédié."""
    with open(file_path, "wb") as f:
        f.write(data)


async def reception_flux_video(websocket, client_id):
    clean_id = sanitize_id(client_id)
    dossier_client = os.path.join(DOSSIER_RACINE_STOCKAGE, clean_id)
    os.makedirs(dossier_client, exist_ok=True)
    
    # Nettoyage des résidus d'une session précédente
    purge_session_directory(dossier_client)

    with session_stats_lock:
        session_stats[clean_id] = {"blocks": 0, "bytes": 0, "active": True}

    print(f"[{NODE_ID}] Nouvelle session d'upload initialisée pour : {clean_id}", flush=True)
    loop = asyncio.get_running_loop()
    compteur_bloc = 0

    try:
        async for message in websocket:
            raw_data = message if isinstance(message, bytes) else message.encode('utf-8')
            chunk_size = len(raw_data)
            chemin_fichier = os.path.join(dossier_client, f"bloc_{compteur_bloc}.dat")

            # Délégation de l'écriture disque au ThreadPool pour ne pas bloquer l'event loop
            await loop.run_in_executor(io_executor, write_file_sync, chemin_fichier, raw_data)

            # Mise à jour rapide des statistiques en mémoire
            with session_stats_lock:
                if clean_id in session_stats:
                    session_stats[clean_id]["blocks"] = compteur_bloc + 1
                    session_stats[clean_id]["bytes"] += chunk_size

            # Acquittement immédiat
            await websocket.send(json.dumps({"statut": "ok", "bloc_id": compteur_bloc}))
            compteur_bloc += 1

    except websockets.exceptions.ConnectionClosed:
        print(f"[{NODE_ID}] Fin de session upload pour : {clean_id}", flush=True)
    finally:
        with session_stats_lock:
            if clean_id in session_stats:
                session_stats[clean_id]["active"] = False


async def envoi_flux_video(websocket, client_id):
    clean_id = sanitize_id(client_id)
    dossier_client = os.path.join(DOSSIER_RACINE_STOCKAGE, clean_id)

    if not os.path.exists(dossier_client):
        await websocket.close(code=1011, reason="Session introuvable sur ce noeud")
        return

    print(f"[{NODE_ID}] Nouvelle session de lecture (Download) pour : {clean_id}", flush=True)
    compteur_bloc = 0
    inactivity_counter = 0
    MAX_WAIT_SECONDS = 20

    while True:
        chemin_fichier = os.path.join(dossier_client, f"bloc_{compteur_bloc}.dat")
        if os.path.exists(chemin_fichier):
            inactivity_counter = 0
            try:
                loop = asyncio.get_running_loop()
                # Lecture non bloquante
                with open(chemin_fichier, "rb") as f:
                    donnees = f.read()

                await websocket.send(donnees)
                compteur_bloc += 1

                # Attente facultative d'acquittement (avec timeout)
                try:
                    await asyncio.wait_for(websocket.recv(), timeout=5.0)
                except asyncio.TimeoutError:
                    pass

            except (websockets.exceptions.ConnectionClosed, asyncio.CancelledError):
                print(f"[{NODE_ID}] Visionneur déconnecté de {clean_id}", flush=True)
                break
            except Exception as e:
                print(f"[{NODE_ID}] Erreur lecture bloc: {e}", flush=True)
                break
        else:
            # Vérifier si la session est encore active ou fermée
            await asyncio.sleep(0.3)
            inactivity_counter += 0.3
            if inactivity_counter >= MAX_WAIT_SECONDS:
                print(f"[{NODE_ID}] Timeout inactivité flux pour {clean_id}. Fermeture lecture.", flush=True)
                try:
                    await websocket.close(code=1000, reason="Fin du flux ou inactivité")
                except Exception:
                    pass
                break


async def routeur_ws(websocket, path=None):
    # Support de la compatibilité websockets version 11 et 14+
    request_path = getattr(websocket, "path", path) or ""
    parts = request_path.strip("/").split("/")

    if len(parts) >= 2:
        action = parts[0].lower()
        client_id = parts[1]
    elif len(parts) == 1 and parts[0]:
        action = "upload"
        client_id = parts[0]
    else:
        action = "upload"
        client_id = "default_stream"

    if action == "upload":
        await reception_flux_video(websocket, client_id)
    elif action == "download":
        await envoi_flux_video(websocket, client_id)
    else:
        await websocket.close(code=1011, reason="Action inconnue")


async def boucle_principale_ws():
    ssl_context = None
    if EDGE_WS_SCHEME == "wss":
        if os.path.exists(TLS_CERT_FILE) and os.path.exists(TLS_KEY_FILE):
            ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ssl_context.load_cert_chain(TLS_CERT_FILE, TLS_KEY_FILE)
            print(f"[{NODE_ID}] Mode sécurisé WSS activé avec certificat {TLS_CERT_FILE}", flush=True)
        else:
            print(f"[{NODE_ID}] AVERTISSEMENT : Certificats TLS introuvables. Repli sur ws.", flush=True)

    protocol_label = "WSS" if ssl_context else "WS"
    async with websockets.serve(
        routeur_ws,
        "0.0.0.0",
        EDGE_WS_PORT,
        ssl=ssl_context,
        max_size=None,
        ping_timeout=60,
        ping_interval=20,
    ):
        print(f"[{NODE_ID}] Serveur {protocol_label} opérationnel sur le port {EDGE_WS_PORT}...", flush=True)
        await asyncio.Future()


if __name__ == "__main__":
    print(f"--- Démarrage du Nœud Edge {NODE_ID} (Zone: {ZONE_ID}) ---", flush=True)
    # Lancement du plan de contrôle MQTT en arrière-plan
    threading.Thread(target=thread_telemetrie_mqtt, daemon=True).start()
    # Lancement de la boucle principale de données WebSocket
    asyncio.run(boucle_principale_ws())
