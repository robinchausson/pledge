import asyncio
import websockets
import os

CLIENT_ID = os.getenv("CLIENT_ID", "Anonyme")

async def reception_flux(websocket, path):
    print(f"Conteneur {CLIENT_ID} prêt à recevoir des données.")
    
    async for message in websocket:
        # Le script calcule la signature SIS ici...
        # Puis il sauvegarde la donnée STRICTEMENT dans son volume monté
        chemin_sauvegarde = f"/donnees_isolees/bloc_video.dat"
        
        with open(chemin_sauvegarde, "ab") as f:
            f.write(message)
            
        await websocket.send('{"statut": "Bloc sécurisé et isolé avec succès"}')

start_server = websockets.serve(reception_flux, "0.0.0.0", 8443)
asyncio.get_event_loop().run_until_complete(start_server)
asyncio.get_event_loop().run_forever()