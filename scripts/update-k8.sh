#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
K8S_DIR="$SCRIPT_DIR/../infra/k8s"

echo "🚀 Iniciando actualización de cargas de trabajo en Kubernetes (Namespace: web-scraping-distributed)..."
echo "🛡️ Los datos del Data Lake S3 y las colas SQS se conservan intactos en el PVC persistente."

# 1. Aplicar configuraciones y manifiestos de computación
echo "📦 Aplicando manifiestos actualizados..."
kubectl apply -f "$K8S_DIR/01-config-map.yml"
kubectl apply -f "$K8S_DIR/02-secret.yaml"
kubectl apply -f "$K8S_DIR/05-producer.yaml"
kubectl apply -f "$K8S_DIR/06-worker-static.yaml"
kubectl apply -f "$K8S_DIR/07-worker-dynamic.yaml"
kubectl apply -f "$K8S_DIR/08-hpa-keda.yaml"
kubectl apply -f "$K8S_DIR/09-cronjob-compactor.yaml"

# 2. Reinicio progresivo (Rolling Update) de los deployments de computación
echo "🔄 Forzando Rolling Update para desplegar nuevas imágenes de Producer y Workers..."
kubectl rollout restart deployment producer worker-static worker-dynamic -n web-scraping-distributed

# 3. Esperar confirmación de estado del Producer
echo "⏳ Verificando estado del rollout en Producer..."
kubectl rollout status deployment/producer -n web-scraping-distributed --timeout=90s

# 4. Resumen del estado actual del clúster
echo ""
echo "📊 Resumen de estado en el clúster (web-scraping-distributed):"
kubectl get pods,scaledobject,cronjob -n web-scraping-distributed

echo ""
echo "✅ Actualización completada con éxito. Cero pérdida de datos."
