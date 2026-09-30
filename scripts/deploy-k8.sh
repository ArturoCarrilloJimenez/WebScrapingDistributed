#!/usr/bin/env bash
set -e

ENVIRONMENT="${1:-local}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
K8S_DIR="$SCRIPT_DIR/../infra/k8s"
DB_DIR="$SCRIPT_DIR/../infra/db"
OVERLAY_DIR="$K8S_DIR/overlays/$ENVIRONMENT"

if [ ! -d "$OVERLAY_DIR" ]; then
    echo "❌ Error: El entorno '$ENVIRONMENT' no existe. Opciones validas: local, prod"
    exit 1
fi

echo "🚀 1. Sincronizando ConfigMap de PostgreSQL desde $DB_DIR/init.sql..."
kubectl create namespace web-scraping-distributed --dry-run=client -o yaml | kubectl apply -f -
kubectl create configmap postgres-init-sql --from-file=init.sql="$DB_DIR/init.sql" -n web-scraping-distributed --dry-run=client -o yaml | kubectl apply -f -

echo "🚀 2. Desplegando infraestructura con Kustomize (Entorno: $ENVIRONMENT)..."
kubectl apply -k "$OVERLAY_DIR"

echo "✅ Despliegue completado con éxito en el clúster."