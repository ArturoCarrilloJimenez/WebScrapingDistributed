# Distributed High-Scale Scraping System

Sistema de ingesta y procesamiento de datos a gran escala diseñado bajo principios de arquitectura distribuida, microservicios y event-driven design.

Su objetivo principal es permitir el procesamiento asíncrono de millones de URLs, garantizando alta disponibilidad, escalabilidad horizontal, resiliencia y desacoplamiento total entre la captación de datos, su extracción y su almacenamiento analítico.

---

## 📌 Visión General

A diferencia de los scrapers tradicionales, este sistema utiliza el patrón **"Fire and Forget"** (`202 Accepted`).

El cliente envía una carga masiva de trabajo y recibe una respuesta instantánea con validación de contratos, mientras el sistema orquesta la distribución de tareas en segundo plano a través de colas de mensajería (Amazon SQS) segregadas por complejidad de renderizado.

### Key Features

| Feature | Descripción |
| :--- | :--- |
| **Escalabilidad Horizontal** | Diseñado para manejar picos de tráfico distribuyendo la carga en workers independientes y desvinculados que consumen de forma concurrente. |
| **Arquitectura Asíncrona Native** | Implementación nativa con Python 3.13+, FastAPI y `asyncio` para evitar bloqueos de I/O en el orquestador, consumo de colas y almacenamiento. |
| **Contratos de Datos Robustos** | Validación estricta mediante **Pydantic V2** en todas las fases del ciclo de vida del mensaje, garantizando consistencia y tipado estático. |
| **Rotación de Proxies Avanzada** | Pool de proxies estáticos y gateways residenciales (backconnect) con **Sticky Sessions** (adherencia de IP por tarea) para proteger cookies de sesión. |
| **Capa Activa de Seguridad Anti-Bot** | Filtrado activo preventivo de Honeypots (`HoneypotGuard`) en HTML estático (bs4) y evaluación en lote JS (~15 ms) en Chromium (Playwright). |
| **Gestión Eficiente de Recursos V8** | Límite estricto de RAM por pestaña (512MB), recolección de basura `gc.collect()` y reciclaje automático de Chromium cada 25 tareas procesadas. |
| **Resiliencia & Backoff Dinámico** | Reintentos inteligentes con recálculo dinámico de Visibility Timeout en SQS (exponencial en bloqueos antibot, lineal en timeouts y 5xx). |
| **Data Lake S3 & ETL Compactor** | Almacenamiento directo en S3 en JSON Lines (.jsonl) particionado Hive y posterior compactado ETL (`jobs/`) a formato analítico **Parquet** con compresión **ZSTD**. |
| **Infraestructura como Código (IaC)** | Módulos **Terraform** (`infra/terraform/`) para aprovisionar SQS, DLQ y S3 (con soporte para Floci local y AWS cloud). |
| **Orquestación Cloud-Native & K8s** | Manifiestos listos para **Kubernetes** (`infra/k8s/`) y CI/CD en **GitHub Actions** con auto-push matricial de imágenes Docker a Docker Hub. |

---

## 🏗️ Arquitectura del Sistema

El sistema implementa el patrón **Productor-Cola-Consumidor** con desacoplamiento total entre quien genera el trabajo (Producer), quien lo ejecuta (Worker) y quien compacta los datos (ETL Jobs).

```mermaid
graph TD
    Client[Cliente / API Request] -->|POST /api/v1/enqueue| Producer[Producer API - FastAPI]
    Producer -->|202 Accepted| Client
    
    Producer -->|Router: static_css| SQSStatic[(SQS - scraping-tasks-static)]
    Producer -->|Router: dynamic_playwright| SQSDynamic[(SQS - scraping-tasks-dynamic)]
    
    subgraph Cluster de Workers Estáticos
        WorkerS1[Worker Static 1]
        WorkerS2[Worker Static 2]
    end

    subgraph Cluster de Workers Dinámicos Playwright
        WorkerD1[Worker Dynamic 1]
        WorkerD2[Worker Dynamic 2]
    end

    SQSStatic -->|Long Polling 20s| WorkerS1
    SQSStatic -->|Long Polling 20s| WorkerS2
    
    SQSDynamic -->|Long Polling 20s| WorkerD1
    SQSDynamic -->|Long Polling 20s| WorkerD2

    WorkerS1 -.->|Fallo crítico > Max Retries| DLQStatic[(DLQ - scraping-tasks-dlq-static)]
    WorkerD1 -.->|Fallo crítico > Max Retries| DLQDynamic[(DLQ - scraping-tasks-dlq-dynamic)]
    
    WorkerS2 -->|Buffer Flush JSONL| S3Bucket[(S3 Data Lake - scraping-data-lake)]
    WorkerD2 -->|Buffer Flush JSONL| S3Bucket
    
    WorkerS2 -->|ACK Batch Delete| SQSStatic
    WorkerD2 -->|ACK Batch Delete| SQSDynamic

    subgraph ETL Job Compactor
        CompactorJob[compact_s3.py]
    end

    S3Bucket -.->|1. Lee fragmentos JSONL| CompactorJob
    CompactorJob -->|2. Escribe Parquet ZSTD| S3Bucket
```

### 1. Producer — El Orquestador de Ingesta

El **Producer** es una API de alto rendimiento desarrollada con **FastAPI**. Recibe lotes de tareas de scraping, valida las cargas útiles contra modelos Pydantic V2 y las encola en Amazon SQS de forma asíncrona.

> 📖 **Guía Completa de Uso de la API:** Consulta el [Manual Detallado de Uso de la API Producer](producer/API_USAGE_GUIDE.md) para ver ejemplos paso a paso, uso de selectores `FieldSpec`, contenedores de colecciones e interacciones dinámicas con Playwright.

- **Respuesta Asíncrona (`202 Accepted`)**: Libera al cliente inmediatamente (patrón Fire and Forget).
- **Enrutamiento Dinámico**: Inspecciona el campo `parser_type` de cada tarea y la dirige a la cola correspondiente (`static_css` -> `scraping-tasks-static`, `dynamic_playwright` -> `scraping-tasks-dynamic`).
- **Batching SQS & Conexión Singleton**: Utiliza `aioboto3` para reutilizar conexiones TLS y despachar tareas en lotes de hasta 10 mensajes (`SendMessageBatch`), maximizando el throughput de red.

---

### 2. SQS & DLQ — El Buffer Decoupled y Red de Seguridad

**Amazon SQS** actúa como el búfer persistente y decoupled entre la ingesta y la ejecución.

- **Separación Física de Colas**: Evita que las tareas pesadas de renderizado dinámico (Playwright) bloqueen la ejecución ultrarrápida de tareas estáticas.
- **Long Polling (20s)**: Configurado con `receive_wait_time_seconds = 20` para minimizar peticiones vacías a la API de SQS, reduciendo costes y uso de CPU en los workers.
- **SSE Encryption & Redrive Policies**: Cifrado del lado del servidor activado y vinculación automática con Dead Letter Queues (DLQ) (`scraping-tasks-dlq-static` y `scraping-tasks-dlq-dynamic`) con retención de 14 días (1.209.600s) para aislar tareas con fallos fatales persistentes.

---

### 3. Worker — El Motor de Extracción y Resiliencia

El **Worker** es el consumidor autónomo y altamente concurrente encargado del scraping y parsing del DOM.

- **Concurrencia por Semáforo (`asyncio.Semaphore`)**: Controla de forma estricta el número máximo de tareas concurrentes (`WORKER_NUM_MAX_CONCURRENT_TASKS_STATIC` o `WORKER_NUM_MAX_CONCURRENT_TASKS_DYNAMIC`).
- **Graceful Shutdown**: Captura señales `SIGINT` y `SIGTERM`. Detiene la lectura de SQS, espera la finalización de tareas en vuelo, realiza un **drenado síncrono de RAM a S3** y procesa los ACKs de borrado antes de apagar los sockets.
- **ParserFactory & UniversalDOMExtractor**: Mapea dinámicamente el motor de scraping:
  - `static_css`: Extracción HTTP estática con `httpx` / `curl_cffi` para impersonar firmas TLS.
  - `dynamic_playwright`: Renderizado dinámico en Chromium headless con `playwright-stealth` y Client Hints coherentes (`Sec-Ch-Ua-Platform: "Windows"`).
  - **Extractor Estructurado**: Soporta entidades individuales y colecciones iterativas (`container`) con especificación de atributos HTML mediante `FieldSpec`.
- **Capa Activa Anti-Bot (`HoneypotGuard`)**: Inspecciona trampas HTML invisibles (estilos inline `display:none`, clases sospechosas, ARIA hidden, `<noscript>`) y ejecuta validaciones batch en JavaScript en Chromium (~15 ms por página).
- **Reciclaje de Chromium & Control V8**: Auto-destruye y reinicia el proceso del navegador cada 25 tareas (`playwright_max_tasks_per_browser`), ejecutando `gc.collect()` e inyectando `--js-flags=--max-old-space-size=512` para garantizar un consumo de RAM estable y predecible.
- **Algoritmo de Backoff Dinámico**: Clasifica errores en **Fatales** (ACK inmediato para evitar reintentos inútiles) y **Recuperables** (ajusta el Visibility Timeout en SQS con backoff lineal para timeouts/5xx y exponencial agresivo para bloqueos 403/429/BLOCKED).

---

### 4. Data Lake S3 & ETL Compactor (`jobs/`)

- **Buffer de Trabajos en RAM (`JobBufferService`)**: Acumula `ParseResult` en memoria y los serializa a formato JSON Lines (.jsonl). Realiza el volcado (flush) a S3 cuando un trabajo alcanza `3 MB` de tamaño o expira tras `60 segundos`.
- **Estructura Hive Partitioned**: Los archivos crudos se escriben en S3 bajo la ruta Hive: `raw-data/job_id={job_id}/part-{worker_id}-{timestamp}.jsonl`.
- **Garantía At-Least-Once**: El ACK de borrado en SQS solo se envía tras la subida exitosa del lote a S3.
- **ETL Compactor (`jobs/compact_s3.py`)**: Job batch programado que lee los fragmentos JSONL de `raw-data/`, consolida los registros mediante `pyarrow` / `pandas` y los escribe en formato analítico comprimido **Parquet (ZSTD)** en `compacted-data/job_id={job_id}/data.parquet`, mitigando el *Small Files Problem* de S3.

---

## 📊 Resumen de Componentes

| Servicio / Módulo | Rol Arquitectónico | Tecnologías Principales | Estado |
| :--- | :--- | :--- | :--- |
| **Producer** | API de Ingesta, Validación Pydantic V2, Batch Enqueue | FastAPI, aioboto3, Pydantic | **Completado** |
| **SQS Broker** | Buffer de tareas desacoplado con Long Polling (20s) y SSE | AWS SQS (Static / Dynamic) | **Completado** |
| **DLQ Queues** | Cuarentena de tareas con fallos persistentes (14 días) | AWS SQS DLQ | **Completado** |
| **Worker Engine** | Consumo asíncrono, Parsers, HoneypotGuard, Backoff | asyncio, httpx, Playwright Chromium, bs4 | **Completado** |
| **Anti-Bot Guard** | Inspección activa de trampas Honeypot en HTML y JS | HoneypotGuard, Chromium JS Batch | **Completado** |
| **Proxies Manager** | Rotación de IPs, Sticky Sessions, Gateways Residenciales | Static Pool & Backconnect Gateways | **Completado** |
| **Data Lake Storage** | Sumidero de datos crudos en JSON Lines (.jsonl) | AWS S3 (`scraping-data-lake`), aioboto3 | **Completado** |
| **ETL Compactor Job** | Compacción de JSONL a Parquet analítico ZSTD (`jobs/`) | Python 3.13, PyArrow, Pandas | **Completado** |
| **Terraform (IaC)** | Declaración de infraestructura SQS, S3 y DLQ | Terraform 1.5+, HCL | **Completado** |
| **Kubernetes** | Manifestos de despliegue, secretos y servicios K8s | Kubernetes (K8s), Docker | **Completado** |
| **CI/CD Pipeline** | Build matricial y auto-publish a Docker Hub en `main` | GitHub Actions | **Completado** |

---

## 📁 Organización del Proyecto

El proyecto sigue la arquitectura limpia **Clean Architecture**, aislando capas de dominio, infraestructura, servicios y pruebas.

```py
WebScrapingDistributed/
│
├── .github/
│   └── workflows/
│       └── pr_validation.yml       # Workflow CI/CD: Pytest, SonarCloud y Docker Build/Push Matrix
│
├── producer/                       # Microservicio: API de Ingesta y Orquestación SQS
│   ├── config/                     # Settings centralizados con Pydantic Settings
│   ├── dependencies/               # Inyección de dependencias (SQS Client Singleton)
│   ├── infrastructure/             # Adaptador de envío por lotes a SQS (Batch Writer)
│   ├── scraping/                   # Endpoints FastAPI y controladores de encolado
│   ├── test/                       # Pruebas unitarias de API y validación de contratos
│   ├── main.py                     # Punto de entrada de FastAPI
│   └── Dockerfile
│
├── worker/                         # Microservicio: Consumidor Asíncrono y Extractor DOM
│   ├── config/                     # Configuración de concurrencia, reintentos y proxies
│   ├── dependencies/               # Contenedor DI (SQS, S3, Proxies, HoneypotGuard)
│   ├── infrastructure/             # Adaptadores de red, almacenamiento S3 y lectura de SQS
│   ├── scraping/                   # Lógica de scraping, parsers, seguridad y buffer
│   │   ├── parsers/                # StaticCSSParser, DynamicParser (Playwright) y ParserFactory
│   │   ├── security/               # HoneypotGuard (inspección de trampas estáticas y JS batch)
│   │   ├── services/               # JobBufferService (buffer en RAM y volcado a S3)
│   │   ├── controller.py           # Ciclo principal de lectura, concurrencia y backoff dinámico
│   │   └── exceptions.py           # Clasificación de excepciones (Fatal, Blocked, Timeout)
│   ├── test/                       # Tests unitarios e integración del Worker
│   ├── main.py                     # Inicializador del Worker con captura de señales (Graceful Shutdown)
│   └── Dockerfile
│
├── jobs/                           # Módulo ETL: Jobs Batch de Almacenamiento
│   ├── config/                     # Settings de conexión a S3 Data Lake
│   ├── test/                       # Tests unitarios del compactador S3
│   ├── compact_s3.py               # Job ETL de compacción de JSONL fragmentado a Parquet ZSTD
│   ├── pyproject.toml              # Gestor de dependencias del módulo jobs
│   ├── Dockerfile                  # Imagen Docker ligera dedicada para ejecución de Jobs
│   └── README.md
│
├── shared/                         # Biblioteca de Código y Modelos Compartidos
│   └── shared/
│       ├── models/                 # Modelos Pydantic V2 compartidos (Task, Response, FieldSpec)
│       └── logging.py              # Logger JSON estructurado con trazabilidad
│
├── scripts/                        # Scripts de Automatización y Operación de Clúster
│   ├── deploy-k8.ps1               # Despliegue secuencial de infraestructura K8s (PowerShell)
│   ├── deploy-k8.sh                # Despliegue secuencial de infraestructura K8s (Bash)
│   ├── update-k8.ps1               # Actualización y Rolling Update sin pérdida de datos (PowerShell)
│   └── update-k8.sh                # Actualización y Rolling Update sin pérdida de datos (Bash)
│
├── infra/                          # Infraestructura como Código (IaC) y Emulación Local
│   ├── terraform/                  # Configuración Terraform IaC (SQS, DLQ, S3 Data Lake)
│   │   ├── main.tf
│   │   └── terraform.tf
│   ├── k8s/                        # Manifestos de Kubernetes (Namespace, Secrets, Deployments, KEDA, CronJob)
│   │   ├── 00-namespace.yaml
│   │   ├── 01-config-map.yml
│   │   ├── 02-secret.yaml
│   │   ├── 03-pvc-emulator.yaml
│   │   ├── 04-emulator-aws.yaml
│   │   ├── 05-producer.yaml
│   │   ├── 06-worker-static.yaml
│   │   ├── 07-worker-dynamic.yaml
│   │   ├── 08-hpa-keda.yaml
│   │   └── 09-cronjob-compactor.yaml
│   └── init-aws.sh                 # Script Bash de inicialización para emulador local (Floci)
│
├── docker-compose.yml              # Entorno de desarrollo multi-contenedor
├── .env.template                   # Plantilla de variables de entorno globales
├── sonar-project.properties        # Configuración de análisis estático SonarCloud
└── uv.lock                         # Archivo de lock del gestor de paquetes uv
```

---

## 🚀 Guía de Inicio Rápido

### Requisitos Previos

| Herramienta | Versión Mínima | Uso / Propósito |
| :--- | :--- | :--- |
| **Python** | `3.13+` | Runtime de desarrollo |
| **Docker & Compose** | `20.10+` / `v2+` | Contenerización y emulación |
| **uv** | `Latest` | Gestor de paquetes ultra-rápido |
| **Terraform** *(Opcional)* | `1.5+` | Aprovisionamiento IaC de infraestructura |
| **kubectl** *(Opcional)* | `1.28+` | Despliegue en clúster Kubernetes |

---

### 1. Clonar el repositorio y preparar entorno

```bash
git clone https://github.com/ArturoCarrilloJimenez/WebScrapingDistributed.git
cd WebScrapingDistributed
cp .env.template .env
```

Asegúrate de que tu archivo `.env` contenga la configuración del Data Lake y del emulador:

```ini
# Credenciales AWS (Floci / Local)
DEFAULT_REGION_AWS=us-east-1
AWS_ACCESS_KEY_ID=test
AWS_SECRET_ACCESS_KEY=test

# SQS
SQS_ENDPOINT_URL=http://emulator-aws:4566
SQS_QUEUE_URL=http://emulator-aws:4566/000000000000/scraping-tasks-static
SQS_QUEUE_URL_DYNAMIC=http://emulator-aws:4566/000000000000/scraping-tasks-dynamic
SQS_REGION=us-east-1

# S3 Data Lake
S3_ENDPOINT_URL=http://emulator-aws:4566
S3_BUCKET_NAME=scraping-data-lake
S3_REGION=us-east-1

# Servicios
NUM_MAX_TASKS=10
PRODUCER_PORT=8000
PRODUCER_HOST=0.0.0.0
DEBUG_MODE=True
WORKER_NUM_MAX_CONCURRENT_TASKS_STATIC=60
WORKER_NUM_MAX_CONCURRENT_TASKS_DYNAMIC=3

# Configuración de Proxies (Opcional)
PROXY_ENABLED=False
PROXY_MODE=static_pool
PROXY_STATIC_LIST=http://proxy1.example.com:8080,http://proxy2.example.com:8080
PROXY_URL=http://user:pass@backconnect.example.com:10001
```

---

### 2. Opción A: Arranque Completo con Docker Compose

```bash
docker-compose up -d --build
```

Esto despliega:
1. **`emulator-aws` (Floci)** en el puerto `4566` (ejecuta `infra/init-aws.sh` creando las colas y el bucket S3 `scraping-data-lake`).
2. **`producer`** en el puerto `8000` (`http://localhost:8000/docs`).
3. **`worker-static`** y **`worker-dynamic`** consumiendo de forma concurrente en segundo plano.

Para escalar workers independientemente:
```bash
docker-compose up -d --scale worker-static=3 --scale worker-dynamic=2
```

---

### 3. Opción B: Aprovisionamiento con Terraform IaC

Para aprovisionar la infraestructura de SQS, DLQ y S3 usando Terraform (compatible con el emulador local Floci o AWS real):

```bash
cd infra/terraform
terraform init
terraform plan
terraform apply
```

---

### 4. Opción C: Despliegue en Kubernetes (1 Solo Comando)

Para desplegar toda la infraestructura en tu clúster de Kubernetes local (Docker Desktop / minikube / K3s), utiliza los scripts automatizados incluidos en `scripts/`:

* **En Windows (PowerShell):**
  ```powershell
  .\scripts\deploy-k8.ps1
  ```
* **En Linux / macOS / Git Bash:**
  ```bash
  ./scripts/deploy-k8.sh
  ```

Estos scripts aplican de forma ordenada los manifiestos `00` al `09` (Namespace, ConfigMaps, Secrets, PVC persistente, Emulador AWS, Producer, Workers con autoescalado KEDA a cero y CronJob del Compactor S3).

---

### 5. Actualizaciones de Versión Seguras (Zero Data Loss)

Cuando subas nuevas versiones de código o imágenes Docker (`producer`, `worker` o `jobs`), puedes desplegar la actualización de forma segura **sin perder los datos del Data Lake S3 ni los mensajes en cola**:

* **En Windows (PowerShell):**
  ```powershell
  .\scripts\update-k8.ps1
  ```
* **En Linux / macOS / Git Bash:**
  ```bash
  ./scripts/update-k8.sh
  ```

> [!TIP]
> **¿Por qué es seguro?** El script preserva el almacenamiento persistente (`floci-data-pvc`) y ejecuta un *Rolling Update* progresivo (`kubectl rollout restart`), garantizando cero tiempo de inactividad (*zero-downtime*) y manteniendo intactos todos los datos históricos.

---

### 6. Ejecutar Job ETL de Compacción a Parquet (`jobs/`)

El compactor consolida los fragmentos `.jsonl` a **Parquet (ZSTD)** y retiene los archivos raw durante 7 días (TTL) mediante etiquetado S3 (`compacted=true`).

* **En Kubernetes:** Se ejecuta automáticamente cada 4 horas vía CronJob (`infra/k8s/09-cronjob-compactor.yaml`).
* **Ejecución Local Manual:**
  ```bash
  cd jobs
  uv sync
  uv run python compact_s3.py
  ```

---

## 🧪 Ejecución de Tests Unitarios e Integración

Cada microservicio cuenta con su propia suite de pruebas aislada con Pytest y simuladores AWS (`moto` / `ThreadedMotoServer`):

```bash
# Tests del Producer API
cd producer
uv run pytest

# Tests del Worker (Parsers, HoneypotGuard, Resiliencia)
cd worker
uv run pytest

# Tests del Job ETL Compactor S3
cd jobs
uv run pytest
```

---

## 📈 Hitos y Roadmap

- [x] **Arquitectura asíncrona "Fire and Forget"** (`202 Accepted`).
- [x] **Integración con AWS SQS** mediante `SendMessageBatch` (lotes de 10) y Long Polling (20s).
- [x] **Separación física de colas** por complejidad (`scraping-tasks-static` y `scraping-tasks-dynamic`).
- [x] **Red de seguridad con Dead Letter Queues (DLQ)** y políticas de retención de 14 días.
- [x] **Producer robusto con FastAPI** y validación Pydantic V2.
- [x] **Worker asíncrono con Graceful Shutdown** (drenado síncrono de RAM a S3).
- [x] **Motor de extracción universal (`UniversalDOMExtractor`)** con soporte para entidades y colecciones (`container` + `FieldSpec`).
- [x] **Capa activa Anti-Bot (`HoneypotGuard`)** con filtrado de trampas HTML e inspección batch JS en Chromium (~15 ms).
- [x] **Gestión de memoria Chromium**: Límite V8 Heap (512MB) y reciclador automático cada 25 tareas (`playwright_max_tasks_per_browser`).
- [x] **Evasión Anti-Bot**: `playwright-stealth` y Client Hints coherentes (`Sec-Ch-Ua-Platform: "Windows"`).
- [x] **Control de Resiliencia Inteligente**: Clasificación de fallos y backoff dinámico en SQS.
- [x] **Módulo de Rotación de Proxies**: Static Pool y Backconnect Gateways con Sticky Sessions.
- [x] **Almacenamiento en Data Lake S3**: Volcado en memoria RAM (`JobBufferService`) en formato JSON Lines particionado Hive (`scraping-data-lake`).
- [x] **Job ETL Compactor (`jobs/`)**: Compacción batch de JSONL a **Parquet (ZSTD)** con Object Tagging (`compacted=true`) y purgado automático TTL (7 días).
- [x] **Infraestructura como Código (IaC)**: Módulos Terraform (`infra/terraform/`) para SQS, DLQ y S3.
- [x] **Manifiestos de Kubernetes (`infra/k8s/`)**: Namespace, ConfigMaps, Secrets, PVC persistente, Deployments y CronJob de compactación (`00` a `09`).
- [x] **Escalado Automático en K8s (KEDA)**: Autoscaling de Workers K8s basado en la longitud de colas SQS (`08-hpa-keda.yaml`) con Scale-to-Zero.
- [x] **Scripts de Operación Unificada (`scripts/`)**: Despliegue (`deploy-k8`) y actualización sin pérdida de datos (`update-k8`) para PowerShell y Bash.
- [x] **Pipeline CI/CD en GitHub Actions**: Validación en PRs con Pytest + SonarCloud y auto-push matricial de imágenes Docker a Docker Hub en `main` (`producer`, `worker`, `jobs`).
- [ ] **Despliegue en VPS (K3s) o AWS Fargate**: Despliegue de los microservicios en un clúster de Kubernetes (K3s) o en AWS Fargate.
- [ ] **Base de Datos de Estado**: Guardado de estados intermedios y de-duplicación de URLs en MongoDB / PostgreSQL.
- [ ] **Dashboard de Observabilidad**: Panel interactivo (Grafana / Prometheus) para monitorización en tiempo real de colas y métricas del clúster de workers.

