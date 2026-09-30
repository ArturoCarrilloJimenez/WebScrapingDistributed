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

echo "🚀 Iniciando actualización de infraestructura en Kubernetes (Entorno: $ENVIRONMENT)..."
echo "🛡️ Los datos persistentes de PostgreSQL, Redis y Data Lake se conservan intactos en sus PVCs."

# 1. Sincronizar ConfigMap de base de datos desde init.sql
echo "📦 1. Sincronizando ConfigMap de PostgreSQL desde $DB_DIR/init.sql..."
kubectl create configmap postgres-init-sql --from-file=init.sql="$DB_DIR/init.sql" -n web-scraping-distributed --dry-run=client -o yaml | kubectl apply -f -

# 2. Aplicar manifiestos actualizados mediante Kustomize
echo "📦 2. Aplicando manifiestos actualizados con Kustomize..."
kubectl apply -k "$OVERLAY_DIR"

# 3. Reinicio progresivo (Rolling Update) de los servicios
echo "🔄 3. Forzando Rolling Update para desplegar nuevas versiones sin pérdida de servicio..."
kubectl rollout restart deployment producer worker-static worker-dynamic -n web-scraping-distributed

# 4. Esperar confirmación de estado del Producer
echo "⏳ 4. Verificando estado del rollout en Producer..."
kubectl rollout status deployment/producer -n web-scraping-distributed --timeout=90s

# 5. Resumen del estado actual del clúster
echo ""
echo "📊 Resumen de estado en el clúster (web-scraping-distributed):"
kubectl get pods,scaledobject,cronjob -n web-scraping-distributed

echo ""
echo "✅ Actualización completada con éxito. Cero pérdida de datos."
