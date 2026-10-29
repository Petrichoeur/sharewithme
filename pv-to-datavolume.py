#!/usr/bin/env python3
"""
Enregistre une liste de PVCs existants en tant que Data Volumes Run:ai (API v2.23)
sans recréer de volumes (useOriginalVolume=True).
"""

import os
import sys
import re
import json
import argparse
import urllib.request
import urllib.parse


def http_json(url, method="GET", data=None, headers=None):
    body = json.dumps(data).encode("utf-8") if data is not None else None
    req = urllib.request.Request(url, data=body, headers=headers or {}, method=method)
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def clean_name(name):
    s = re.sub(r"[^a-z0-9-]", "-", name.lower()).strip("-")
    return (s if s and s[0].isalpha() else f"dv-{s}")[:63].rstrip("-")


def main():
    parser = argparse.ArgumentParser(description="Enregistre des PVCs existants en Data Volumes Run:ai.")
    parser.add_argument("--url", default=os.environ.get("RUNAI_URL"), required=not os.environ.get("RUNAI_URL"), help="URL Run:ai")
    parser.add_argument("--client-id", default=os.environ.get("RUNAI_CLIENT_ID"), required=not os.environ.get("RUNAI_CLIENT_ID"))
    parser.add_argument("--client-secret", default=os.environ.get("RUNAI_CLIENT_SECRET"), required=not os.environ.get("RUNAI_CLIENT_SECRET"))
    parser.add_argument("--project", required=True, help="Nom du projet Run:ai où se trouvent les PVCs")
    parser.add_argument("--pvcs", required=True, help="Noms des PVCs séparés par des virgules (ex: pvc-1,pvc-2)")
    args = parser.parse_args()

    base_url = args.url.rstrip("/")
    pvc_list = [p.strip() for p in args.pvcs.split(",") if p.strip()]

    # 1. Token Run:ai
    token_resp = http_json(
        f"{base_url}/api/v1/token",
        method="POST",
        data={"grantType": "client_credentials", "clientId": args.client_id, "clientSecret": args.client_secret},
        headers={"Content-Type": "application/json"}
    )
    token = token_resp.get("accessToken") or token_resp.get("access_token")
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    # 2. Résolution du projectId
    q = urllib.parse.urlencode({"filterBy": f"name=={args.project}"})
    projects = http_json(f"{base_url}/api/v1/org-unit/projects?{q}", headers=headers).get("projects", [])
    project_id = next((str(p["id"]) for p in projects if p.get("name") == args.project), None)
    if not project_id:
        sys.exit(f"Erreur : projet '{args.project}' introuvable.")

    # 3. Enregistrement de chaque PVC
    for pvc in pvc_list:
        dv_name = clean_name(pvc)
        payload = {
            "name": dv_name,
            "description": f"PVC {pvc}",
            "originPvcName": pvc,
            "projectId": project_id,
            "useOriginalVolume": True
        }
        try:
            http_json(f"{base_url}/api/v1/datavolumes", method="POST", data=payload, headers=headers)
            print(f"[OK] {pvc} -> DataVolume '{dv_name}' enregistré.")
        except urllib.error.HTTPError as e:
            if e.code == 409:
                print(f"[SKIP] {pvc} -> déjà enregistré.")
            else:
                print(f"[ERREUR] {pvc} -> HTTP {e.code}: {e.read().decode()}", file=sys.stderr)


if __name__ == "__main__":
    main()
