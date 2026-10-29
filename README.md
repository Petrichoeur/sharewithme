# sharewithme

Charts Helm simples (zéro `_helpers.tpl`, scripts lisibles en `ConfigMap`) et outil API pour **Run:ai 2.23** sur OpenShift :

1. **[`charts/postgres-backup`](charts/postgres-backup)** : `CronJob` de sauvegarde PostgreSQL (`pg_dump -Fc`) vers S3 avec rétention.
2. **[`charts/postgres-restore`](charts/postgres-restore)** : `Job` de restauration PostgreSQL depuis S3 (séparé du backup).
3. **[`charts/runai-model-downloader`](charts/runai-model-downloader)** : Télécharge un modèle depuis un miroir HF vers un PVC et l'enregistre en Data Volume Run:ai.
4. **[`pv-to-datavolume.py`](pv-to-datavolume.py)** : Enregistre une liste de PVCs existants en Data Volumes via l'API Run:ai (`useOriginalVolume: true`).

---

## 1. `charts/postgres-backup`

```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: postgres-backup
  namespace: openshift-gitops
spec:
  project: default
  source:
    repoURL: 'https://github.com/Petrichoeur/sharewithme.git'
    targetRevision: main
    path: charts/postgres-backup
    helm:
      values: |
        images:
          postgres: "registry.entreprise.corp/postgres:16-alpine"
          awsCli: "registry.entreprise.corp/amazon/aws-cli:2.15.30"
        s3:
          endpoint: "https://s3.ecs01.group.gca"
          bucket: "backup-runai-backend"
          secretName: "accesskey-s3-backup"
  destination:
    server: https://kubernetes.default.svc
    namespace: runai-backend
```

---

## 2. `charts/postgres-restore`

```bash
helm template postgres-restore ./charts/postgres-restore \
  -n runai-backend \
  --set confirm="CONFIRM-RESTORE" \
  --set backupTimestamp="latest" \
  | oc apply -f -
```

---

## 3. `charts/runai-model-downloader`

Déployer dans le namespace du projet Run:ai cible (ex: `runai-ai-research`) :

```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: model-mistral-7b
  namespace: openshift-gitops
spec:
  project: default
  source:
    repoURL: 'https://github.com/Petrichoeur/sharewithme.git'
    targetRevision: main
    path: charts/runai-model-downloader
    helm:
      values: |
        image: "registry.entreprise.corp/python:3.11-slim"
        model:
          repoId: "mistralai/Mistral-7B-v0.1"
          endpointUrl: "https://miroir-hf.entreprise.corp/api/huggingface"
        storage:
          pvcName: "mistral-7b-pvc"
          size: "16Gi"
        runai:
          url: "https://runai.entreprise.corp"
          dataVolumeName: "mistral-7b-v0-1"
          projectName: "ai-research"
  destination:
    server: https://kubernetes.default.svc
    namespace: runai-ai-research
```

---

## 4. `pv-to-datavolume.py`

```bash
./pv-to-datavolume.py \
  --url "https://runai.entreprise.corp" \
  --client-id "<client-id>" \
  --client-secret "<client-secret>" \
  --project "ai-research" \
  --pvcs "pvc-1,pvc-2,pvc-3"
```