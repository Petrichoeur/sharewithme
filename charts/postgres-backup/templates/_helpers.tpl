{{/*
Expand the name of the chart.
*/}}
{{- define "postgres-backup.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
*/}}
{{- define "postgres-backup.fullname" -}}
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

{{/*
Common labels
*/}}
{{- define "postgres-backup.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{ include "postgres-backup.selectorLabels" . }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: runai-postgres-backup
{{- end }}

{{/*
Selector labels
*/}}
{{- define "postgres-backup.selectorLabels" -}}
app.kubernetes.io/name: {{ include "postgres-backup.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
PostgreSQL Stock Image reference
*/}}
{{- define "postgres-backup.postgresImage" -}}
{{- if .Values.backupJob.postgresImage.registry }}
{{- printf "%s/%s:%s" .Values.backupJob.postgresImage.registry .Values.backupJob.postgresImage.repository .Values.backupJob.postgresImage.tag }}
{{- else }}
{{- printf "%s:%s" .Values.backupJob.postgresImage.repository .Values.backupJob.postgresImage.tag }}
{{- end }}
{{- end }}

{{/*
S3 Stock Image reference
*/}}
{{- define "postgres-backup.s3Image" -}}
{{- if .Values.backupJob.s3Image.registry }}
{{- printf "%s/%s:%s" .Values.backupJob.s3Image.registry .Values.backupJob.s3Image.repository .Values.backupJob.s3Image.tag }}
{{- else }}
{{- printf "%s:%s" .Values.backupJob.s3Image.repository .Values.backupJob.s3Image.tag }}
{{- end }}
{{- end }}
