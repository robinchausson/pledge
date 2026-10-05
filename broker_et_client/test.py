# test_stream.py
import asyncio
import websockets
import json

async def envoyer_faux_flux():
    # On simule un client diffuseur qui se connecte au noeud Edge
    uri = "ws://localhost:8443/Client_Bob" # Remplacer par l'IP client MQTT
    try:
        async with websockets.connect(uri) as websocket:
            print(f"Connexion WSS réussie sur {uri}")

            # Envoi de 3 faux blocs vidéo (des octets binaires)
            for i in range(3):
                faux_bloc = f"CONTENU_VIDEO_SECRET_BLOC_{i}".encode('utf-8')
                await websocket.send(faux_bloc)
                print(f"Bloc {i} envoyé.")

                reponse = await websocket.recv()
                print(f"Nœud Edge a répondu : {reponse}")
                await asyncio.sleep(1)

    except Exception as e:
        print(f"Erreur de connexion : {e}")

asyncio.run(envoyer_faux_flux())
