param (
    [ValidateSet("local", "prod")]
    [string]$Environment = "local"
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$K8sDir = Resolve-Path (Join-Path $ScriptDir "..\infra\k8s")
$DbDir = Resolve-Path (Join-Path $ScriptDir "..\infra\db")
$OverlayDir = Join-Path $K8sDir "overlays\$Environment"

Write-Host "Iniciando actualizacion de infraestructura en Kubernetes (Entorno: $Environment)..." -ForegroundColor Cyan
Write-Host "Los datos persistentes de PostgreSQL, Redis y Data Lake se conservan intactos en sus PVCs." -ForegroundColor Gray

# 1. Sincronizar ConfigMap de base de datos desde init.sql
Write-Host "1. Sincronizando ConfigMap de PostgreSQL desde $DbDir\init.sql..." -ForegroundColor Yellow
kubectl create configmap postgres-init-sql --from-file=init.sql="$DbDir\init.sql" -n web-scraping-distributed --dry-run=client -o yaml | kubectl apply -f -

# 2. Aplicar manifiestos actualizados mediante Kustomize
Write-Host "2. Aplicando manifiestos actualizados con Kustomize..." -ForegroundColor Yellow
kubectl apply -k "$OverlayDir"

# 3. Reinicio progresivo (Rolling Update) de los servicios
Write-Host "3. Forzando Rolling Update para desplegar nuevas versiones sin perdida de servicio..." -ForegroundColor Yellow
kubectl rollout restart deployment producer worker-static worker-dynamic -n web-scraping-distributed

# 4. Esperar confirmacion de estado del Producer
Write-Host "4. Verificando estado del rollout en Producer..." -ForegroundColor Yellow
kubectl rollout status deployment/producer -n web-scraping-distributed --timeout=90s

# 5. Resumen del estado actual del cluster
Write-Host ""
Write-Host "Resumen de estado en el cluster (web-scraping-distributed):" -ForegroundColor Cyan
kubectl get pods,scaledobject,cronjob -n web-scraping-distributed

Write-Host ""
Write-Host "Actualizacion completada con exito. Cero perdida de datos." -ForegroundColor Green
