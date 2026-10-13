# sharewithme

Ce dépôt contient deux Helm Charts optimisés pour **OpenShift en environnement Air-Gapped strict** et déployables nativement via **ArgoCD (GitOps)** :

1. **`charts/postgres-backup`** : Sauvegarde à chaud PostgreSQL (production namespace `runai-backend`) avec rotation rolling sur 7 jours et procédure de restauration immédiate vers/depuis un stockage S3 externe.
2. **`charts/runai-model-downloader`** : Téléchargement autonome d'un modèle d'IA depuis un miroir interne HuggingFace dans un PVC dimensionné sur mesure, puis enregistrement en **Data Volume Run:ai** via l'API.

---

## 🔒 Principes Air-Gapped & Production

- **Zéro build d'image** : Fonctionne directement avec vos images officielles standardisées synchronisées dans votre registry d'entreprise (`postgres:16-alpine`, `amazon/aws-cli:latest`, `python:3.11-slim`).
- **Registry d'entreprise centralisée (`global.imageRegistry`)** : Totalement variabilisée dans `values.yaml` et surchargée proprement par ArgoCD. Aucun préfixe technique en dur (comme `docker-local`).
- **Zéro installation au runtime** : Aucun appel à `pip`, `apk` ou `apt` pendant l'exécution des pods.
- **Conformité OpenShift SCC** : Exécution sous profils de sécurité restreints (`runAsNonRoot: true`, `drop: [ALL]`, `allowPrivilegeEscalation: false`).

---

## 1. `charts/postgres-backup` (Namespace `runai-backend`)

### Architecture & Fonctionnement

```
[CronJob quotidien] - Namespace runai-backend
┌───────────────────────────────── Pod Backup ─────────────────────────────────┐
│                                                                              │
│   InitContainer (postgres:16-alpine)         Conteneur (aws-cli:latest)      │
│   ┌───────────────────────────────┐          ┌───────────────────────────┐   │
│   │ 1. pg_dump -Fc (à chaud)      │ ──File──>│ 2. aws s3 cp ...          │───┼──> S3 Externe
│   │    vers /work/<timestamp>/    │ (volume) │    + Vérification intègre │   │
│   │    + vérification pg_restore  │          │    + Purge Rolling > 7    │   │
│   └───────────────────────────────┘          └───────────────────────────┘   │
└──────────────────────────────────────────────────────────────────────────────┘
```

- **Production-Ready & Multi-bases** : Sauvegarde des rôles (`globals.sql`) et de toutes les bases applicatives Run:ai avec vérification d'intégrité de l'archive (`pg_restore --list`) avant upload.
- **Secrets existants câblés** :
  - Secret base PostgreSQL : `runai-backend-postgresql` (clé `postgres-password`).
  - Secret S3 : `accesskey-s3-backup` (clés `userfa` / `pwdfa` pour le backup, `userro` / `pwdro` pour la restauration).
- **Rolling Snapshots (7 jours)** : Après chaque sauvegarde réussie, purge automatique pour **conserver strictement les 7 derniers snapshots**.
- **Pointeur `LATEST`** : Maintenu à jour pour pointer directement sur la dernière sauvegarde valide.

### Déploiement ArgoCD

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
        global:
          # Votre registry d'entreprise interne
          imageRegistry: registry.entreprise.corp
          imagePullSecrets:
            - name: my-registry-secret
        backupJob:
          postgresImage:
            repository: postgres
            tag: "16-alpine"
          s3Image:
            repository: amazon/aws-cli
            tag: "latest"
        postgres:
          host: runai-backend-postgresql.runai-backend.svc.cluster.local
          existingSecret: runai-backend-postgresql
          secretKeyRef:
            passwordKey: postgres-password
        s3:
          url: "https://s3.eu-west-3.amazonaws.com/mon-bucket-backup"
          existingSecret: accesskey-s3-backup
          secretKeyRef:
            accessKeyIdKey: userfa
            secretAccessKeyKey: pwdfa
          restoreSecretKeyRef:
            accessKeyIdKey: userro
            secretAccessKeyKey: pwdro
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

### Procédure de Restauration (Disaster Recovery)

En cas d'incident, déclenchez le job de restauration après avoir arrêté les connexions de l'application Run:ai :

```bash
helm upgrade --install postgres-backup ./charts/postgres-backup -n runai-backend \
  --reuse-values \
  --set restore.enabled=true \
  --set restore.confirm=RESTORE \
  --set restore.backup="latest"
```

---

## 2. `charts/runai-model-downloader`

### Architecture & Fonctionnement

```
[ArgoCD Sync] ──> Crée le PVC (taille fit)
                      │
                      ▼
               [Job PostSync Hook] (python:3.11-slim)
                      │
                      ├─ 1. Télécharge les poids en streaming direct depuis le miroir interne vers /mnt/models
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
  --endpoint "https://miroir-hf.entreprise.corp/api/huggingface"
```

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
        global:
          imageRegistry: registry.entreprise.corp
        image:
          repository: python
          tag: "3.11-slim"
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
  destination:
    server: https://kubernetes.default.svc
    namespace: runai-backend
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
    │       ├── configmap-aws.yaml    # Config S3 addressing style
    │       ├── cronjob.yaml          # Backup quotidien 2 images + volume partagé + Rolling 7
    │       └── job-restore.yaml      # Restore sécurisé à la demande
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