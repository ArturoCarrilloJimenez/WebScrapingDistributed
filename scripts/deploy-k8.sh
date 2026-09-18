#!/usr/bin/env bash
set -e

# Directorio del script y resolución del path de los manifiestos de Kubernetes
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
K8S_DIR="$SCRIPT_DIR/../infra/k8s"

echo "🚀 Iniciando despliegue de infraestructura en Kubernetes (Namespace: web-scraping-distributed)..."

kubectl apply -f "$K8S_DIR/00-namespace.yaml"
kubectl apply -f "$K8S_DIR/01-config-map.yml"
kubectl apply -f "$K8S_DIR/02-secret.yaml"
kubectl apply -f "$K8S_DIR/03-pvc-emulator.yaml"
kubectl apply -f "$K8S_DIR/04-emulator-aws.yaml"
kubectl apply -f "$K8S_DIR/05-producer.yaml"
kubectl apply -f "$K8S_DIR/06-worker-static.yaml"
kubectl apply -f "$K8S_DIR/07-worker-dynamic.yaml"
kubectl apply -f "$K8S_DIR/08-hpa-keda.yaml"
kubectl apply -f "$K8S_DIR/09-cronjob-compactor.yaml"

echo "✅ Despliegue completado con éxito en el clúster."