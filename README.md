# sharewithme

Ce dépôt contient deux Helm Charts optimisés pour **OpenShift en environnement Air-Gapped strict** et déployables nativement via **ArgoCD (GitOps)** :

1. **`charts/postgres-backup`** : Sauvegarde à chaud PostgreSQL (prod namespace `runai`) avec rotation rolling sur 7 jours et procédure de restauration immédiate vers/depuis un S3 interne.
2. **`charts/runai-model-downloader`** : Téléchargement autonome d'un modèle d'IA depuis un miroir interne Artifactory HuggingFace dans un PVC dimensionné sur mesure, puis enregistrement en **Data Volume Run:ai** via l'API.

---

## 🔒 Principes Air-Gapped & Production

- **Zéro build d'image** : Fonctionne avec les images de base standards déjà synchronisées dans votre Artifactory (`postgres:16-alpine`, `amazon/aws-cli:latest`, `python:3.11-slim`).
- **Zéro installation au runtime** : Aucun appel à `pip`, `apk` ou `apt` pendant l'exécution des pods.
- **Zéro buffer disque local** : Les flux de backup/restore transitent via des pipes FIFO en mémoire RAM (`emptyDir: medium: Memory`) pour éviter tout risque de disque plein ou crash OOM.
- **Conformité OpenShift SCC** : Exécution sous profils de sécurité restreints (`runAsNonRoot: true`, `drop: [ALL]`, `allowPrivilegeEscalation: false`).

---

## 1. `charts/postgres-backup`

### Architecture & Fonctionnement

```
[CronJob quotidien]
┌───────────────────────────────── Pod Backup ─────────────────────────────────┐
│                                                                              │
│   InitContainer (postgres:16-alpine)         Conteneur (aws-cli:latest)      │
│   ┌───────────────────────────────┐          ┌───────────────────────────┐   │
│   │ pg_dump -Fc (à chaud)         │ ──FIFO──>│ aws s3 cp - s3://...      │───┼──> S3 Interne
│   └───────────────────────────────┘  (RAM)   │ (Purge Rolling > 7 snaps) │   │
│                                              └───────────────────────────┘   │
└──────────────────────────────────────────────────────────────────────────────┘
```

- **Backup à chaud** : Utilise le format custom PostgreSQL (`-Fc`) avec transaction snapshot (aucun verrou bloquant sur l'application).
- **Rolling Snapshots (7 jours)** : Après chaque sauvegarde réussie, le script liste les snapshots S3 horodatés et supprime automatiquement les plus anciens pour **conserver strictement les 7 derniers**.
- **Pointeur `latest.dump`** : Maintenu à jour à chaque sauvegarde pour simplifier une restauration d'urgence.

### Déploiement ArgoCD

Créez une ressource ArgoCD `Application` :

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
          secretKeyRef:
            passwordKey: postgres-password
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
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
```

### Procédure de Restauration (Disaster Recovery)

En cas d'incident sur la base de données, déclenchez le job de restauration :

```bash
# Restaurer le dernier snapshot disponible :
helm upgrade --install postgres-backup ./charts/postgres-backup -n runai \
  --reuse-values \
  --set restore.enabled=true \
  --set restore.dumpFile="latest"
```
*(Pour cibler un snapshot précis : `--set restore.dumpFile="runai_20261009_020000Z.dump"`)*.

---

## 2. `charts/runai-model-downloader`

### Architecture & Fonctionnement

```
[ArgoCD Sync] ──> Crée le PVC (taille fit)
                      │
                      ▼
               [Job PostSync Hook] (python:3.11-slim)
                      │
                      ├─ 1. Télécharge les poids en streaming direct depuis le miroir Artifactory vers /mnt/models
                      │
                      └─ 2. Appelle l'API Run:ai (/api/v1/token puis /api/v1/datavolumes)
                            => PVC transformé en Data Volume Run:ai partagé
```

- **Dimensionnement exact du PV** : Le volume est calculé pour s'adapter exactement aux poids du modèle (+15% de marge pour les métadonnées et la journalisation ext4/xfs).
- **Zéro pip install** : Le script de téléchargement et le client API Run:ai utilisent uniquement la bibliothèque standard Python (`urllib.request`, `json`, `ssl`).
- **Conformité API Run:ai** : Respecte les spécifications de l'API Workload Assets / Data Volumes de Run:ai (authentification OAuth2 Client Credentials).

### Étape 1 : Pré-calcul de la taille optimale du PV

Exécutez le script utilitaire fourni :
```bash
python3 charts/runai-model-downloader/calculate-size.py \
  mistralai/Mistral-7B-v0.1 \
  --endpoint "https://artifactory.internal.corp/artifactory/api/huggingface"
```
*Exemple : Modèle de 14.48 GiB ➔ Taille recommandée avec buffer : `16Gi`.*

### Étape 2 : Déploiement ArgoCD

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

---

## 📁 Structure du Répertoire

```text
.
├── README.md
└── charts/
    ├── postgres-backup/
    │   ├── Chart.yaml
    │   ├── values.yaml
    │   └── templates/
    │       ├── _helpers.tpl
    │       ├── cronjob.yaml          # Backup quotidien 2 images + FIFO + Rolling 7
    │       ├── job-restore.yaml      # Restore d'urgence à la demande
    │       └── secret.yaml
    └── runai-model-downloader/
        ├── Chart.yaml
        ├── values.yaml
        ├── calculate-size.py         # Utilitaire de calcul de taille du PV
        └── templates/
            ├── _helpers.tpl
            ├── pvc.yaml              # PVC dimensionné sur mesure
            ├── job.yaml              # Job ArgoCD PostSync Hook
            └── configmap-scripts.yaml # Scripts Python purs sans pip (download + API Run:ai)
```