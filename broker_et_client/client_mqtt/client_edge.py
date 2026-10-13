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
from urllib.parse import urlparse, parse_qs

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

# Worker de Transcodage Edge et Re-signature Déléguée (Phase 3)
try:
    from edge_transcoder import EdgeTranscoder
    edge_transcoder = EdgeTranscoder(node_id=NODE_ID, zone_id=ZONE_ID)
except Exception as _exc:
    try:
        from .edge_transcoder import EdgeTranscoder
        edge_transcoder = EdgeTranscoder(node_id=NODE_ID, zone_id=ZONE_ID)
    except Exception:
        edge_transcoder = None
        print(f"[{NODE_ID}] Avertissement: EdgeTranscoder non initialisé: {_exc}", flush=True)

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

    print(f"[{NODE_ID}] Nouvelle session d'upload direct (Phase 4 P2P) pour : {clean_id}", flush=True)
    loop = asyncio.get_running_loop()
    compteur_bloc = 0

    try:
        async for message in websocket:
            raw_data = message if isinstance(message, bytes) else message.encode('utf-8')
            chunk_size = len(raw_data)
            bloc_id = compteur_bloc

            # Détection et parsing du paquet de transmission Phase 4
            try:
                text_msg = message if isinstance(message, str) else message.decode('utf-8')
                if text_msg.strip().startswith('{'):
                    packet_obj = json.loads(text_msg)
                    if isinstance(packet_obj, dict):
                        bloc_id = packet_obj.get("block_index", compteur_bloc)
                        if "payload_b64" in packet_obj:
                            chunk_size = len(packet_obj["payload_b64"])
            except Exception:
                pass

            chemin_fichier = os.path.join(dossier_client, f"bloc_{bloc_id}.dat")

            # Délégation de l'écriture disque au ThreadPool pour ne pas bloquer l'event loop
            await loop.run_in_executor(io_executor, write_file_sync, chemin_fichier, raw_data)

            # Phase 3 : Worker de Transcodage Edge et re-signature déléguée (Section 3.6.2)
            if edge_transcoder:
                def transcode_and_save_worker(raw_bytes, b_id):
                    try:
                        transcoded_res = edge_transcoder.process_and_sign_chunk(
                            session_id=clean_id,
                            block_id=b_id,
                            raw_chunk_payload=raw_bytes,
                            target_quality="720p",
                            owner_id=clean_id,
                        )
                        trans_path = os.path.join(dossier_client, f"bloc_{b_id}_720p.dat")
                        write_file_sync(trans_path, json.dumps(transcoded_res).encode("utf-8"))
                    except Exception as err:
                        print(f"[{NODE_ID}] Avertissement transcodage bloc {b_id}: {err}", flush=True)

                loop.run_in_executor(io_executor, transcode_and_save_worker, raw_data, bloc_id)

            # Mise à jour rapide des statistiques en mémoire
            with session_stats_lock:
                if clean_id in session_stats:
                    session_stats[clean_id]["blocks"] = max(session_stats[clean_id]["blocks"], bloc_id + 1)
                    session_stats[clean_id]["bytes"] += chunk_size

            # Acquittement immédiat au diffuseur
            await websocket.send(json.dumps({"statut": "ok", "bloc_id": bloc_id}))
            compteur_bloc = bloc_id + 1

    except websockets.exceptions.ConnectionClosed:
        print(f"[{NODE_ID}] Fin de session upload pour : {clean_id}", flush=True)
    finally:
        with session_stats_lock:
            if clean_id in session_stats:
                session_stats[clean_id]["active"] = False


async def envoi_flux_video(websocket, client_id, mode="live", fmt="auto", quality="original"):
    clean_id = sanitize_id(client_id)
    dossier_client = os.path.join(DOSSIER_RACINE_STOCKAGE, clean_id)
    is_transcoded_req = quality in ("720p", "transcoded", "low")
    suffix = "_720p" if is_transcoded_req else ""

    if not os.path.exists(dossier_client):
        await websocket.close(code=1011, reason="Session introuvable sur ce noeud")
        return

    print(f"[{NODE_ID}] Nouvelle session de lecture directe (Download P2P, mode={mode}, format={fmt}, qualite={quality}) pour : {clean_id}", flush=True)

    # Récupération du dernier bloc disponible
    dernier_bloc = 0
    with session_stats_lock:
        if clean_id in session_stats:
            dernier_bloc = max(0, session_stats[clean_id].get("blocks", 0) - 1)

    if dernier_bloc == 0 and os.path.exists(dossier_client):
        try:
            bloc_files = [
                int(f.split("_")[1].split(".")[0])
                for f in os.listdir(dossier_client)
                if f.startswith("bloc_") and f.endswith(".dat") and f.split("_")[1].split(".")[0].isdigit()
            ]
            if bloc_files:
                dernier_bloc = max(bloc_files)
        except Exception:
            pass

    BUFFER_INITIAL_BLOCS = 3

    async def envoyer_bloc(data_bytes: bytes):
        """Envoie le bloc sous forme brute ou JSON selon le format demandé."""
        if fmt == "raw":
            try:
                parsed = json.loads(data_bytes.decode('utf-8'))
                if isinstance(parsed, dict) and "payload_b64" in parsed:
                    import base64
                    await websocket.send(base64.b64decode(parsed["payload_b64"]))
                    return
                elif isinstance(parsed, dict) and "chunk_b64" in parsed:
                    import base64
                    await websocket.send(base64.b64decode(parsed["chunk_b64"]))
                    return
            except Exception:
                pass
            await websocket.send(data_bytes)
        else:
            # Format par défaut : envoi direct du paquet (JSON ou binaire)
            try:
                # Si c'est du texte JSON valide, envoyer comme frame texte WS
                text = data_bytes.decode('utf-8')
                await websocket.send(text)
            except UnicodeDecodeError:
                await websocket.send(data_bytes)

    if mode == "live" and dernier_bloc > BUFFER_INITIAL_BLOCS:
        # En mode live avec historique :
        # 1. Envoi prioritaire du bloc 0 (contenant l'en-tête d'initialisation WebM nécessaire à MSE)
        chemin_init = os.path.join(dossier_client, f"bloc_0{suffix}.dat")
        if not os.path.exists(chemin_init) and suffix:
            chemin_init = os.path.join(dossier_client, "bloc_0.dat")

        if os.path.exists(chemin_init):
            try:
                with open(chemin_init, "rb") as f:
                    donnees_init = f.read()
                await envoyer_bloc(donnees_init)
                # Court délai d'attente pour acquittement éventuel
                try:
                    await asyncio.wait_for(websocket.recv(), timeout=0.05)
                except asyncio.TimeoutError:
                    pass
            except Exception as e:
                print(f"[{NODE_ID}] Erreur envoi bloc initial WebM: {e}", flush=True)

        # 2. Démarrage directement sur les blocs récents (tampon glissant de 3 blocs)
        compteur_bloc = max(1, dernier_bloc - BUFFER_INITIAL_BLOCS)
        print(f"[{NODE_ID}] Diffusion Live {clean_id} : amorçage au bloc {compteur_bloc} (dernier={dernier_bloc})", flush=True)
    else:
        # Mode replay / début du flux
        compteur_bloc = 0
        print(f"[{NODE_ID}] Lecture {clean_id} : démarrage depuis le début (bloc 0)", flush=True)

    inactivity_counter = 0
    MAX_WAIT_SECONDS = 20

    while True:
        chemin_fichier = os.path.join(dossier_client, f"bloc_{compteur_bloc}{suffix}.dat")
        # Si le bloc transcodé demandé n'est pas encore sur disque, vérifier le bloc original
        if not os.path.exists(chemin_fichier) and suffix:
            chemin_orig = os.path.join(dossier_client, f"bloc_{compteur_bloc}.dat")
            if os.path.exists(chemin_orig) and edge_transcoder:
                try:
                    with open(chemin_orig, "rb") as f:
                        raw_bytes = f.read()
                    transcoded_res = edge_transcoder.process_and_sign_chunk(
                        session_id=clean_id,
                        block_id=compteur_bloc,
                        raw_chunk_payload=raw_bytes,
                        target_quality="720p",
                        owner_id=clean_id,
                    )
                    write_file_sync(chemin_fichier, json.dumps(transcoded_res).encode("utf-8"))
                except Exception as e:
                    print(f"[{NODE_ID}] Erreur transcodage volée bloc {compteur_bloc}: {e}", flush=True)
                    chemin_fichier = chemin_orig
            else:
                chemin_fichier = chemin_orig

        if os.path.exists(chemin_fichier):
            inactivity_counter = 0
            try:
                # Lecture du bloc
                with open(chemin_fichier, "rb") as f:
                    donnees = f.read()

                await envoyer_bloc(donnees)
                compteur_bloc += 1

                # Attente facultative d'acquittement (avec court timeout de 0.1s)
                try:
                    await asyncio.wait_for(websocket.recv(), timeout=0.1)
                except asyncio.TimeoutError:
                    pass

            except (websockets.exceptions.ConnectionClosed, asyncio.CancelledError):
                print(f"[{NODE_ID}] Visionneur déconnecté de {clean_id}", flush=True)
                break
            except Exception as e:
                print(f"[{NODE_ID}] Erreur lecture bloc: {e}", flush=True)
                break
        else:
            # Vérifier si la session est encore active ou fermée (polling rapide à 50ms)
            await asyncio.sleep(0.05)
            inactivity_counter += 0.05
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
    parsed = urlparse(request_path)
    clean_path = parsed.path.strip("/")
    parts = clean_path.split("/") if clean_path else []
    query_params = parse_qs(parsed.query)
    mode = query_params.get("mode", ["live"])[0].lower()
    fmt = query_params.get("format", ["auto"])[0].lower()
    quality = query_params.get("quality", ["original"])[0].lower()

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
        await envoi_flux_video(websocket, client_id, mode=mode, fmt=fmt, quality=quality)
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
