$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$K8sDir = Resolve-Path (Join-Path $ScriptDir "..\infra\k8s")

Write-Host "Iniciando despliegue de infraestructura en Kubernetes (Namespace: web-scraping-distributed)..." -ForegroundColor Cyan

kubectl apply -f "$K8sDir\00-namespace.yaml"
kubectl apply -f "$K8sDir\01-config-map.yml"
kubectl apply -f "$K8sDir\02-secret.yaml"
kubectl apply -f "$K8sDir\03-pvc-emulator.yaml"
kubectl apply -f "$K8sDir\04-emulator-aws.yaml"
kubectl apply -f "$K8sDir\05-producer.yaml"
kubectl apply -f "$K8sDir\06-worker-static.yaml"
kubectl apply -f "$K8sDir\07-worker-dynamic.yaml"
kubectl apply -f "$K8sDir\08-hpa-keda.yaml"
kubectl apply -f "$K8sDir\09-cronjob-compactor.yaml"

Write-Host "Despliegue completado con exito en el cluster." -ForegroundColor Green
