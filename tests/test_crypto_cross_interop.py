"""
Test de validation croisée et d'interopérabilité mathématique :
Vérifie la conformité exacte entre le moteur cryptographique Python (crypto_engine.py)
et le client JavaScript frontend (cryptoClient.js) selon le papier scientifique Sellami et al. (FGCS 2024).
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path
import pytest

root_dir = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(root_dir / "serveur_crypto"))

from crypto_engine import (
    hash_h1_to_g1,
    compute_sis_vector,
    normalize,
    DEFAULT_PARAM_A,
)


def test_h1_hash_to_curve_interoperability():
    """Vérifie que H1(ID) produit exactement les mêmes coordonnées affines [x, y] en Python et JavaScript."""
    node_path = shutil.which("node")
    if not node_path:
        pytest.skip("Node.js n'est pas disponible pour ce test croisé")

    test_identities = [
        "alice@pledge.local",
        "bob@pledge.local",
        "Edge_Node_01",
        "streamer_mobility_zone_2",
    ]

    py_points = {}
    for ident in test_identities:
        pt = hash_h1_to_g1(ident)
        aff = normalize(pt)
        py_points[ident] = {"x": str(int(aff[0])), "y": str(int(aff[1]))}

    js_code = f"""
    import {{ hashH1ToG1 }} from './serveur_web/frontend/src/cryptoClient.js';
    const idents = {json.dumps(test_identities)};
    const res = {{}};
    for (const id of idents) {{
        const aff = hashH1ToG1(id).toAffine();
        res[id] = {{ x: aff.x.toString(), y: aff.y.toString() }};
    }}
    console.log(JSON.stringify(res));
    """

    proc = subprocess.run(
        [node_path, "--input-type=module", "-e", js_code],
        cwd=str(root_dir),
        capture_output=True,
        text=True,
        check=True,
    )
    js_points = json.loads(proc.stdout)

    for ident in test_identities:
        assert py_points[ident]["x"] == js_points[ident]["x"], f"Coordonnée x divergente pour {ident}"
        assert py_points[ident]["y"] == js_points[ident]["y"], f"Coordonnée y divergente pour {ident}"


def test_sis_vector_computation_interoperability():
    """Vérifie que le calcul matriciel SIS v_i = (A * x_i) mod q est identique entre Python et JavaScript."""
    node_path = shutil.which("node")
    if not node_path:
        pytest.skip("Node.js n'est pas disponible pour ce test croisé")

    sample_chunks = [
        b"TEST_CHUNK_PAYLOAD_001_WEBM",
        b"ANOTHER_RANDOM_VIDEO_FRAME_BYTES_42",
    ]

    for chunk in sample_chunks:
        py_v = compute_sis_vector(chunk, DEFAULT_PARAM_A)

        chunk_b64 = chunk.decode("latin1")
        js_code = f"""
        import {{ computeSisVector, DEFAULT_PARAM_A }} from './serveur_web/frontend/src/cryptoClient.js';
        const str = {json.dumps(chunk_b64)};
        const bytes = new Uint8Array(str.length);
        for (let i = 0; i < str.length; i++) bytes[i] = str.charCodeAt(i);
        const jsV = computeSisVector(bytes, DEFAULT_PARAM_A);
        console.log(JSON.stringify(jsV));
        """

        proc = subprocess.run(
            [node_path, "--input-type=module", "-e", js_code],
            cwd=str(root_dir),
            capture_output=True,
            text=True,
            check=True,
        )
        js_v = json.loads(proc.stdout)

        assert py_v == js_v, f"Divergence SIS pour le fragment {chunk}"

