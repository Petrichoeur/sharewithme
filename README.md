# sharewithme

Charts Helm prêts pour **ArgoCD** sur **OpenShift Air-Gapped** (images et dépôts modèles via Artifactory).

---

## 1. `charts/postgres-backup` (Backup Rolling 7 jours & Restore)

Sauvegarde quotidienne à chaud de la base PostgreSQL de prod (`runai`) vers un S3 interne, avec conservation stricte des **7 derniers snapshots**.

### Fonctionnement
- **Dump à chaud** : `pg_dump -Fc` streamé directement vers S3 (`aws s3 cp - ...`). Aucun fichier temporaire sur le disque du pod (zéro risque de crash disque plein).
- **Rolling Snapshots (7 jours)** : À chaque sauvegarde, le CronJob liste les dumps S3 et purge les plus anciens pour n'en conserver **que 7**.
- **Restore immédiat** : Un job déclenchable à la demande (supporte les annotations ArgoCD Hook) pour streamer le snapshot S3 vers `pg_restore`.

### Déploiement via ArgoCD
Exemple d'Application ArgoCD :
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
        image:
          registry: artifactory.internal.corp
          repository: docker-local/postgres-awscli
          tag: "16-v1"
        postgres:
          host: postgres-postgresql.runai.svc.cluster.local
          database: runai
          existingSecret: runai-pg-credentials
        s3:
          endpointUrl: https://s3.internal.corp
          bucket: runai-backups
          prefix: postgres-prod
          existingSecret: s3-credentials
          rollingRotation:
            enabled: true
            maxSnapshots: 7
  destination:
    server: https://kubernetes.default.svc
    namespace: runai
```

### En cas de sinistre : Restaurer la base
Passer `restore.enabled` à `true` dans les values ArgoCD (ou via Helm) :
```bash
helm upgrade --install postgres-backup ./charts/postgres-backup -n runai \
  --reuse-values \
  --set restore.enabled=true \
  --set restore.dumpFile="latest"
```

---

## 2. `charts/runai-model-downloader` (Modèle HF vers Run:ai Data Volume)

Télécharge un modèle HuggingFace depuis votre miroir interne Artifactory dans un PVC dimensionné au plus juste, et l'enregistre en tant que **Data Volume** dans Run:ai (selon la documentation officielle Run:ai).

### Étape 1 : Calculer la taille exacte du PV
Un script simple est fourni pour interroger l'API du miroir et calculer la taille exacte (+15% pour le filesystem ext4/xfs) :
```bash
python3 charts/runai-model-downloader/calculate-size.py \
  mistralai/Mistral-7B-v0.1 \
  --endpoint "https://artifactory.internal.corp/artifactory/api/huggingface"
```
*Exemple de sortie : Modèle 14.48 GiB -> PV recommandé : `16Gi`.*

### Étape 2 : Déploiement via ArgoCD
Le chart déploie le PVC et lance un Job en **PostSync Hook** :
1. Téléchargement propre sans liens symboliques (`snapshot_download`).
2. Appel à l'API Run:ai (`/api/v1/token` puis `/api/v1/datavolumes`) pour créer le Data Volume rattaché au PVC.

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
        image:
          registry: artifactory.internal.corp
          repository: docker-local/model-downloader
          tag: "1.0.0"
        model:
          repoId: "mistralai/Mistral-7B-v0.1"
          endpointUrl: "https://artifactory.internal.corp/artifactory/api/huggingface"
        storage:
          size: "16Gi"
          storageClassName: "ocs-storagecluster-ceph-rbd"
        runai:
          enabled: true
          controlPlaneUrl: "https://runai.internal.corp"
          clusterUuid: "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
          dataVolumeName: "mistral-7b-v0-1"
          projectName: "ai-research"
          authSecretName: "runai-api-credentials"
  destination:
    server: https://kubernetes.default.svc
    namespace: runai
```