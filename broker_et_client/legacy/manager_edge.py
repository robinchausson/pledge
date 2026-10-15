import paho.mqtt.client as mqtt
import docker
import json

client_mqtt = mqtt.Client(client_id="Manager_Edge_Julian")
docker_env = docker.from_env()

# Dictionnaire pour garder la trace des ports attribués
ports_alloues = 8000

def creer_conteneur_isole(client_id):
    global ports_alloues
    ports_alloues += 1
    port_wss_dedie = ports_alloues
    nom_volume = f"stockage_isole_{client_id}"
    nom_conteneur = f"edge_worker_{client_id}"

    # 1. Création du volume strictement dédié au client
    try:
        docker_env.volumes.get(nom_volume)
    except docker.errors.NotFound:
        docker_env.volumes.create(name=nom_volume)
        print(f"Volume {nom_volume} créé.")

    # 2. Lancement du micro-conteneur qui recevra le flux vidéo
    # Ce conteneur exécute l'image "worker_video" 
    try:
        conteneur = docker_env.containers.run(
            "worker_video:latest",
            name=nom_conteneur,
            detach=True,
            environment=[f"CLIENT_ID={client_id}"],
            ports={'8443/tcp': port_wss_dedie},
            volumes={nom_volume: {'bind': '/donnees_isolees', 'mode': 'rw'}}
        )
        print(f"[{client_id}] Conteneur isolé démarré sur le port {port_wss_dedie}")
        return port_wss_dedie
    except docker.errors.APIError as e:
        print(f"Erreur Docker : {e}")
        return None

def on_message(client, userdata, msg):
    # L'orchestrateur demande d'ouvrir un espace pour un client
    requete = json.loads(msg.payload)
    client_id = requete.get("client_id")
    
    print(f"Requête d'allocation reçue pour le client : {client_id}")
    
    # Création du conteneur à la volée
    nouveau_port = creer_conteneur_isole(client_id)
    
    if nouveau_port:
        # On renvoie à l'orchestrateur le port spécifique à transmettre au client
        reponse = {
            "client_id": client_id,
            "ip_p2p": "192.168.2.15", # IP de cette machine Edge
            "port_wss": nouveau_port,
            "statut": "conteneur_pret"
        }
        client_mqtt.publish("allocation/reponse", json.dumps(reponse))

client_mqtt.on_message = on_message
client_mqtt.connect("192.168.2.10", 1883)
client_mqtt.subscribe("allocation/demande")
client_mqtt.loop_forever()