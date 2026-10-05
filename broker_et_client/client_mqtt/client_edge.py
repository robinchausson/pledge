import paho.mqtt.client as mqtt
import asyncio
import websockets
import threading
import psutil
import shutil
import time
import json
import os

# --- CONFIGURATION ---
BROKER_IP = os.getenv("BROKER_IP", "10.4.19.234") 
BROKER_PORT = int(os.getenv("BROKER_PORT", 1883))
NODE_ID = os.getenv("NODE_ID", "PC_Julian")
ZONE_ID = os.getenv("ZONE_ID", "Zone_1")
PORT_WSS = int(os.getenv("PORT_WSS", 8443))
NODE_IP = os.getenv("NODE_IP", "127.0.0.1") # IP par défaut à adapter selon le déploiement

DOSSIER_RACINE_STOCKAGE = "/donnees_isolees"
os.makedirs(DOSSIER_RACINE_STOCKAGE, exist_ok=True)

# ==========================================
# 1. PLAN DE CONTRÔLE : TÉLÉMÉTRIE MQTT (En arrière-plan)
# ==========================================
def thread_telemetrie_mqtt():
    client = mqtt.Client(client_id=NODE_ID)
    
    def on_connect(client, userdata, flags, rc):
        if rc == 0:
            print(f"[{NODE_ID}] Connecté au Broker MQTT {BROKER_IP}")
        else:
            print(f"[{NODE_ID}] Erreur de connexion MQTT (Code {rc})")
            
    client.on_connect = on_connect
    
    try:
        client.connect(BROKER_IP, BROKER_PORT, 60)
        client.loop_start()
    except Exception as e:
        print(f"Erreur fatale MQTT : {e}")
        return

    # Cette boucle infinie tourne désormais dans son propre Thread sans bloquer le reste
    while True:
        # 1. Télémétrie Globale du Nœud (Inchangée)
        charge_cpu = psutil.cpu_percent(interval=1)
        _, _, free = shutil.disk_usage("/")
        stockage_dispo_gb = round(free / (1024 ** 3), 2)
        
        payload_global = {
            "noeud_id": NODE_ID,
            "zone": ZONE_ID,
            "ip_address": NODE_IP,
            "port_wss": PORT_WSS,
            "stockage_dispo_gb": stockage_dispo_gb,
            "cpu_usage_pct": charge_cpu,
        }
        client.publish(f"telemetrie/{ZONE_ID}/{NODE_ID}", json.dumps(payload_global), qos=1)
        
        # 2. Télémétrie Granulaire par Client (Nouveau)
        # Le script parcourt le dossier racine pour voir qui stocke quoi
        if os.path.exists(DOSSIER_RACINE_STOCKAGE):
            for nom_client in os.listdir(DOSSIER_RACINE_STOCKAGE):
                chemin_client = os.path.join(DOSSIER_RACINE_STOCKAGE, nom_client)
                
                if os.path.isdir(chemin_client):
                    # Compte le nombre de blocs (.dat) et calcule la taille totale
                    fichiers = os.listdir(chemin_client)
                    nombre_blocs = len([f for f in fichiers if f.endswith('.dat')])
                    taille_totale_octets = sum(os.path.getsize(os.path.join(chemin_client, f)) for f in fichiers)
                    
                    payload_client = {
                        "client_id": nom_client,
                        "blocs_stockes": nombre_blocs,
                        "taille_ko": round(taille_totale_octets / 1024, 2)
                    }
                    
                    # Publication sur un topic extrêmement précis
                    topic_client = f"telemetrie/{ZONE_ID}/{NODE_ID}/clients/{nom_client}"
                    client.publish(topic_client, json.dumps(payload_client), qos=1)

        time.sleep(9)

# ==========================================
# 2. PLAN DE DONNÉES : ISOLATION ET WSS (Programme principal)
# ==========================================
async def reception_flux_video(websocket, client_id):
    dossier_client = os.path.join(DOSSIER_RACINE_STOCKAGE, client_id)
    os.makedirs(dossier_client, exist_ok=True)
    
    print(f"[{NODE_ID}] Nouvelle connexion WSS (Upload). Espace alloué pour : {client_id}")

    compteur_bloc = 0
    try:
        async for message in websocket:
            chemin_fichier = os.path.join(dossier_client, f"bloc_{compteur_bloc}.dat")
            with open(chemin_fichier, "wb") as f:
                f.write(message)
                
            print(f"[{NODE_ID}] {client_id} -> Bloc {compteur_bloc} isolé et stocké.")
            await websocket.send(json.dumps({"statut": "ok", "bloc_id": compteur_bloc}))
            compteur_bloc += 1
    except websockets.exceptions.ConnectionClosed:
        print(f"[{NODE_ID}] Diffuseur déconnecté.")

async def envoi_flux_video(websocket, client_id):
    dossier_client = os.path.join(DOSSIER_RACINE_STOCKAGE, client_id)
    if not os.path.exists(dossier_client):
        await websocket.close(code=1011, reason="Client introuvable")
        return
        
    print(f"[{NODE_ID}] Nouvelle connexion WSS (Download) pour : {client_id}")
    compteur_bloc = 0
    
    while True:
        chemin_fichier = os.path.join(dossier_client, f"bloc_{compteur_bloc}.dat")
        if os.path.exists(chemin_fichier):
            try:
                with open(chemin_fichier, "rb") as f:
                    donnees = f.read()
                await websocket.send(donnees)
                print(f"[{NODE_ID}] {client_id} -> Bloc {compteur_bloc} envoyé au visionneur.")
                
                # Attente d'un accusé de réception du backend
                ack = await websocket.recv()
                compteur_bloc += 1
            except websockets.exceptions.ConnectionClosed:
                print(f"[{NODE_ID}] Visionneur déconnecté.")
                break
            except Exception as e:
                print(f"[{NODE_ID}] Erreur de lecture/envoi: {e}")
                break
        else:
            # Le bloc n'existe pas encore (attente du direct ou fin du flux)
            await asyncio.sleep(0.5)

async def routeur_wss(websocket, path):
    parts = path.strip("/").split("/")
    if len(parts) >= 2:
        action = parts[0]
        client_id = parts[1]
    else:
        action = "upload"
        client_id = parts[0] if len(parts) > 0 and parts[0] else "Client_Inconnu"
        
    if action == "upload":
        await reception_flux_video(websocket, client_id)
    elif action == "download":
        await envoi_flux_video(websocket, client_id)
    else:
        await websocket.close(code=1011, reason="Action inconnue")

async def boucle_principale_wss():
    async with websockets.serve(routeur_wss, "0.0.0.0", PORT_WSS):
        print(f"[{NODE_ID}] Serveur WSS en écoute sur le port {PORT_WSS}...")
        await asyncio.Future()

# ==========================================
# 3. LANCEMENT PARALLÈLE
# ==========================================
if __name__ == "__main__":
    print(f"--- Démarrage du noeud Edge {NODE_ID} ---")
    
    # 1. On lance le MQTT sur une voie séparée (daemon=True le ferme si le script s'arrête)
    threading.Thread(target=thread_telemetrie_mqtt, daemon=True).start()
    
    # 2. On lance le serveur WSS sur la voie principale
    asyncio.run(boucle_principale_wss())
