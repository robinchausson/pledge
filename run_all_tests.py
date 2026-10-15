#!/usr/bin/env python3
"""
Master Test Suite Runner - Projet PLEDGE
Exécute l'intégralité de la batterie de tests du projet :
  1. Tests Unitaires & Intégration Backend (FastAPI, Auth, Signalisation, MQTT, Admin)
  2. Tests Cryptographiques (SIS L'Ecuyer, Hess-IBS, Mobilité, Opérations Dynamiques)
  3. Tests Nœud Fog Edge (WebSocket direct, Transcodage 720p, Re-signature déléguée)
  4. Tests d'Interopérabilité Croisée Python <-> JavaScript (H1 Hash-to-curve, SIS)
  5. Tests de Bout en Bout Système (E2E Fog Streaming P2P, Audit public, Résistance aux attaques)
  6. Banc d'Essai Expérimental & Benchmarks (Conformité papier Sellami et al., FGCS 2024)
  7. Tests Unitaires Frontend WebAssembly / JavaScript (@noble/curves, cryptoClient.js)
"""

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path


ROOT_DIR = Path(__file__).parent.resolve()
FRONTEND_DIR = ROOT_DIR / "serveur_web" / "frontend"


def print_banner():
    banner = """
================================================================================
                    BATTERIE DE TESTS GLOBALE - PROJET PLEDGE
     Architecture Fog Décentralisée & Schéma d'Intégrité Vérifiable (FGCS 2024)
================================================================================
"""
    print(banner)


def run_command_stream(cmd, cwd=None, description=""):
    print(f"\n>>> Exécution : {description}")
    print(f"    Commande : {' '.join(cmd)}")
    print(f"    Répertoire : {cwd or ROOT_DIR}")
    print("-" * 80)
    t0 = time.perf_counter()
    proc = subprocess.run(cmd, cwd=str(cwd or ROOT_DIR), text=True)
    t1 = time.perf_counter()
    duration_sec = t1 - t0
    success = proc.returncode == 0
    status_str = "SUCCÈS" if success else "ÉCHEC"
    print("-" * 80)
    print(f"[{status_str}] Durée : {duration_sec:.2f} s | Code retour : {proc.returncode}\n")
    return success, duration_sec


def main():
    parser = argparse.ArgumentParser(description="Batterie de tests globale Pledge")
    parser.add_argument("--pytest-only", action="store_true", help="N'exécute que la suite pytest Python")
    parser.add_argument("--frontend-only", action="store_true", help="N'exécute que la suite JavaScript Frontend")
    parser.add_argument("-v", "--verbose", action="store_true", help="Mode verbeux")
    parser.add_argument("-k", "--filter", type=str, default="", help="Filtre d'expression de test pour pytest")
    args = parser.parse_args()

    print_banner()

    start_total_time = time.perf_counter()
    results = []

    # 1. Suite Pytest
    if not args.frontend_only:
        pytest_cmd = [sys.executable, "-m", "pytest"]
        if args.verbose:
            pytest_cmd.append("-v")
        else:
            pytest_cmd.append("-v")
        if args.filter:
            pytest_cmd.extend(["-k", args.filter])

        success_pytest, dur_pytest = run_command_stream(
            pytest_cmd,
            cwd=ROOT_DIR,
            description="Suite Pytest Complète (Backend, Crypto, Fog Edge, E2E, Benchmarks)",
        )
        results.append(("Suite Complète Pytest (Python)", success_pytest, dur_pytest))

    # 2. Suite Frontend Node.js
    if not args.pytest_only:
        node_path = shutil.which("node")
        if node_path:
            npm_path = shutil.which("npm") or shutil.which("npm.cmd")
            if npm_path and (FRONTEND_DIR / "package.json").exists():
                success_js, dur_js = run_command_stream(
                    [npm_path, "test"],
                    cwd=FRONTEND_DIR,
                    description="Suite Frontend Crypto Client (Node.js & @noble/curves)",
                )
            else:
                success_js, dur_js = run_command_stream(
                    [node_path, "test_cryptoClient.js"],
                    cwd=FRONTEND_DIR,
                    description="Suite Frontend Crypto Client (Node.js direct)",
                )
            results.append(("Suite Crypto Frontend (JavaScript)", success_js, dur_js))
        else:
            print("[AVERTISSEMENT] Node.js non trouvé sur le système : suite frontend ignorée.")

    total_time = time.perf_counter() - start_total_time

    # Bilan récapitulatif
    print("\n" + "=" * 80)
    print("                      BILAN GLOBAL D'EXÉCUTION DES TESTS")
    print("=" * 80)
    all_passed = True
    for name, success, dur in results:
        status_badge = "[OK] PASSED" if success else "[!!] FAILED"
        if not success:
            all_passed = False
        print(f" {status_badge:<14} | {name:<50} | {dur:>6.2f} s")
    print("-" * 80)
    print(f" Durée Totale : {total_time:.2f} s")

    if all_passed:
        print("\n>>> RÉSULTAT FINAL : TOUTES LES SUITES DE TEST ONT RÉUSSI (100% SUCCÈS) <<<\n")
        sys.exit(0)
    else:
        print("\n>>> RÉSULTAT FINAL : DES TESTS ONT ÉCHOUÉ <<<\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
