#!/usr/bin/env python3
"""
pv-to-datavolume.py

Enregistre une liste de PVCs existants sur le cluster en tant que Data Volumes Run:ai (v2.23+).
Ne recrée AUCUN volume : utilise `useOriginalVolume: true` pour référencer directement les PVCs existants.
"""

import os
import sys
import json
import ssl
import re
import argparse
import subprocess
import urllib.request
import urllib.parse


def sanitize_volume_name(name: str) -> str:
    """Respecte la contrainte Run:ai : ^[a-z][-a-z0-9]{0,62}$"""
    clean = re.sub(r'[^a-zA-Z0-9-]', '-', name.lower()).strip('-')
    if not clean:
        clean = "datavolume"
    if not clean[0].isalpha():
        clean = f"dv-{clean}"
    return clean[:63].rstrip('-')


class RunaiClient:
    def __init__(self, base_url: str, client_id: str, client_secret: str, ca_cert: str = None):
        self.base_url = base_url.rstrip("/")
        self.client_id = client_id
        self.client_secret = client_secret
        self.ctx = ssl.create_default_context()
        ca = ca_cert or os.environ.get("REQUESTS_CA_BUNDLE") or os.environ.get("SSL_CERT_FILE")
        if ca and os.path.exists(ca):
            self.ctx.load_verify_locations(cafile=ca)
        self.token = None

    def authenticate(self) -> str:
        # 1. Standard Run:ai 2.x : POST /api/v1/token (JSON)
        try:
            req = urllib.request.Request(
                f"{self.base_url}/api/v1/token",
                data=json.dumps({
                    "grantType": "client_credentials",
                    "clientId": self.client_id,
                    "clientSecret": self.client_secret
                }).encode("utf-8"),
                headers={"Content-Type": "application/json", "Accept": "application/json"},
                method="POST"
            )
            with urllib.request.urlopen(req, context=self.ctx, timeout=15) as resp:
                data = json.loads(resp.read().decode())
                self.token = data.get("accessToken") or data.get("access_token")
                if self.token:
                    return self.token
        except Exception:
            pass

        # 2. OAuth2 v2 : POST /api/v2/token (x-www-form-urlencoded)
        req = urllib.request.Request(
            f"{self.base_url}/api/v2/token",
            data=urllib.parse.urlencode({
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret
            }).encode("utf-8"),
            headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req, context=self.ctx, timeout=15) as resp:
            data = json.loads(resp.read().decode())
            self.token = data.get("access_token") or data.get("accessToken")
            if not self.token:
                raise RuntimeError("Token vide dans la réponse")
            return self.token

    def _headers(self) -> dict:
        if not self.token:
            self.authenticate()
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "Accept": "application/json"
        }

    def resolve_project(self, project_name: str, cluster_uuid: str = None) -> tuple:
        """Retourne (project_id, cluster_id) à partir du nom du projet."""
        q = urllib.parse.urlencode({"filterBy": f"name=={project_name}"})
        endpoints = [
            f"{self.base_url}/api/v1/org-unit/projects?{q}",
            f"{self.base_url}/api/v1/projects?{q}"
        ]
        if cluster_uuid:
            endpoints.append(f"{self.base_url}/v1/k8s/clusters/{cluster_uuid}/projects")

        for ep in endpoints:
            try:
                req = urllib.request.Request(ep, headers=self._headers())
                with urllib.request.urlopen(req, context=self.ctx, timeout=15) as resp:
                    data = json.loads(resp.read().decode())
                    projects = data.get("projects", data if isinstance(data, list) else [])
                    for p in projects:
                        if p.get("name") == project_name:
                            return str(p.get("id")), str(p.get("clusterId") or cluster_uuid or "")
            except Exception:
                continue

        raise RuntimeError(f"Projet '{project_name}' introuvable via l'API Run:ai. Utilisez --project-id.")

    def register_datavolume(self, name: str, origin_pvc: str, project_id: str, description: str = "") -> dict:
        payload = {
            "name": name,
            "description": (description or f"PVC {origin_pvc}")[:127],
            "originPvcName": origin_pvc,
            "projectId": str(project_id),
            "useOriginalVolume": True
        }
        req = urllib.request.Request(
            f"{self.base_url}/api/v1/datavolumes",
            data=json.dumps(payload).encode("utf-8"),
            headers=self._headers(),
            method="POST"
        )
        try:
            with urllib.request.urlopen(req, context=self.ctx, timeout=15) as resp:
                return {"status": "registered", "code": resp.status, "data": json.loads(resp.read().decode())}
        except urllib.error.HTTPError as he:
            err_body = he.read().decode()
            if he.code == 409 or "already exists" in err_body.lower():
                return {"status": "exists", "code": 409}
            return {"status": "error", "code": he.code, "message": err_body}
        except Exception as e:
            return {"status": "error", "code": 500, "message": str(e)}

    def share_datavolume(self, dv_id: str, scopes: list) -> bool:
        req = urllib.request.Request(
            f"{self.base_url}/api/v1/datavolumes/{dv_id}/sharedScopes",
            data=json.dumps({"add": scopes}).encode("utf-8"),
            headers=self._headers(),
            method="PATCH"
        )
        with urllib.request.urlopen(req, context=self.ctx, timeout=15):
            return True


def discover_bound_pvcs(namespace: str) -> list:
    """Liste uniquement les PVCs en état 'Bound' dans le namespace via oc ou kubectl."""
    cli = "oc" if subprocess.call(["which", "oc"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) == 0 else "kubectl"
    cmd = [cli, "get", "pvc", "-n", namespace, "-o", "json"]
    try:
        out = subprocess.check_output(cmd, stderr=subprocess.PIPE, text=True)
        items = json.loads(out).get("items", [])
        bound = []
        for item in items:
            name = item.get("metadata", {}).get("name", "")
            phase = item.get("status", {}).get("phase", "")
            labels = item.get("metadata", {}).get("labels", {})
            # Ignore les PVCs qui sont déjà des copies créées par un autre DataVolume
            if name and phase == "Bound" and "run.ai/datavolume-id" not in labels:
                bound.append(name)
        return bound
    except Exception as e:
        print(f"[ERROR] Échec {cli} get pvc -n {namespace} : {e}", file=sys.stderr)
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="Enregistre des PVCs existants en tant que Data Volumes Run:ai (v2.23+).")
    parser.add_argument("--runai-url", default=os.environ.get("RUNAI_BASE_URL"), required=not os.environ.get("RUNAI_BASE_URL"),
                        help="URL du Control Plane Run:ai")
    parser.add_argument("--client-id", default=os.environ.get("RUNAI_CLIENT_ID"), required=not os.environ.get("RUNAI_CLIENT_ID"),
                        help="Client ID Run:ai")
    parser.add_argument("--client-secret", default=os.environ.get("RUNAI_CLIENT_SECRET"), required=not os.environ.get("RUNAI_CLIENT_SECRET"),
                        help="Client Secret Run:ai")
    parser.add_argument("--project-name", default=os.environ.get("RUNAI_PROJECT_NAME"),
                        help="Nom du projet Run:ai où résident les PVCs")
    parser.add_argument("--project-id", default=os.environ.get("RUNAI_PROJECT_ID"),
                        help="ID du projet Run:ai (évite la résolution par nom)")
    parser.add_argument("--cluster-uuid", default=os.environ.get("RUNAI_CLUSTER_UUID"),
                        help="UUID du cluster (requis uniquement si --share-cluster sans résolution projet)")
    parser.add_argument("--ca-cert", default=os.environ.get("RUNAI_CA_CERT"),
                        help="Certificat CA racine")
    parser.add_argument("--pvcs", help="Liste de PVCs existants séparés par des virgules (ex: pvc-1,pvc-2)")
    parser.add_argument("--file", help="Fichier texte contenant un nom de PVC par ligne")
    parser.add_argument("--namespace", help="Namespace Kubernetes/OpenShift pour auto-discovery des PVCs Bound")
    parser.add_argument("--share-cluster", action="store_true", help="Partage automatiquement chaque Data Volume avec tout le cluster")
    parser.add_argument("--prefix", default="", help="Préfixe pour le nom du Data Volume")
    parser.add_argument("--dry-run", action="store_true", help="Affiche les appels API sans les exécuter")

    args = parser.parse_args()

    if not args.project_name and not args.project_id:
        sys.exit("[ERROR] Vous devez fournir --project-name ou --project-id.")

    pvcs = []
    if args.pvcs:
        pvcs.extend([p.strip() for p in args.pvcs.split(",") if p.strip()])
    if args.file:
        with open(args.file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and line not in pvcs:
                    pvcs.append(line)
    if args.namespace:
        for p in discover_bound_pvcs(args.namespace):
            if p not in pvcs:
                pvcs.append(p)

    if not pvcs:
        sys.exit("[ERROR] Aucun PVC fourni. Utilisez --pvcs, --file ou --namespace.")

    client = RunaiClient(args.runai_url, args.client_id, args.client_secret, args.ca_cert)

    project_id = args.project_id
    cluster_uuid = args.cluster_uuid

    if not args.dry_run:
        try:
            client.authenticate()
            if not project_id:
                project_id, resolved_cluster = client.resolve_project(args.project_name, cluster_uuid)
                if not cluster_uuid:
                    cluster_uuid = resolved_cluster
        except Exception as e:
            sys.exit(f"[ERROR] Initialisation Run:ai échouée : {e}")
    else:
        project_id = project_id or f"<id:{args.project_name}>"

    ok, skip, err = 0, 0, 0
    for pvc in pvcs:
        dv_name = sanitize_volume_name(f"{args.prefix}{pvc}")
        if args.dry_run:
            print(f"[DRY-RUN] Enregistrement PVC '{pvc}' -> DataVolume '{dv_name}' (projectId={project_id}, useOriginalVolume=True)")
            ok += 1
            continue

        res = client.register_datavolume(dv_name, pvc, project_id)
        if res["status"] == "registered":
            dv_id = res["data"].get("id")
            print(f"[OK] PVC '{pvc}' enregistré en DataVolume '{dv_name}' (id={dv_id})")
            if args.share_cluster and cluster_uuid and dv_id:
                try:
                    client.share_datavolume(dv_id, [{"scopeType": "cluster", "scopeId": cluster_uuid}])
                    print(f"     -> Partagé avec le cluster {cluster_uuid}")
                except Exception as e:
                    print(f"     [WARN] Échec partage cluster : {e}", file=sys.stderr)
            ok += 1
        elif res["status"] == "exists":
            print(f"[SKIP] PVC '{pvc}' ('{dv_name}') déjà enregistré")
            skip += 1
        else:
            print(f"[ERR] PVC '{pvc}' -> HTTP {res['code']}: {res.get('message')}", file=sys.stderr)
            err += 1

    print(f"\nTerminé : {ok} enregistré(s), {skip} existant(s), {err} erreur(s).")
    sys.exit(1 if err > 0 else 0)


if __name__ == "__main__":
    main()
