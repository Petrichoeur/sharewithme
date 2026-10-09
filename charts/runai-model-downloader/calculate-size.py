# calculate-size.py
# Utilitaire local pour interroger l'API HuggingFace (ou miroir) et dimensionner le volume au juste prix
import os
import sys
import json
import math
import argparse
import urllib.request

def calculate_size(repo_id: str, endpoint: str = "https://huggingface.co", revision: str = "main", token: str = None, buffer_percent: int = 15):
    api_url = f"{endpoint.rstrip('/')}/api/models/{repo_id}/revision/{revision}"
    headers = {"User-Agent": "model-sizer/1.0"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    
    print(f"[*] Interrogation de l'API HuggingFace: {api_url}")
    req = urllib.request.Request(api_url, headers=headers)
    try:
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode())
    except Exception as e:
        print(f"[!] Erreur: {e}")
        sys.exit(1)

    siblings = data.get("siblings", [])
    total_bytes = 0
    missing_sizes = 0

    for item in siblings:
        rfilename = item.get("rfilename")
        size = item.get("size")
        if size is not None:
            total_bytes += int(size)
        else:
            # Fallback HEAD
            r_url = f"{endpoint.rstrip('/')}/{repo_id}/resolve/{revision}/{rfilename}"
            h_req = urllib.request.Request(r_url, headers=headers, method="HEAD")
            try:
                with urllib.request.urlopen(h_req) as h_resp:
                    clen = h_resp.headers.get("Content-Length")
                    if clen:
                        total_bytes += int(clen)
                    else:
                        missing_sizes += 1
            except:
                missing_sizes += 1

    gib_raw = total_bytes / (1024 ** 3)
    buffered_bytes = total_bytes * (1.0 + (buffer_percent / 100.0))
    pvc_gib = math.ceil(buffered_bytes / (1024 ** 3))

    print("\n" + "="*50)
    print(f" Modèle : {repo_id}")
    print(f" Taille brute des poids : {gib_raw:.2f} GiB ({total_bytes} bytes)")
    print(f" Marge de sécurité (+{buffer_percent}% FS journal/metadata) : {pvc_gib} GiB")
    print(f" => Taille recommandée pour le PVC : {pvc_gib}Gi")
    print("="*50 + "\n")
    return f"{pvc_gib}Gi"

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Calcule la taille optimale de PV pour un modèle HuggingFace")
    parser.add_argument("repo_id", help="Identifiant du modèle (ex: mistralai/Mistral-7B-v0.1)")
    parser.add_argument("--endpoint", default="https://huggingface.co", help="URL du miroir HF")
    parser.add_argument("--revision", default="main", help="Git revision / tag")
    parser.add_argument("--buffer", type=int, default=15, help="Pourcentage de buffer pour le FS")
    args = parser.parse_args()

    calculate_size(args.repo_id, args.endpoint, args.revision, os.environ.get("HF_TOKEN"), args.buffer)
