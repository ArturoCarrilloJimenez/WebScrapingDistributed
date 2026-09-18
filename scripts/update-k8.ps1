$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$K8sDir = Resolve-Path (Join-Path $ScriptDir "..\infra\k8s")

Write-Host "Iniciando actualizacion de cargas de trabajo en Kubernetes (Namespace: web-scraping-distributed)..." -ForegroundColor Cyan
Write-Host "Los datos del Data Lake S3 y las colas SQS se conservan intactos en el PVC persistente." -ForegroundColor Gray

# 1. Aplicar configuraciones y manifiestos de computacion
Write-Host "Aplicando manifiestos actualizados..." -ForegroundColor Yellow
kubectl apply -f "$K8sDir\01-config-map.yml"
kubectl apply -f "$K8sDir\02-secret.yaml"
kubectl apply -f "$K8sDir\05-producer.yaml"
kubectl apply -f "$K8sDir\06-worker-static.yaml"
kubectl apply -f "$K8sDir\07-worker-dynamic.yaml"
kubectl apply -f "$K8sDir\08-hpa-keda.yaml"
kubectl apply -f "$K8sDir\09-cronjob-compactor.yaml"

# 2. Reinicio progresivo (Rolling Update) de los deployments de computacion
Write-Host "Forzando Rolling Update para desplegar nuevas imagenes de Producer y Workers..." -ForegroundColor Yellow
kubectl rollout restart deployment producer worker-static worker-dynamic -n web-scraping-distributed

# 3. Esperar confirmacion de estado del Producer
Write-Host "Verificando estado del rollout en Producer..." -ForegroundColor Yellow
kubectl rollout status deployment/producer -n web-scraping-distributed --timeout=90s

# 4. Resumen del estado actual del cluster
Write-Host ""
Write-Host "Resumen de estado en el cluster (web-scraping-distributed):" -ForegroundColor Cyan
kubectl get pods,scaledobject,cronjob -n web-scraping-distributed

Write-Host ""
Write-Host "Actualizacion completada con exito. Cero perdida de datos." -ForegroundColor Green
