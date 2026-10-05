import paho.mqtt.client as mqtt
import json
import os
import datetime
from sqlalchemy.orm import Session
try:
    from .models import SessionLocal, EdgeNode, DataLocation
except ImportError:
    from models import SessionLocal, EdgeNode, DataLocation

# On récupère le broker par défaut ou via variable d'environnement
BROKER_IP = os.getenv("BROKER_IP", "10.4.19.234")
BROKER_PORT = int(os.getenv("BROKER_PORT", 1883))

def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print(f"[Backend MQTT] Connecté au Broker {BROKER_IP}:{BROKER_PORT}")
        # On s'abonne à toute la télémétrie
        client.subscribe("telemetrie/#")
    else:
        print(f"[Backend MQTT] Erreur de connexion (Code {rc})")

def on_message(client, userdata, msg):
    try:
        topic_parts = msg.topic.split("/")
        payload = json.loads(msg.payload.decode())
        
        db: Session = SessionLocal()
        
        # Route Globale: telemetrie/{ZONE_ID}/{NODE_ID}
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
            
        # Route Granulaire: telemetrie/{ZONE_ID}/{NODE_ID}/clients/{client_id}
        elif len(topic_parts) == 5 and topic_parts[3] == "clients":
            node_id = topic_parts[2]
            client_id = topic_parts[4]
            
            location = db.query(DataLocation).filter(
                DataLocation.node_id == node_id, 
                DataLocation.client_id == client_id
            ).first()
            
            if not location:
                location = DataLocation(node_id=node_id, client_id=client_id)
                db.add(location)
                
            location.blocks_count = payload.get("blocs_stockes")
            location.total_size_kb = payload.get("taille_ko")
            db.commit()
            
    except Exception as e:
        print(f"[Backend MQTT] Erreur parsing message: {e}")
    finally:
        if 'db' in locals():
            db.close()

def start_mqtt():
    client = mqtt.Client(client_id="Backend_Controller")
    client.on_connect = on_connect
    client.on_message = on_message
    
    try:
        client.connect(BROKER_IP, BROKER_PORT, 60)
        client.loop_start() # Démarre le thread réseau en arrière-plan
        return client
    except Exception as e:
        print(f"[Backend MQTT] Erreur fatale: {e}")
        return None
