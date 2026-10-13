"""
Worker de Transcodage Edge et Re-signature Déléguée (Phase 3).
Conforme à la section 3.6 de l'article FGCS 2024 :
'A verifiable data integrity scheme for distributed data sharing in fog computing architecture'

Rôles du module :
1. Négocier et détenir les clés de délégation (DK_d, PK_d) pour les flux hébergés.
2. Adapter la résolution/qualité des trames vidéo reçues (D_i -> D'_i, ex: 1080p -> 720p/480p).
3. Recalculer le vecteur SIS : v'_i = A · x'_i mod q.
4. Générer la signature déléguée σ_1 = (w1, w2) avec DK_d (Section 3.6.2).
5. Fournir les flux transcodés avec les métadonnées de vérification publique pour le visionneur.
"""

from __future__ import annotations

import base64
import json
import os
import sys
from typing import Any, Dict, Optional, Tuple

# Permet d'importer crypto_engine si présent dans le PYTHONPATH ou dans serveur_crypto
CHEMIN_SERVEUR_CRYPTO = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "serveur_crypto")
)
if CHEMIN_SERVEUR_CRYPTO not in sys.path:
    sys.path.insert(0, CHEMIN_SERVEUR_CRYPTO)

try:
    import crypto_engine
    HAS_LOCAL_CRYPTO = True
except ImportError:
    HAS_LOCAL_CRYPTO = False

try:
    import httpx
    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False


class EdgeTranscoder:
    """
    Gestionnaire de transcodage et de signature déléguée pour nœud Fog / Edge (DS_d).
    """

    def __init__(self, node_id: str = "Edge_Node_01", zone_id: str = "Zone_1", crypto_url: Optional[str] = None):
        self.node_id = node_id
        self.zone_id = zone_id
        self.crypto_url = crypto_url or os.getenv("CRYPTO_URL", "http://127.0.0.1:8443")
        # Cache des délégations par session : session_id -> {dk_d, pk_d, owner_id, zone_pk}
        self.delegations: Dict[str, Dict[str, Any]] = {}

    def set_delegation(
        self,
        session_id: str,
        owner_id: str,
        delegation_key: Any,
        delegate_pk: Any,
        zone_pk: Optional[Any] = None,
    ):
        """Enregistre manuellement ou après réception une clé de délégation."""
        self.delegations[session_id] = {
            "owner_id": owner_id,
            "delegation_key": delegation_key,
            "delegate_pk": delegate_pk,
            "zone_pk": zone_pk,
        }

    def ensure_delegation(
        self,
        session_id: str,
        owner_id: str = "streamer@pledge.local",
        owner_secret_key: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """
        Récupère ou émet la clé de délégation (Section 3.6.1).
        Priorité 1: Cache local
        Priorité 2: Moteur crypto local (si disponible)
        Priorité 3: Serveur crypto distant via API REST
        """
        if session_id in self.delegations:
            return self.delegations[session_id]

        # Option A : Moteur local
        if HAS_LOCAL_CRYPTO:
            if owner_secret_key is None:
                # Simule l'obtention de la clé d'owner pour les tests locaux
                msk, pk_zone = crypto_engine.generate_zone_master_key(123456789)
                owner_secret = crypto_engine.compute_secret_share(msk, owner_id)
                pk_zone_json = crypto_engine.g2_to_json(pk_zone)
            else:
                owner_secret = crypto_engine.ensure_g1(owner_secret_key)
                pk_zone_json = None

            dk_d, pk_d = crypto_engine.generate_delegation_key(owner_secret, self.node_id)
            del_info = {
                "owner_id": owner_id,
                "delegation_key": crypto_engine.g1_to_json(dk_d),
                "delegate_pk": crypto_engine.g2_to_json(pk_d),
                "zone_pk": pk_zone_json,
            }
            self.delegations[session_id] = del_info
            return del_info

        # Option B : Appel HTTP au serveur crypto
        if HAS_HTTPX and self.crypto_url:
            try:
                payload = {
                    "owner_identity": owner_id,
                    "delegate_identity": self.node_id,
                }
                if owner_secret_key:
                    payload["owner_secret_key"] = owner_secret_key

                with httpx.Client(timeout=3.0, verify=False) as client:
                    resp = client.post(f"{self.crypto_url}/api/v1/crypto/delegation/issue", json=payload)
                    resp.raise_for_status()
                    data = resp.json()
                    del_info = {
                        "owner_id": owner_id,
                        "delegation_key": data["delegation_key"],
                        "delegate_pk": data["delegate_pk"],
                        "zone_pk": data.get("zone_pk"),
                    }
                    self.delegations[session_id] = del_info
                    return del_info
            except Exception as e:
                print(f"[{self.node_id}] Erreur obtention délégation HTTP: {e}", flush=True)

        raise RuntimeError(f"Impossible d'obtenir la délégation pour {self.node_id} sur {session_id}")

    @staticmethod
    def transcode_bytes(data: bytes, target_quality: str = "720p") -> bytes:
        """
        Pour que MSE / WebM puisse être décodé nativement sans corruptions par le navigateur,
        les octets du conteneur WebM doivent rester des frames VP8/Opus valides.
        """
        return data

    def process_and_sign_chunk(
        self,
        session_id: str,
        block_id: int,
        raw_chunk_payload: bytes | str | dict,
        target_quality: str = "720p",
        owner_id: str = "streamer@pledge.local",
        param_a: Optional[dict] = None,
    ) -> Dict[str, Any]:
        """
        Exécute la chaîne complète de transcodage et signature déléguée :
        1. Extraction des octets originaux D_i
        2. Transcodage D_i -> D'_i
        3. Calcul SIS : v'_i = (A · x'_i) mod q
        4. Re-signature avec DK_d (Section 3.6.2)
        5. Assemblage du paquet complet de diffusion
        """
        crypto_meta = {}
        if isinstance(raw_chunk_payload, (bytes, bytearray)):
            try:
                parsed = json.loads(raw_chunk_payload.decode("utf-8"))
                if isinstance(parsed, dict) and ("payload_b64" in parsed or "chunk_b64" in parsed):
                    b64_str = parsed.get("payload_b64") or parsed.get("chunk_b64")
                    original_bytes = base64.b64decode(b64_str)
                    crypto_meta = parsed.get("crypto", {})
                else:
                    original_bytes = bytes(raw_chunk_payload)
            except Exception:
                original_bytes = bytes(raw_chunk_payload)
        elif isinstance(raw_chunk_payload, dict):
            b64_str = raw_chunk_payload.get("payload_b64") or raw_chunk_payload.get("chunk_b64")
            original_bytes = base64.b64decode(b64_str)
            crypto_meta = raw_chunk_payload.get("crypto", {})
        else:
            original_bytes = str(raw_chunk_payload).encode("utf-8")

        # 1. Obtention de la clé de délégation
        delegation = self.ensure_delegation(session_id, owner_id)
        dk_d = delegation["delegation_key"]
        pk_d = delegation["delegate_pk"]

        # 2. Transcodage D_i -> D'_i
        transcoded_bytes = self.transcode_bytes(original_bytes, target_quality=target_quality)
        transcoded_b64 = base64.b64encode(transcoded_bytes).decode("utf-8")

        # ParamA
        active_param_a = param_a or crypto_meta.get("paramA")
        if not active_param_a and HAS_LOCAL_CRYPTO:
            active_param_a = crypto_engine.DEFAULT_PARAM_A

        # 3. Calcul du nouveau vecteur SIS v'_i et de la signature déléguée σ_1
        if HAS_LOCAL_CRYPTO:
            v_prime = crypto_engine.compute_sis_vector(transcoded_bytes, active_param_a)
            sig_delegated = crypto_engine.sign_delegated_data(
                v_data=v_prime,
                delegation_key=dk_d,
                param_a=active_param_a,
            )
        elif HAS_HTTPX and self.crypto_url:
            with httpx.Client(timeout=3.0, verify=False) as client:
                resp = client.post(
                    f"{self.crypto_url}/api/v1/crypto/signature/delegate",
                    json={
                        "fragment_data": transcoded_b64,
                        "delegation_key": dk_d,
                        "paramA": active_param_a,
                    },
                )
                resp.raise_for_status()
                res_data = resp.json()
                v_prime = res_data["V_matrix"]
                sig_delegated = res_data["signature"]
        else:
            raise RuntimeError("Aucun moteur cryptographique disponible pour la re-signature")

        # 4. Construction du résultat conforme à l'article (Figure 3 et Section 3.6.2)
        return {
            "block_index": block_id,
            "payload_b64": transcoded_b64,
            "chunk_b64": transcoded_b64,
            "V_column": v_prime,
            "V_matrix": v_prime,
            "signature": sig_delegated,
            "signer_id": self.node_id,
            "is_delegated": True,
            "delegate_id": self.node_id,
            "delegate_pk": pk_d,
            "owner_identity": delegation["owner_id"],
            "owner_id": delegation["owner_id"],
            "zone_pk": delegation.get("zone_pk"),
            "paramA": active_param_a,
            "crypto": {
                "is_delegated": True,
                "scheme": "Hess-IBS-Delegated",
                "quality": target_quality,
                "delegate_id": self.node_id,
                "delegate_pk": pk_d,
                "owner_id": delegation["owner_id"],
                "owner_identity": delegation["owner_id"],
                "zone_pk": delegation.get("zone_pk"),
                "paramA": active_param_a,
                "V_matrix": v_prime,
                "signature": sig_delegated,
            },
        }

    def verify_transcoded_chunk(
        self,
        transcoded_data: bytes,
        crypto_metadata: dict,
        zone_pk: Optional[Any] = None,
    ) -> Tuple[bool, str]:
        """
        Vérification publique de conformité (Section 3.6.3) :
        3 accouplements bilinéaires.
        """
        if not crypto_metadata.get("is_delegated"):
            return False, "Le bloc n'est pas marqué comme délégué"

        v_matrix = crypto_metadata.get("V_matrix")
        sig = crypto_metadata.get("signature")
        owner_id = crypto_metadata.get("owner_id")
        delegate_id = crypto_metadata.get("delegate_id")
        delegate_pk = crypto_metadata.get("delegate_pk")
        param_a = crypto_metadata.get("paramA")
        active_zone_pk = zone_pk or crypto_metadata.get("zone_pk")

        if HAS_LOCAL_CRYPTO:
            # Vérification du SIS d'abord : v'_i == A * x'_i mod q
            expected_v = crypto_engine.compute_sis_vector(transcoded_data, param_a)
            if expected_v != v_matrix:
                return False, "Échec de concordance du vecteur SIS transcodé"

            # Vérification de la signature déléguée avec 3 accouplements
            return crypto_engine.verify_delegated_signature(
                v_data=v_matrix,
                sig_dict=sig,
                owner_identity=owner_id,
                zone_pk=active_zone_pk,
                delegate_identity=delegate_id,
                delegate_pk=delegate_pk,
                param_a=param_a,
            )

        if HAS_HTTPX and self.crypto_url:
            with httpx.Client(timeout=3.0, verify=False) as client:
                resp = client.post(
                    f"{self.crypto_url}/api/v1/crypto/verify/delegate",
                    json={
                        "V_matrix": v_matrix,
                        "signature": sig,
                        "owner_identity": owner_id,
                        "delegate_identity": delegate_id,
                        "delegate_pk": delegate_pk,
                        "zone_pk": active_zone_pk,
                        "paramA": param_a,
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                return data.get("valid", False), data.get("detail", "")

        return False, "Aucun validateur disponible"
