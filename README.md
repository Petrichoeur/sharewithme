# sharewithme

Helm Charts et scripts d'administration pour OpenShift et **Run:ai v2.23** en production (environnement Air-Gapped) :

1. **[`charts/postgres-backup`](charts/postgres-backup)** : `CronJob` de sauvegarde à chaud (`pg_dump`) de PostgreSQL (`runai-backend`) vers S3 avec rotation glissante.
2. **[`charts/postgres-restore`](charts/postgres-restore)** : `Job` isolé de restauration d'urgence depuis S3 (avec verrou anti-écrasement).
3. **[`charts/runai-model-downloader`](charts/runai-model-downloader)** : Téléchargement d'un modèle depuis un miroir HF vers un PVC (neuf ou existant) et enregistrement en Data Volume Run:ai.
4. **[`pv-to-datavolume.py`](pv-to-datavolume.py)** : Script CLI (pur Python standard) pour enregistrer une liste de PVCs existants en tant que Data Volumes Run:ai sans recréer de volumes.

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
        global:
          imageRegistry: registry.entreprise.corp
        postgres:
          host: runai-backend-postgresql.runai-backend.svc.cluster.local
          existingSecret: runai-backend-postgresql
          secretKeyRef:
            passwordKey: postgres-password
        s3:
          url: "https://s3.ecs01.group.gca"
          existingSecret: accesskey-s3-backup
          secretKeyRef:
            accessKeyIdKey: userfa
            secretAccessKeyKey: pwdfa
          rollingRotation:
            enabled: true
            maxSnapshots: 7
  destination:
    server: https://kubernetes.default.svc
    namespace: runai-backend
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
```

---

## 2. `charts/postgres-restore`

Séparé du chart de backup pour éviter tout déclenchement accidentel par ArgoCD.
Nécessite obligatoirement `restore.confirm="CONFIRM-RESTORE"`.

```bash
helm template postgres-restore ./charts/postgres-restore \
  -n runai-backend \
  --set global.imageRegistry="registry.entreprise.corp" \
  --set restore.confirm="CONFIRM-RESTORE" \
  --set restore.backup="latest" \
  --set s3.url="https://s3.ecs01.group.gca" \
  --set s3.existingSecret="accesskey-s3-backup" \
  | oc apply -f -
```

---

## 3. `charts/runai-model-downloader`

> **Important** : Ce chart doit être déployé dans le **namespace du projet Run:ai** cible (ex: `runai-ai-research`), là où le contrôleur `shared-objects-controller` de Run:ai recherche l'`originPvcName`.

- Si `storage.existingClaim` est vide : crée le PVC dimensionné, télécharge le modèle et l'enregistre en Data Volume (`useOriginalVolume: true`).
- Si `storage.existingClaim` est renseigné : utilise le PVC existant sans en recréer un.
- Si `model.repoId` est vide : ignore le téléchargement et enregistre uniquement le PVC dans Run:ai.

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
        global:
          imageRegistry: registry.entreprise.corp
        model:
          repoId: "mistralai/Mistral-7B-v0.1"
          endpointUrl: "https://miroir-hf.entreprise.corp/api/huggingface"
        storage:
          size: "16Gi"
          storageClassName: "ocs-storagecluster-ceph-rbd"
        runai:
          enabled: true
          controlPlaneUrl: "https://runai.entreprise.corp"
          clusterUuid: "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
          dataVolumeName: "mistral-7b-v0-1"
          projectName: "ai-research"
          authSecretName: "runai-api-credentials"
          shareWithCluster: true
  destination:
    server: https://kubernetes.default.svc
    namespace: runai-ai-research
```

---

## 4. Enregistrement de PVCs existants (`pv-to-datavolume.py`)

Enregistre des PVCs existants (`Bound`) en tant que Data Volumes Run:ai (`POST /api/v1/datavolumes` avec `useOriginalVolume: true` : labellise les PV/PVC sur place sans les dupliquer).

```bash
# Depuis une liste explicite de PVCs
./pv-to-datavolume.py \
  --runai-url "https://runai.entreprise.corp" \
  --client-id "<client-id>" \
  --client-secret "<client-secret>" \
  --project-name "ai-research" \
  --pvcs pvc-model-1,pvc-model-2 \
  --share-cluster

# Depuis un fichier texte (1 nom de PVC par ligne)
./pv-to-datavolume.py \
  --runai-url "https://runai.entreprise.corp" \
  --client-id "<client-id>" \
  --client-secret "<client-secret>" \
  --project-name "ai-research" \
  --file mes-pvcs.txt

# Découverte automatique des PVCs Bound d'un namespace projet
./pv-to-datavolume.py \
  --runai-url "https://runai.entreprise.corp" \
  --client-id "<client-id>" \
  --client-secret "<client-secret>" \
  --project-name "ai-research" \
  --namespace "runai-ai-research"
```