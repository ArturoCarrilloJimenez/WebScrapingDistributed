param (
    [ValidateSet("local", "prod")]
    [string]$Environment = "local"
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$K8sDir = Resolve-Path (Join-Path $ScriptDir "..\infra\k8s")
$DbDir = Resolve-Path (Join-Path $ScriptDir "..\infra\db")
$OverlayDir = Join-Path $K8sDir "overlays\$Environment"

Write-Host "1. Sincronizando ConfigMap de PostgreSQL desde $DbDir\init.sql..." -ForegroundColor Cyan
kubectl create namespace web-scraping-distributed --dry-run=client -o yaml | kubectl apply -f -
kubectl create configmap postgres-init-sql --from-file=init.sql="$DbDir\init.sql" -n web-scraping-distributed --dry-run=client -o yaml | kubectl apply -f -

Write-Host "2. Desplegando infraestructura con Kustomize (Entorno: $Environment)..." -ForegroundColor Cyan
kubectl apply -k "$OverlayDir"

Write-Host "Despliegue completado con exito en el cluster." -ForegroundColor Green
