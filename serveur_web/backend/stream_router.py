import json
import base64
import websockets
import httpx
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends
from sqlalchemy.orm import Session
try:
    from .models import get_db, EdgeNode, DataLocation
except ImportError:
    from models import get_db, EdgeNode, DataLocation
import os

router = APIRouter()

# Dictionnaire de configuration des serveurs crypto par zone
CRYPTO_SERVERS = {
    "Zone_1": os.getenv("CRYPTO_URL_ZONE_1", "https://127.0.0.1:8443"),
    "Zone_2": os.getenv("CRYPTO_URL_ZONE_2", "https://127.0.0.1:8444"),
}

async def get_crypto_signature(fragment_bytes: bytes, zone_id: str) -> dict:
    crypto_url = CRYPTO_SERVERS.get(zone_id)
    if not crypto_url:
        print(f"[{zone_id}] Aucun serveur crypto configuré.", flush=True)
        return {}

    fragment_b64 = base64.b64encode(fragment_bytes).decode("utf-8")
    payload = {"fragment_data": fragment_b64, "paramA": 42}
    
    # verify=False est nécessaire en dev avec le certificat autosigné du crypto node
    async with httpx.AsyncClient(verify=False, timeout=30.0) as client:
        try:
            response = await client.post(f"{crypto_url}/api/v1/crypto/signature", json=payload)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            print(f"Erreur appel Crypto Node {zone_id} ({crypto_url}): {e}", flush=True)
            return {}

@router.websocket("/api/stream/publish/{client_id}")
async def publish_stream(websocket: WebSocket, client_id: str, db: Session = Depends(get_db)):
    await websocket.accept()
    print(f"[Diffuseur] Nouvelle connexion WSS entrante : {client_id}")
    
    # 1. Stratégie de Handover / Routage (Phase 5)
    # Pour l'instant, on prend le noeud qui a le plus d'espace libre
    target_node = db.query(EdgeNode).order_by(EdgeNode.storage_available_gb.desc()).first()
    
    if not target_node:
        print("[Backend] Aucun noeud Edge disponible dans la DB.")
        await websocket.close(code=1011, reason="Aucun noeud Edge disponible")
        return
        
    print(f"[Backend] Routage du flux vers {target_node.node_id} (Zone: {target_node.zone_id}, IP: {target_node.ip_address})")
    
    # 2. Ouvrir une connexion WSS vers le nœud Edge
    # En dev, si le Edge n'a pas encore TLS, on utilise ws://
    edge_ws_url = f"ws://{target_node.ip_address}:{target_node.wss_port}/upload/{client_id}"
    
    try:
        async with websockets.connect(edge_ws_url, max_size=None, ping_timeout=120, ping_interval=60) as edge_ws:
            print(f"[Backend] Connecté avec succès au noeud Edge {target_node.node_id}")
            
            try:
                while True:
                    # Recevoir un chunk binaire du Frontend (Diffuseur)
                    chunk = await websocket.receive_bytes()
                    
                    # Interroger le serveur crypto de la zone pour signer le fragment
                    crypto_data = await get_crypto_signature(chunk, target_node.zone_id)
                    print(f"[DEBUG] get_crypto_signature a retourné : {list(crypto_data.keys())}", flush=True)
                    
                    # Construire le payload contenant les données brutes + signature
                    payload_to_store = {
                        "chunk_b64": base64.b64encode(chunk).decode("utf-8"),
                        "crypto": crypto_data
                    }
                    
                    # Transmettre au nœud Edge en le sérialisant en JSON encodé UTF-8
                    await edge_ws.send(json.dumps(payload_to_store).encode('utf-8'))
                    
                    # Le nœud Edge acquitte l'écriture (voir client_edge.py)
                    ack_bytes = await edge_ws.recv()
                    ack = json.loads(ack_bytes) if isinstance(ack_bytes, bytes) else json.loads(ack_bytes.encode())
                    
                    # Confirmer au Frontend que le bloc est sécurisé et stocké
                    await websocket.send_json({"status": "stored", "edge_node": target_node.node_id, "block": ack.get("bloc_id")})
                    
            except WebSocketDisconnect:
                print(f"[Diffuseur] Déconnexion propre de {client_id}")
    except Exception as e:
        print(f"[Backend] Erreur de distribution vers l'Edge: {e}")
        try:
            await websocket.close(code=1011, reason="Erreur de distribution vers le Edge")
        except:
            pass


async def verify_crypto_integrity(fragment_b64: str, zone_id: str, expected_v_matrix: list) -> bool:
    crypto_url = CRYPTO_SERVERS.get(zone_id)
    if not crypto_url:
        print(f"[{zone_id}] Aucun serveur crypto configuré.")
        return False
        
    payload = {"fragment_data": fragment_b64, "paramA": 42}
    
    async with httpx.AsyncClient(verify=False, timeout=30.0) as client:
        try:
            response = await client.post(f"{crypto_url}/api/v1/crypto/integrity", json=payload)
            response.raise_for_status()
            data = response.json()
            actual_v_matrix = data.get("V_matrix")
            if actual_v_matrix != expected_v_matrix:
                print(f"[DEBUG] V_matrix diffèrent ! Attendu ({len(expected_v_matrix) if expected_v_matrix else 0}) vs Reçu ({len(actual_v_matrix) if actual_v_matrix else 0})", flush=True)
                return False
            return True
        except Exception as e:
            print(f"Erreur vérification intégrité {zone_id}: {e}", flush=True)
            return False

@router.websocket("/api/stream/watch/{client_id}")
async def watch_stream(websocket: WebSocket, client_id: str, db: Session = Depends(get_db)):
    await websocket.accept()
    print(f"[Visionneur] Demande de lecture WSS pour : {client_id}")
    
    # 1. Localisation des données
    location = db.query(DataLocation).filter(DataLocation.client_id == client_id).first()
    if not location:
        print(f"[Backend] Flux {client_id} introuvable dans la DB.")
        await websocket.close(code=4004, reason="Flux introuvable")
        return
        
    source_node = db.query(EdgeNode).filter(EdgeNode.node_id == location.node_id).first()
    if not source_node:
        print("[Backend] Noeud Edge source introuvable.")
        await websocket.close(code=1011, reason="Noeud Edge source introuvable")
        return
        
    # 2. Récupération depuis le Edge
    edge_ws_url = f"ws://{source_node.ip_address}:{source_node.wss_port}/download/{client_id}"
    
    try:
        async with websockets.connect(edge_ws_url, max_size=None, ping_timeout=120, ping_interval=60) as edge_ws:
            print(f"[Backend] Connecté au noeud Edge {source_node.node_id} pour lecture")
            
            try:
                while True:
                    bloc_bytes = await edge_ws.recv()
                    
                    try:
                        bloc_data = json.loads(bloc_bytes)
                        chunk_b64 = bloc_data.get("chunk_b64")
                        crypto_data = bloc_data.get("crypto", {})
                        expected_v_matrix = crypto_data.get("V_matrix", [])
                        
                        # 3. Vérification de l'intégrité (SIS) via le Serveur Crypto
                        is_valid = await verify_crypto_integrity(chunk_b64, source_node.zone_id, expected_v_matrix)
                        
                        if is_valid:
                            # 4. Envoi au Visionneur si la vérification passe
                            chunk_bytes = base64.b64decode(chunk_b64)
                            await websocket.send_bytes(chunk_bytes)
                            
                            # Acquittement au noeud Edge pour recevoir le bloc suivant
                            await edge_ws.send(json.dumps({"statut": "ok"}))
                        else:
                            print(f"[Backend] ALERTE INTÉGRITÉ ! Fragment corrompu pour {client_id}")
                            await websocket.close(code=1011, reason="Erreur d'intégrité crypto SIS")
                            break
                    except json.JSONDecodeError:
                        print("[Backend] Format de bloc invalide reçu depuis l'Edge")
                        break
            except websockets.exceptions.ConnectionClosed:
                print(f"[Backend] Fin de lecture du flux depuis l'Edge")
            except WebSocketDisconnect:
                print(f"[Visionneur] Utilisateur déconnecté")
    except Exception as e:
        print(f"[Backend] Erreur de récupération depuis l'Edge: {e}")
        try:
            await websocket.close(code=1011, reason="Erreur de lecture depuis l'Edge")
        except:
            pass
