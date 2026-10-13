import base64
import functools
import hashlib
import os
import secrets
import sys
from contextlib import asynccontextmanager

sys.setrecursionlimit(5000)

import numpy as np
from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel, Field
from py_ecc.bn128 import FQ, G1, G2, add, curve_order, multiply, pairing


SIS_L = 64
SIS_M = 1536
SIS_Q = 4099
PARAM_A = 42


class CryptoState:
    secret_scalar: int | None = None
    public_key: tuple | None = None
    secret_share: tuple | None = None


state = CryptoState()


def point_to_json(point: tuple) -> list[int]:
    return [int(coordinate.n) if hasattr(coordinate, "n") else int(coordinate) for coordinate in point]


def json_to_point(coords: list[int]) -> tuple:
    if len(coords) != 2:
        raise ValueError("Un point G1 doit comporter exactement 2 coordonnées")
    return (FQ(int(coords[0])), FQ(int(coords[1])))


@functools.lru_cache(maxsize=1)
def reconstruct_matrix_a(seed: int) -> np.ndarray:
    generator = np.random.default_rng(seed)
    return generator.integers(0, SIS_Q, size=(SIS_L, SIS_M), dtype=np.int64)


def hash_h3(data: bytes) -> list[int]:
    digest = hashlib.sha256(data).digest()
    return list((digest * ((SIS_M // len(digest)) + 1))[:SIS_M])


def compute_integrity(data: bytes) -> list[int]:
    vector = hash_h3(data)
    return np.mod(np.dot(reconstruct_matrix_a(PARAM_A), vector), SIS_Q).tolist()


def hash_h2(vector: list[int], param_a: int, pairing_value: object) -> int:
    message = f"{vector}{param_a}{pairing_value}".encode()
    return int(hashlib.sha256(message).hexdigest(), 16) % curve_order


def generate_signature(vector: list[int], param_a: int) -> dict:
    if state.secret_share is None:
        raise RuntimeError("La clé privée de zone n'est pas initialisée")

    # Génération réelle de signature Hess-IBS sur courbe BN128
    nonce = secrets.randbelow(curve_order - 1) + 1
    pairing_value = pairing(G2, multiply(G1, nonce))
    challenge = hash_h2(vector, param_a, pairing_value)
    
    # signature_point = (challenge * secret_key) + (nonce * G1)
    sig_term1 = multiply(state.secret_share, challenge)
    sig_term2 = multiply(G1, nonce)
    signature_point = add(sig_term1, sig_term2)
    
    return {
        "version": "1.0",
        "w1": int(challenge),
        "w2": point_to_json(signature_point),
        "p1": point_to_json(G1),
    }


def verify_signature_math(vector: list[int], param_a: int, sig_dict: dict) -> tuple[bool, str]:
    if state.public_key is None:
        return False, "Nœud crypto non initialisé"

    try:
        w1 = int(sig_dict["w1"])
        w2 = json_to_point(sig_dict["w2"])
        
        # Vérification par accouplement : R' = w2 - (w1 * P_pub)
        neg_w1 = (curve_order - (w1 % curve_order)) % curve_order
        r_point = add(w2, multiply(state.public_key, neg_w1))
        r_pairing = pairing(G2, r_point)
        
        expected_w1 = hash_h2(vector, param_a, r_pairing)
        if expected_w1 == w1:
            return True, "Signature Hess-IBS valide"
        return False, "Échec de correspondance du challenge cryptographique"
    except Exception as e:
        return False, f"Erreur de décodage/vérification : {e}"


def decode_fragment(fragment_data: str) -> bytes:
    try:
        return base64.b64decode(fragment_data, validate=True)
    except ValueError as error:
        raise HTTPException(status_code=422, detail="fragment_data doit être du Base64 valide") from error


@asynccontextmanager
async def lifespan(_: FastAPI):
    configured_scalar = os.environ.get("CRYPTO_SECRET_SCALAR")
    if configured_scalar:
        state.secret_scalar = int(configured_scalar)
    else:
        # Secret par défaut déterministe pour dev si non fourni, évite changement aléatoire
        state.secret_scalar = 123456789
        print("[crypto-node] AVERTISSEMENT : CRYPTO_SECRET_SCALAR non fourni, utilisation du secret par défaut.")

    if not 0 < state.secret_scalar < curve_order:
        raise RuntimeError("CRYPTO_SECRET_SCALAR doit être compris entre 1 et curve_order - 1")
        
    state.public_key = multiply(G1, state.secret_scalar)
    state.secret_share = state.public_key
    print(f"[crypto-node] Initialisé avec succès. Clé publique: {point_to_json(state.public_key)}")
    yield


app = FastAPI(title="Pledge Crypto Node", version="1.0.0", lifespan=lifespan)


class FragmentRequest(BaseModel):
    fragment_data: str = Field(..., description="Fragment encodé en Base64")
    param_a: int = Field(PARAM_A, alias="paramA")


class VerifyRequest(BaseModel):
    v_matrix: list[int] = Field(..., alias="V_matrix", description="Vecteur SIS V_matrix")
    param_a: int = Field(PARAM_A, alias="paramA")
    signature: dict = Field(..., description="Objet signature Hess-IBS {w1, w2, p1}")


@app.get("/")
def read_root() -> dict[str, str]:
    return {"service": "crypto-node", "status": "ok"}


@app.get("/health")
def health() -> dict:
    is_ready = state.public_key is not None
    return {
        "status": "ok" if is_ready else "not_ready",
        "initialized": is_ready,
        "curve": "bn128",
        "version": "1.0",
    }


@app.get("/api/v1/crypto/parameters")
def get_crypto_parameters() -> dict:
    if state.public_key is None:
        raise HTTPException(status_code=503, detail="Le nœud crypto n'est pas initialisé")
    return {
        "curve": "bn128",
        "public_key": point_to_json(state.public_key),
        "paramA": PARAM_A,
        "sis": {"l": SIS_L, "m": SIS_M, "q": SIS_Q, "hash": "SHA-256"},
        "signature": {"scheme": "Hess-IBS", "group": "G1", "version": "1.0"},
    }


@app.post("/api/v1/crypto/integrity")
def calculate_integrity(request: FragmentRequest) -> dict:
    fragment = decode_fragment(request.fragment_data)
    if request.param_a != PARAM_A:
        raise HTTPException(status_code=422, detail=f"paramA doit valoir {PARAM_A}")
    return {"paramA": PARAM_A, "V_matrix": compute_integrity(fragment)}


@app.post("/api/v1/crypto/signature")
def sign_fragment(request: FragmentRequest) -> dict:
    fragment = decode_fragment(request.fragment_data)
    if request.param_a != PARAM_A:
        raise HTTPException(status_code=422, detail=f"paramA doit valoir {PARAM_A}")
    vector = compute_integrity(fragment)
    return {"paramA": PARAM_A, "V_matrix": vector, "signature": generate_signature(vector, PARAM_A)}


@app.post("/api/v1/crypto/verify")
def verify_signature(request: VerifyRequest) -> dict:
    if request.param_a != PARAM_A:
        raise HTTPException(status_code=422, detail=f"paramA doit valoir {PARAM_A}")
    is_valid, reason = verify_signature_math(request.v_matrix, request.param_a, request.signature)
    return {"valid": is_valid, "detail": reason}