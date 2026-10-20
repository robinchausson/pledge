"""
Test de transmission de flux client vers nœud Edge (WebSocket).
Issu de broker_et_client/test.py et adapté pour l'exécution automatisée de la suite de tests.
"""

import asyncio
import json
import pytest
import websockets


@pytest.mark.asyncio
async def test_envoyer_faux_flux_simulated():
    """Valide l'émission et la réception de blocs vidéo par un client diffuseur vers un serveur Edge simulé."""
    recus = []

    async def fake_edge_handler(websocket):
        async for msg in websocket:
            recus.append(msg)
            await websocket.send(json.dumps({"statut": "Bloc sécurisé et reçu"}))

    server = await websockets.serve(fake_edge_handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    uri = f"ws://127.0.0.1:{port}/Client_Bob"

    try:
        async with websockets.connect(uri) as ws:
            for i in range(3):
                faux_bloc = f"CONTENU_VIDEO_SECRET_BLOC_{i}".encode("utf-8")
                await ws.send(faux_bloc)
                rep = await ws.recv()
                data = json.loads(rep)
                assert data["statut"] == "Bloc sécurisé et reçu"
        assert len(recus) == 3
    finally:
        server.close()
        await server.wait_closed()

