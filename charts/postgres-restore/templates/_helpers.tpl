{{- define "postgres-restore.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "postgres-restore.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{- define "postgres-restore.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{ include "postgres-restore.selectorLabels" . }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{- define "postgres-restore.selectorLabels" -}}
app.kubernetes.io/name: {{ include "postgres-restore.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{- define "postgres-restore.image" -}}
{{- $img := .image -}}
{{- $registry := $img.registry | default .root.Values.global.imageRegistry -}}
{{- if $registry }}
{{- printf "%s/%s:%s" (trimSuffix "/" $registry) $img.repository $img.tag -}}
{{- else }}
{{- printf "%s:%s" $img.repository $img.tag -}}
{{- end }}
{{- end }}

{{- define "postgres-restore.postgresImage" -}}
{{- include "postgres-restore.image" (dict "image" .Values.restoreJob.postgresImage "root" .) -}}
{{- end }}

{{- define "postgres-restore.s3Image" -}}
{{- include "postgres-restore.image" (dict "image" .Values.restoreJob.s3Image "root" .) -}}
{{- end }}

{{- define "postgres-restore.s3Endpoint" -}}
{{- if .Values.s3.url }}
{{- $u := urlParse .Values.s3.url -}}
{{- printf "%s://%s" $u.scheme $u.host -}}
{{- else }}
{{- printf "" -}}
{{- end }}
{{- end }}

{{- define "postgres-restore.s3Bucket" -}}
{{- if .Values.s3.bucket }}
{{- .Values.s3.bucket -}}
{{- else if .Values.s3.url }}
{{- $u := urlParse .Values.s3.url -}}
{{- trimPrefix "/" $u.path | splitList "/" | first -}}
{{- else }}
{{- printf "" -}}
{{- end }}
{{- end }}

{{- define "postgres-restore.pullSecrets" -}}
{{- $ps := .Values.restoreJob.pullSecrets | default .Values.global.imagePullSecrets -}}
{{- with $ps }}
imagePullSecrets:
{{- toYaml . | nindent 2 }}
{{- end }}
{{- end }}

{{- define "postgres-restore.s3Env" -}}
- name: S3_ENDPOINT
  value: {{ include "postgres-restore.s3Endpoint" . | quote }}
- name: S3_BUCKET
  value: {{ include "postgres-restore.s3Bucket" . | quote }}
- name: S3_PREFIX
  value: {{ .Values.s3.prefix | trimAll "/" | quote }}
- name: AWS_DEFAULT_REGION
  value: {{ .Values.s3.region | quote }}
- name: AWS_CONFIG_FILE
  value: /etc/aws/config
- name: HOME
  value: /tmp
{{- with .Values.s3.proxy.httpProxy }}
- name: HTTP_PROXY
  value: {{ . | quote }}
- name: http_proxy
  value: {{ . | quote }}
{{- end }}
{{- with .Values.s3.proxy.httpsProxy }}
- name: HTTPS_PROXY
  value: {{ . | quote }}
- name: https_proxy
  value: {{ . | quote }}
{{- end }}
{{- with .Values.s3.proxy.noProxy }}
- name: NO_PROXY
  value: {{ . | quote }}
- name: no_proxy
  value: {{ . | quote }}
{{- end }}
{{- if .Values.s3.customCa.enabled }}
- name: AWS_CA_BUNDLE
  value: /etc/ssl/custom/ca.crt
{{- end }}
{{- end }}

{{- define "postgres-restore.s3Credentials" -}}
- name: AWS_ACCESS_KEY_ID
  valueFrom:
    secretKeyRef:
      name: {{ .root.Values.s3.existingSecret | required "s3.existingSecret est obligatoire" }}
      key: {{ .keys.accessKeyIdKey }}
- name: AWS_SECRET_ACCESS_KEY
  valueFrom:
    secretKeyRef:
      name: {{ .root.Values.s3.existingSecret }}
      key: {{ .keys.secretAccessKeyKey }}
{{- end }}

{{- define "postgres-restore.pgEnv" -}}
- name: PGHOST
  value: {{ .Values.postgres.host | quote }}
- name: PGPORT
  value: {{ .Values.postgres.port | quote }}
- name: PGUSER
  value: {{ .Values.postgres.username | quote }}
- name: PGPASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ .Values.postgres.existingSecret | required "postgres.existingSecret est obligatoire" }}
      key: {{ .Values.postgres.secretKeyRef.passwordKey }}
{{- end }}

{{- define "postgres-restore.s3VolumeMounts" -}}
- name: aws-config
  mountPath: /etc/aws
  readOnly: true
{{- if .Values.s3.customCa.enabled }}
- name: custom-ca
  mountPath: /etc/ssl/custom/ca.crt
  subPath: {{ .Values.s3.customCa.caKey }}
  readOnly: true
{{- end }}
{{- end }}

{{- define "postgres-restore.s3Volumes" -}}
- name: aws-config
  configMap:
    name: {{ include "postgres-restore.fullname" . }}-aws-config
{{- if .Values.s3.customCa.enabled }}
- name: custom-ca
  configMap:
    name: {{ .Values.s3.customCa.configMapName }}
{{- end }}
{{- end }}
