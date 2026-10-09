# sharewithme

Charts Helm conçus pour **OpenShift en environnement Air-Gapped strict** :
- **Zéro build d'image Docker** : Fonctionne directement avec les images officielles standard présentes dans votre Artifactory.
- **Zéro installation au runtime (`pip`, `apk`, `apt`)** : Tous les scripts tournent avec la bibliothèque standard Python (`urllib`, `json`, `ssl`).
- **Déploiement ArgoCD Ready** : Intègre les annotations de synchronisation `argocd.argoproj.io/hook`.

---

## 1. `charts/postgres-backup` : Backup & Restore (2 Images Standards + FIFO)

### Architecture Zero-Build :
Pour éviter tout build d'image combinant `pg_dump` et `aws-cli`, le pod s'appuie sur deux conteneurs utilisant des images officielles standards couplées par un **named pipe FIFO** en mémoire (`emptyDir: medium: Memory`) :
1. **Conteneur PostgreSQL (`postgres:16-alpine`)** : Exécute `pg_dump` à chaud et écrit dans le pipe FIFO.
2. **Conteneur S3 (`amazon/aws-cli:latest` ou équivalent standard)** : Lit le pipe FIFO et streame directement vers le S3 interne.
*(Même principe inversé pour la restauration à chaud : le conteneur S3 télécharge vers le FIFO, puis `postgres` lit le FIFO et applique `pg_restore`)*.

### Fonctionnalités
- **Rolling 7 Snapshots** : Conserve strictement les **7 derniers dumps** quotidiens sur S3 et purge les plus anciens.
- **Zéro écriture disque Pod** : Le flux transite par le FIFO en RAM, aucun risque de remplir le disque éphémère du pod.
- **Restauration immédiate** : Job à la demande (`restore.enabled=true`) pour restaurer `latest.dump` ou un snapshot spécifique.

### Déploiement ArgoCD (`Application`)
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
        backupJob:
          postgresImage:
            registry: artifactory.internal.corp
            repository: docker-local/postgres
            tag: "16-alpine"
          s3Image:
            registry: artifactory.internal.corp
            repository: docker-local/aws-cli
            tag: "latest"
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

### Restauration en cas d'incident
```bash
helm upgrade --install postgres-backup ./charts/postgres-backup -n runai \
  --reuse-values \
  --set restore.enabled=true \
  --set restore.dumpFile="latest"
```

---

## 2. `charts/runai-model-downloader` : Download Modèle HF & Run:ai Data Volume

### Architecture Zero-Build :
Utilise uniquement l'image officielle standard **`python:3.11-slim`** (sans aucun `pip install` de packages externes). Le script Python autonome utilise la bibliothèque standard (`urllib.request`) pour :
1. Interroger l'API du miroir HuggingFace (Artifactory).
2. Télécharger en chunks de 16 Mo chaque fichier du repo vers le PV.
3. S'authentifier sur le Control Plane Run:ai (`/api/v1/token`) et enregistrer le PVC en tant que **Data Volume** partagé (`/api/v1/datavolumes`).

### Étape 1 : Calcul de la taille optimale du PV
```bash
python3 charts/runai-model-downloader/calculate-size.py \
  mistralai/Mistral-7B-v0.1 \
  --endpoint "https://artifactory.internal.corp/artifactory/api/huggingface"
```

### Étape 2 : Déploiement ArgoCD (`Application`)
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
          repository: docker-local/python
          tag: "3.11-slim"
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