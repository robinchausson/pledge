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

# Suivi du nombre de visionneurs connectés par session
active_viewers_lock = threading.Lock()
active_viewers = {}  # session_id -> int (nombre de spectateurs actifs)


def sanitize_id(raw_id: str) -> str:
    """Assainit un identifiant pour éviter toute traversée de chemin (path traversal)."""
    sanitized = re.sub(r'[^a-zA-Z0-9_\-.]', '_', raw_id.strip())
    if not sanitized or sanitized.startswith('.'):
        sanitized = f"session_{int(time.time())}"
    return sanitized


def purge_session_directory(session_dir: str):
    """Purge les blocs d'une session terminée et libère intégralement l'espace disque."""
    if os.path.exists(session_dir):
        try:
            for item in os.listdir(session_dir):
                item_path = os.path.join(session_dir, item)
                try:
                    if os.path.isfile(item_path):
                        os.remove(item_path)
                    elif os.path.isdir(item_path):
                        shutil.rmtree(item_path, ignore_errors=True)
                except OSError:
                    pass
            try:
                os.rmdir(session_dir)
            except OSError:
                pass
            print(f"[{NODE_ID}] Nettoyage effectué : stockage libéré pour {session_dir}", flush=True)
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
            c.subscribe("pledge/commands/#", qos=1)
        else:
            print(f"[{NODE_ID}] Erreur de connexion MQTT (Code {rc})", flush=True)

    def on_message(c, userdata, msg):
        try:
            topic = msg.topic
            payload = json.loads(msg.payload.decode('utf-8'))
            if topic == "pledge/commands/purge_session":
                s_id = payload.get("session_id")
                if s_id:
                    clean_id = sanitize_id(s_id)
                    s_dir = os.path.join(DOSSIER_RACINE_STOCKAGE, clean_id)
                    print(f"[{NODE_ID}] Commande MQTT de purge reçue pour : {clean_id}", flush=True)
                    purge_session_directory(s_dir)
                    with session_stats_lock:
                        session_stats.pop(clean_id, None)
        except Exception as err:
            print(f"[{NODE_ID}] Erreur message MQTT command: {err}", flush=True)

    client.on_connect = on_connect
    client.on_message = on_message

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
    """Écriture synchrone atomique exécutée dans le pool de threads dédié."""
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    tmp_path = f"{file_path}.tmp"
    with open(tmp_path, "wb") as f:
        f.write(data)
    os.replace(tmp_path, file_path)


async def reception_flux_video(websocket, client_id):
    clean_id = sanitize_id(client_id)
    dossier_client = os.path.join(DOSSIER_RACINE_STOCKAGE, clean_id)
    
    # Nettoyage des résidus d'une session précédente
    purge_session_directory(dossier_client)
    os.makedirs(dossier_client, exist_ok=True)

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

            owner_real_id = clean_id
            # Détection et parsing du paquet de transmission Phase 4
            try:
                text_msg = message if isinstance(message, str) else message.decode('utf-8')
                if text_msg.strip().startswith('{'):
                    packet_obj = json.loads(text_msg)
                    if isinstance(packet_obj, dict):
                        bloc_id = packet_obj.get("block_index", compteur_bloc)
                        if "payload_b64" in packet_obj:
                            chunk_size = len(packet_obj["payload_b64"])
                        if "signer_id" in packet_obj:
                            owner_real_id = packet_obj["signer_id"]
            except Exception:
                pass

            chemin_fichier = os.path.join(dossier_client, f"bloc_{bloc_id}.dat")

            # Délégation de l'écriture disque au ThreadPool pour ne pas bloquer l'event loop
            await loop.run_in_executor(io_executor, write_file_sync, chemin_fichier, raw_data)

            # Phase 3 : Worker de Transcodage Edge et re-signature déléguée (Section 3.6.2)
            if edge_transcoder:
                def transcode_and_save_worker(raw_bytes, b_id, real_owner):
                    try:
                        transcoded_res = edge_transcoder.process_and_sign_chunk(
                            session_id=clean_id,
                            block_id=b_id,
                            raw_chunk_payload=raw_bytes,
                            target_quality="720p",
                            owner_id=real_owner,
                        )
                        trans_path = os.path.join(dossier_client, f"bloc_{b_id}_720p.dat")
                        write_file_sync(trans_path, json.dumps(transcoded_res).encode("utf-8"))
                    except Exception as err:
                        print(f"[{NODE_ID}] Avertissement transcodage bloc {b_id}: {err}", flush=True)

                loop.run_in_executor(io_executor, transcode_and_save_worker, raw_data, bloc_id, owner_real_id)

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

        # Planifier la purge automatique après la fin du live
        def delayed_purge():
            time.sleep(4)
            # Attendre que les éventuels spectateurs aient terminé la lecture
            for _ in range(15):
                with active_viewers_lock:
                    if active_viewers.get(clean_id, 0) == 0:
                        break
                time.sleep(1)

            print(f"[{NODE_ID}] Purge automatique post-live du stockage pour : {clean_id}", flush=True)
            purge_session_directory(dossier_client)
            with session_stats_lock:
                session_stats.pop(clean_id, None)

        threading.Thread(target=delayed_purge, daemon=True).start()


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

    # Suivi des spectateurs connectés
    with active_viewers_lock:
        active_viewers[clean_id] = active_viewers.get(clean_id, 0) + 1

    # Amorçage séquentiel depuis le bloc 0 pour garantir l'envoi de l'en-tête WebM (MSE)
    # sans rupture de continuité temporelle
    compteur_bloc = 0
    print(f"[{NODE_ID}] Lecture {clean_id} : démarrage séquentiel depuis bloc 0 (mode={mode}, dernier={dernier_bloc})", flush=True)

    inactivity_counter = 0
    MAX_WAIT_SECONDS = 20

    try:
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

                    # Mise à jour dynamique du dernier bloc disponible
                    with session_stats_lock:
                        if clean_id in session_stats:
                            dernier_bloc = max(dernier_bloc, session_stats[clean_id].get("blocks", 0) - 1)

                    if compteur_bloc >= dernier_bloc:
                        await asyncio.sleep(0.04)  # Attente du prochain bloc au bord du direct
                    else:
                        await asyncio.sleep(0.02)  # Pacing régulier de rattrapage (50 blocs/sec)

                except (websockets.exceptions.ConnectionClosed, asyncio.CancelledError):
                    print(f"[{NODE_ID}] Visionneur déconnecté de {clean_id}", flush=True)
                    break
                except Exception as e:
                    print(f"[{NODE_ID}] Erreur lecture bloc: {e}", flush=True)
                    break
            else:
                # Vérifier si la session est terminée et si le spectateur a déjà tout lu
                is_active = True
                with session_stats_lock:
                    is_active = session_stats.get(clean_id, {}).get("active", False)

                if not is_active and compteur_bloc >= dernier_bloc and dernier_bloc > 0:
                    print(f"[{NODE_ID}] Diffusion achevée pour {clean_id}. Fin de lecture normale.", flush=True)
                    try:
                        await websocket.close(code=1000, reason="Fin du live")
                    except Exception:
                        pass
                    break

                # Polling d'attente d'un nouveau bloc
                await asyncio.sleep(0.05)
                inactivity_counter += 0.05
                if inactivity_counter >= MAX_WAIT_SECONDS:
                    print(f"[{NODE_ID}] Timeout inactivité flux pour {clean_id}. Fermeture lecture.", flush=True)
                    try:
                        await websocket.close(code=1000, reason="Fin du flux ou inactivité")
                    except Exception:
                        pass
                    break
    finally:
        with active_viewers_lock:
            active_viewers[clean_id] = max(0, active_viewers.get(clean_id, 1) - 1)


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
