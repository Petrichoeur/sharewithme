# sharewithme

Helm Charts pour OpenShift en environnement Air-Gapped déployables via ArgoCD :
1. `charts/postgres-backup` : Sauvegarde à chaud (pg_dump) de PostgreSQL (`runai-backend`) vers S3 externe avec rotation sur 7 jours et restauration.
2. `charts/runai-model-downloader` : Téléchargement d'un modèle depuis un miroir interne vers un PV adapté et création du Data Volume Run:ai.

---

## 1. `charts/postgres-backup` (Namespace `runai-backend`)

### Fonctionnement
- **Sauvegarde séquentielle** :
  1. `InitContainer` (`postgres:16-alpine`) : `pg_dumpall --globals-only` (rôles) + `pg_dump -Fc` de chaque base vers `/work/<timestamp>/` et vérification de l'intégrité de l'archive (`pg_restore --list`).
  2. `Conteneur principal` (`amazon/aws-cli:latest`) : Envoi vers S3 externe, mise à jour du pointeur `LATEST`, purge pour conserver strictement les **7 derniers snapshots**, nettoyage local.
- **Restauration** : Job à la demande qui télécharge depuis S3 et exécute `pg_restore`.

### Déploiement ArgoCD

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
          url: "https://mon-s3-externe.corp/mon-bucket"
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

### Restauration d'urgence

```bash
helm upgrade --install postgres-backup ./charts/postgres-backup -n runai-backend \
  --reuse-values \
  --set restore.enabled=true \
  --set restore.confirm=RESTORE \
  --set restore.backup="latest"
```

---

## 2. `charts/runai-model-downloader`

### Calcul préalable de la taille du PV

```bash
python3 charts/runai-model-downloader/calculate-size.py \
  mistralai/Mistral-7B-v0.1 \
  --endpoint "https://miroir-hf.entreprise.corp/api/huggingface"
```

### Déploiement ArgoCD

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
  destination:
    server: https://kubernetes.default.svc
    namespace: runai-backend
```