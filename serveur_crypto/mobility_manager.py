"""
Gestionnaire de Mobilité Multi-Zone pour Diffuseur (Data Owner Ow)
Conforme à l'article scientifique :
'A verifiable data integrity scheme for distributed data sharing in fog computing architecture'
(Sellami, Imine, Gallais - Future Generation Computer Systems 150, 2024, pp. 64-77).

Section 3.4.2 & Section 3.4.3 :
- Initialisation dans la première zone s_1 : S_w = S_w^1, PK = PK_1
- Mobilité vers la zone s_k : S_w <- S_w + S_w^k, PK <- PK + PK_k
- Maintien de la validité des signatures et non-révocation des contenus précédents
"""

from __future__ import annotations

import base64
from typing import Any, Dict, List, Optional, Tuple

import requests
import crypto_engine as engine


class ZoneInfo:
    def __init__(self, zone_id: str, zone_pk: tuple, secret_share: tuple):
        self.zone_id = zone_id
        self.zone_pk = zone_pk  # Point G2
        self.secret_share = secret_share  # Point G1

    def to_dict(self) -> dict:
        return {
            "zone_id": self.zone_id,
            "zone_pk": engine.g2_to_json(self.zone_pk),
            "secret_share": engine.g1_to_json(self.secret_share),
        }


class StreamerMobilityClient:
    """
    Client de gestion d'identité et de mobilité multi-zone pour le diffuseur (Data Owner Ow).
    """

    def __init__(self, identity: str):
        self.identity = identity.strip()
        self.visited_zones: Dict[str, ZoneInfo] = {}
        self.accumulated_secret_share: Optional[tuple] = None  # S_w in G1
        self.accumulated_public_key: Optional[tuple] = None    # PK in G2

    @property
    def current_secret_key(self) -> Optional[tuple]:
        return self.accumulated_secret_share

    @property
    def current_public_key(self) -> Optional[tuple]:
        return self.accumulated_public_key

    def add_zone_credentials(
        self,
        zone_id: str,
        secret_share: tuple | List[int],
        zone_pk: tuple | List[List[int]],
    ) -> None:
        """
        Enregistre la part secrète S_w^s délivrée par le serveur IS_s et la clé publique PK_s.
        Met à jour de manière homomorphe la clé secrète et la clé publique globale.
        """
        share_pt = engine.ensure_g1(secret_share)
        pk_pt = engine.ensure_g2(zone_pk)

        if zone_id in self.visited_zones:
            # Zone déjà enregistrée, pas de double accumulation
            return

        zone_info = ZoneInfo(zone_id, pk_pt, share_pt)
        self.visited_zones[zone_id] = zone_info

        # Accumulation homomorphe : S_w <- sum(S_w^s), PK <- sum(PK_s)
        if self.accumulated_secret_share is None:
            self.accumulated_secret_share = share_pt
            self.accumulated_public_key = pk_pt
        else:
            self.accumulated_secret_share = engine.add(self.accumulated_secret_share, share_pt)
            self.accumulated_public_key = engine.add(self.accumulated_public_key, pk_pt)

    def join_zone_via_api(self, is_url: str, session: Optional[requests.Session] = None) -> dict:
        """
        Interroge directement le Serveur d'Identification IS_s de la zone cible
        via l'endpoint conforme `POST /api/v1/auth/identify`.
        """
        client = session or requests
        url = f"{is_url.rstrip('/')}/api/v1/auth/identify"
        payload = {"identity": self.identity}

        response = client.post(url, json=payload, timeout=5.0)
        response.raise_for_status()
        data = response.json()

        zone_id = data.get("zone_id", f"Zone_{len(self.visited_zones) + 1}")
        secret_share = data["secret_share"]
        zone_pk = data["zone_pk"]

        self.add_zone_credentials(zone_id, secret_share, zone_pk)
        return {
            "identity": self.identity,
            "zone_id": zone_id,
            "total_zones": len(self.visited_zones),
            "accumulated_pk": engine.g2_to_json(self.accumulated_public_key),
        }

    def sign_block(
        self,
        data: bytes,
        param_a: dict | None = None,
        use_zone_only: Optional[str] = None,
    ) -> dict:
        """
        Calcule la preuve SIS v_i et signe avec la clé secrète accumulée (ou celle d'une zone spécifique).
        """
        if not self.visited_zones or self.accumulated_secret_share is None:
            raise RuntimeError("Le diffuseur ne possède aucune clé de zone active.")

        param = engine.parse_param_a(param_a)
        v_vector = engine.compute_sis_vector(data, param_a)

        if use_zone_only:
            if use_zone_only not in self.visited_zones:
                raise ValueError(f"Zone {use_zone_only} inconnue du client.")
            signing_key = self.visited_zones[use_zone_only].secret_share
            verifying_pk = self.visited_zones[use_zone_only].zone_pk
            zones_scope = [use_zone_only]
        else:
            signing_key = self.accumulated_secret_share
            verifying_pk = self.accumulated_public_key
            zones_scope = list(self.visited_zones.keys())

        signature = engine.sign_data(v_vector, signing_key, param_a)

        return {
            "signer_id": self.identity,
            "zones_scope": zones_scope,
            "paramA": {"I0": param[0], "a": param[1], "C0": param[2]},
            "V_matrix": v_vector,
            "signature": signature,
            "verifying_pk": engine.g2_to_json(verifying_pk),
        }

    def get_public_credentials(self) -> dict:
        """Exporte l'état public vérifiable par n'importe quel consommateur / spectateur."""
        if self.accumulated_public_key is None:
            raise RuntimeError("Aucune clé publique initialisée.")
        return {
            "identity": self.identity,
            "accumulated_pk": engine.g2_to_json(self.accumulated_public_key),
            "zones": {
                z_id: engine.g2_to_json(z.zone_pk)
                for z_id, z in self.visited_zones.items()
            },
        }

    @staticmethod
    def verify_block_package(
        block_package: dict,
        data: Optional[bytes] = None,
        override_pk: Optional[tuple | List[List[int]]] = None,
    ) -> Tuple[bool, str]:
        """
        Vérification publique complète d'un bloc sans TPA :
        1. Si les données brutes sont fournies, vérifie que V correspond bien aux données via SIS.
        2. Vérifie la signature Hess-IBS contre la clé publique accumulée.
        """
        param_a = block_package.get("paramA")
        v_vector = block_package.get("V_matrix")
        sig = block_package.get("signature")
        signer_id = block_package.get("signer_id")

        if data is not None:
            recomputed_v = engine.compute_sis_vector(data, param_a)
            if recomputed_v != v_vector:
                return False, "Échec d'intégrité SIS : le vecteur V ne correspond pas aux données"

        if override_pk is not None:
            pk = engine.ensure_g2(override_pk)
        elif "verifying_pk" in block_package:
            pk = engine.ensure_g2(block_package["verifying_pk"])
        else:
            return False, "Clé publique de vérification introuvable dans le paquet"

        return engine.verify_signature(
            v_data=v_vector,
            sig_dict=sig,
            owner_identity=signer_id,
            zone_pk=pk,
            param_a=param_a,
        )
