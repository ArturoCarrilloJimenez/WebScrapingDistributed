# Distributed High-Scale Scraping System

Sistema de ingesta y procesamiento de datos a gran escala diseñado bajo principios de arquitectura distribuida y event-driven design.

Su objetivo principal es permitir el procesamiento asíncrono de millones de URLs, garantizando escalabilidad, resiliencia y desacoplamiento total entre la captación de datos y su ejecución.

Además, este proyecto lo estoy usando con el objetivo de aprender en el proceso con el enfoque de ver hasta donde puede llegar.

## 📌 Visión General

A diferencia de los scrapers tradicionales, este sistema utiliza el patrón **"Fire and Forget"** (`202 Accepted`).

El usuario envía una carga masiva de trabajo y recibe una respuesta instantánea, mientras el sistema orquesta la distribución de tareas en segundo plano a través de colas de mensajería (SQS).

### Key Features

| Feature                           | Descripción                                                                                                                                       |
| --------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Escalabilidad horizontal**      | Diseñado para manejar picos de tráfico distribuyendo la carga en workers independientes que compiten de manera eficiente por el trabajo.          |
| **Arquitectura asíncrona**        | Implementación nativa con FastAPI y `asyncio` para evitar bloqueos de I/O, tanto en el orquestador como en los motores de consumo y extracción.   |
| **Contratos de Datos Robustos**   | Validación estricta mediante **Pydantic** en todas las fases del ciclo de vida del mensaje, garantizando consistencia en las colas.               |
| **Rotación de Proxies Avanzada**  | Soporte para pools de proxies estáticos y gateways residenciales (backconnect) con persistencia de sesión por tarea (**Sticky Sessions**).        |
| **Resiliencia & Backoff Dinámico**| Mecanismo de reintentos inteligente con cálculo de tiempo de espera dinámico adaptado a la categoría del error (bloqueos, timeouts, caídas 5xx).  |
| **Infraestructura Cloud-Native**  | Preparado para entornos AWS (SQS/DLQ) y emulado localmente de forma eficiente con **Floci** (emulador open source sin restricciones de LocalStack).|

---

## 🏗️ Arquitectura del Sistema

El sistema implementa el patrón **Productor-Cola-Consumidor** con desacoplamiento total entre quien genera el trabajo (Producer) y quien lo ejecuta (Worker).

Esto permite que ambos lados escalen de forma independiente y que ninguna tarea se pierda aunque los workers sufran caídas críticas.

```mermaid
graph TD
    Client[Cliente / API Request] -->|POST /scraping/batch| Producer[Producer API - FastAPI]
    Producer -->|202 Accepted| Client
    Producer -->|Batch Send 10 msg max| SQS[(SQS - scraping-tasks)]
    
    subgraph Cluster de Workers [Escalado Horizontal]
        Worker1[Worker 1]
        Worker2[Worker 2]
        WorkerN[Worker N]
    end

    SQS -->|Fetch batch| Worker1
    SQS -->|Fetch batch| Worker2
    SQS -->|Fetch batch| WorkerN

    Worker1 -.->|Fallo crítico > Max Retries| DLQ[(DLQ - scraping-tasks-dlq)]
    Worker2 -.->|Reintento temporal| SQS
    WorkerN -->|ACK - Delete Batch| SQS
```

### 1. Producer — El Orquestador

El **Producer** es una API de alto rendimiento desarrollada con **FastAPI**. No ejecuta scraping directamente; su única responsabilidad es recibir lotes de URLs, validarlos contra los contratos estrictos del sistema y despacharlos a la cola de mensajería.

> [!NOTE]
> Responde con `202 Accepted` de forma inmediata (**patrón Fire and Forget**), liberando al cliente sin obligarlo a esperar a que el scraping finalice.

#### ¿Por qué FastAPI + aioboto3?
- **Concurrencia asíncrona**: FastAPI con `asyncio` permite recibir miles de peticiones concurrentes sin bloquear hilos de I/O del servidor de aplicaciones mientras espera la confirmación de envío a SQS.
- **Conexiones eficientes (Singleton)**: `aioboto3` reutiliza una única conexión al cliente SQS, evitando el coste computacional de abrir y negociar TLS en una nueva conexión por cada petición recibida.
- **Batching optimizado**: El envío se realiza en lotes (`SendMessageBatch`) de hasta 10 mensajes (límite de la API de SQS), reduciendo drásticamente las llamadas de red y optimizando la tasa de transferencia.

---

### 2. SQS & DLQ — El Buffer y la Red de Seguridad

**Amazon SQS** actúa como el búfer persistente y de desacoplamiento entre el volumen de producción y la capacidad del consumidor.
- **Desacoplamiento total**: El Producer puede encolar 100.000 tareas en segundos; el clúster de Workers las irá procesando a su propio ritmo de manera fluida.
- **Garantía de entrega (At-least-once)**: Si un Worker falla a mitad del procesamiento de una tarea, el mensaje vuelve a estar visible en la cola después de que expire su tiempo de visibilidad para ser procesado por otro Worker saludable.
- **DLQ (Dead Letter Queue)**: Cuando una tarea supera el número máximo de reintentos (`max_retries`, por defecto 10), SQS la desvía automáticamente a la cola de descarte (`scraping-tasks-dlq`). Esto evita que un error persistente (como una URL inexistente o formato corrupto) sature los hilos de ejecución indefinidamente.

---

### 3. Worker — El Motor de Extracción (100% Funcional)

El **Worker** es un microservicio autónomo y altamente concurrente encargado de la ejecución del scraping. Consume mensajes de SQS, analiza su carga útil, asigna los recursos necesarios y delega la extracción a su factoría de parsers.

#### Características Clave del Worker:
- **Concurrencia Controlada**: Utiliza un semáforo asíncrono (`asyncio.Semaphore`) limitado por `WORKER_NUM_MAX_CONCURRENT_TASKS` para evitar saturar el ancho de banda local y prevenir bloqueos de CPU por exceso de tareas en vuelo.
- **Apagado Seguro (Graceful Shutdown)**: Captura las señales de parada del sistema (`SIGINT`, `SIGTERM`). Ante una señal de detención, el motor:
  1. Deja de recibir nuevas tareas de SQS de forma inmediata.
  2. Espera a que todas las tareas en vuelo terminen de procesarse de manera limpia.
  3. Ejecuta un vaciado rápido (**flush**) de los ACKs restantes acumulados en su buffer de memoria hacia SQS.
  4. Cierra las conexiones y sockets del cliente asíncrono de forma controlada.
- **Factoría de Parsers Dinámica (`ParserFactory`)**: Mapea dinámicamente el campo `parser_type` de la tarea al motor de extracción adecuado. Actualmente integra:
  - `STATIC_CSS`: Extractor altamente eficiente basado en `aiohttp` y selectores CSS/XPath configurados bajo demanda.
- **Buffer asíncrono de ACKs (`_ack_flusher`)**: Para evitar el coste de borrar mensajes uno a uno en SQS, el Worker los acumula en una cola en memoria y una tarea secundaria los elimina en lotes periódicos de hasta 10 mensajes, reduciendo el tráfico de red de bajada.

#### Resiliencia y Algoritmo de Backoff Dinámico:
El Worker clasifica las excepciones para responder de manera inteligente ante los fallos:
1. **Errores Fatales** (e.g., `NOT_FOUND` (404) o `INVALID_SCHEMA`): No se reintentan. El Worker los elimina de inmediato de SQS (ACK forzado) para no desperdiciar recursos del sistema.
2. **Errores Recuperables**: Incrementan el contador de intentos y recalculan de manera dinámica el **Visibility Timeout** en SQS utilizando estrategias específicas:
   - **Timeout de red**: Backoff lineal corto (`5s * retry_count`).
   - **Error del Servidor Remoto (5xx)**: Backoff lineal moderado (`15s * retry_count`).
   - **Bloqueo / Antibot (`BLOCKED`)**: Backoff exponencial agresivo (`30s * 2^retry_count`, límite de 300s) para enfriar la IP o el proxy asignado.

---

### 4. Sistema de Proxies Avanzado

El sistema integra un módulo de red altamente sofisticado y configurable para la rotación y gestión de proxies, mitigando los bloqueos antibot habituales.

Soporta dos modalidades de funcionamiento:
- **Pool de Proxies Estáticos (`static_pool`)**: Rota las conexiones a través de una lista predefinida en `PROXY_STATIC_LIST`.
- **Gateway Residencial / Rotativo (`backconnect`)**: Canaliza todas las peticiones a través de un proxy backconnect configurado en `PROXY_URL`.

Ambos modos son compatibles con **Sticky Sessions** (Sesiones Adherentes). Si la tarea incluye un `sticky_session_id` (por ejemplo, el ID de un scraper de sesión), el sistema garantiza que todas las peticiones sucesivas utilicen el mismo nodo de salida o proxy residencial (inyectando dinámicamente el identificador de sesión en la autenticación del proxy o mediante hash de selección), protegiendo la coherencia de las cookies de sesión.

---

## 📊 Resumen de Componentes

| Servicio | Rol | Protocolo / Transporte | Estado |
| :--- | :--- | :--- | :--- |
| **Producer** | Ingesta, Validación (Pydantic), Encolado (Batching) | HTTP REST (FastAPI) | **Completado** |
| **Transporte** | Búfer temporal y persistencia de tareas | AWS SQS | **Completado** |
| **Dead Letter** | Captura y cuarentena de tareas defectuosas | AWS SQS DLQ | **Completado** |
| **Worker** | Consumo asíncrono, Concurrencia, Parsers, Reintentos | asyncio + aiohttp | **Completado** |
| **Proxies** | Evasión de bloqueos, Sticky Sessions | Static Pool & Backconnect | **Completado** |

---

## 📁 Organización del Proyecto

El repositorio sigue los principios de **Clean Architecture**, aislando la infraestructura de los contratos y la lógica de negocio básica compartida.

```py
WebScrapingDistributed/
│
├── producer/                       # Microservicio: Ingesta y encolado de tareas de scraping
│   ├── config/                     # Configuración de variables de la API (Settings)
│   ├── dependencies/               # Inyección de dependencias del cliente SQS
│   ├── infrastructure/             # Adaptadores de salida hacia SQS (Batch Writer)
│   ├── scraping/                   # API REST y controladores de encolado
│   ├── test/                       # Tests unitarios del API y contratos
│   ├── main.py                     # Inicializador y configuración de FastAPI
│   └── Dockerfile
│
├── worker/                         # Microservicio: Consumidor y extractor asíncrono
│   ├── config/                     # Configuración de entornos y variables del Worker
│   ├── dependencies/               # Contenedor de dependencias del Worker (SQS, Proxies, Clientes)
│   ├── infrastructure/             # Adaptadores de infraestructura
│   │   ├── network/                # Gestión de red y rotación de proxies (StaticPool, Backconnect)
│   │   └── task/                   # Adaptador de consumo asíncrono de SQS
│   ├── scraping/                   # Motores de análisis y extracción
│   │   ├── parsers/                # Motores de parseo (StaticCSSParser) y factorías
│   │   ├── controller.py           # Orquestador de consumo, concurrencia y tolerancia a fallos
│   │   └── exceptions.py           # Clasificación de excepciones (Fatal, Blocked, Timeout)
│   ├── test/                       # Tests unitarios y de integración de lógica y reintentos
│   ├── main.py                     # Inicializador del Worker y captura de señales (Graceful Shutdown)
│   └── Dockerfile
│
├── shared/                         # Código común y modelos compartidos
│   └── shared/
│       ├── models/                 # Contratos Pydantic compartidos
│       │   ├── parse_type/         # Enums de motores de parseo (static_css, playwright_amazon)
│       │   ├── scraping_task.py    # Modelo de datos de tareas y resiliencia
│       │   └── batch_task_response.py # Modelos de respuestas por lotes
│       └── logging.py              # Configuración común del logger estructurado
│
├── infra/                          # Scripts de infraestructura local y cloud
│   └── init-aws.sh                 # Script de configuración automática de SQS/DLQ
│
├── docker-compose.yml              # Orquestación de contenedores locales
├── .env.template                   # Plantilla de variables de entorno del proyecto
├── sonar-project.properties        # Propiedades de análisis estático SonarQube / SonarCloud
└── uv.lock                         # Archivo de bloqueo del gestor de dependencias
```

---

## 🚀 Guía de Inicio Rápido

### Requisitos Previos

| Herramienta | Versión Mínima | Descripción |
| :--- | :--- | :--- |
| **Python** | `3.13+` | Lenguaje de desarrollo principal |
| **Docker & Compose** | `20.10+` / `v2+` | Contenerización y emulación de red |
| **uv** | `Latest` | Gestor de dependencias ultra-rápido de Python |

---

### 1. Clonar el repositorio y configurar variables

```bash
git clone https://github.com/ArturoCarrilloJimenez/WebScrapingDistributed.git
cd WebScrapingDistributed
cp .env.template .env
```

Configura tu archivo `.env` con los valores de prueba locales (los valores por defecto ya están preparados para funcionar directamente con el compose):

```ini
# Credenciales AWS (Floci)
DEFAULT_REGION_AWS=us-east-1
AWS_ACCESS_KEY_ID=test
AWS_SECRET_ACCESS_KEY=test

# SQS
SQS_ENDPOINT_URL=http://emulator-aws:4566
SQS_QUEUE_URL=http://emulator-aws:4566/000000000000/scraping-tasks

# Servicios
NUM_MAX_TASKS=10
PRODUCER_PORT=8000
PRODUCER_HOST=0.0.0.0
DEBUG_MODE=True
WORKER_NUM_MAX_CONCURRENT_TASKS=5

# Configuración de Proxies del Worker (Opcional)
PROXY_ENABLED=False
PROXY_MODE=static_pool
PROXY_STATIC_LIST=http://proxy1.example.com:8080,http://proxy2.example.com:8080
PROXY_URL=http://user:pass@backconnect.example.com:10001
```

### 2. Arranque Completo con Docker Compose (Recomendado)

Levanta toda la infraestructura distribuida con un único comando:

```bash
docker-compose up -d --build
```

Este comando levanta de forma inmediata:
1. **`emulator-aws` (Floci)** en el puerto `4566`. Ejecuta automáticamente `infra/init-aws.sh` inicializando la cola principal `scraping-tasks` y su Dead Letter Queue `scraping-tasks-dlq`.
2. **`producer`** en el puerto `8000`. Expone la API para inyectar tareas.
3. **`worker`**. Consumidor de tareas en segundo plano. Puedes escalar los workers libremente si quieres comprobar el reparto de carga:
   ```bash
   docker-compose up -d --scale worker=3
   ```

### 3. Arranque en Desarrollo Local (Sin Docker para los servicios)

Si deseas depurar los microservicios de manera directa utilizando entornos virtuales con `uv`:

#### Paso 1: Levantar únicamente el emulador de AWS
```bash
docker-compose up emulator-aws -d
```

#### Paso 2: Ejecutar el Producer
```bash
cd producer
uv sync
uv run uvicorn main:app --reload --port 8000
```

#### Paso 3: Ejecutar el Worker (en otra terminal)
```bash
cd worker
uv sync
uv run python main.py
```

---

## 🧪 Ejecución de Tests

El proyecto cuenta con una suite completa de pruebas unitarias y de integración asíncronas para garantizar que los flujos de comunicación con SQS y el comportamiento del algoritmo de resiliencia funcionen como se espera.

Para ejecutar los tests de cada servicio:

```bash
# Ejecutar tests del Producer
cd producer
uv run pytest

# Ejecutar tests del Worker
cd worker
uv run pytest
```

---

## 📈 Roadmap

- [x] **Arquitectura asíncrona "Fire and Forget"** de extremo a extremo.
- [x] **Integración avanzada con SQS** mediante batching optimizado (lotes de 10 mensajes).
- [x] **Sistema de logging estructurado** con identificadores de contexto unificados.
- [x] **Integración continua (CI) mediante SonarQube** para análisis de calidad y mantenibilidad estática de código.
- [x] **Producer robusto** que gestiona, valida con Pydantic y encola las tareas.
- [x] **Worker asíncrono robusto** y con soporte completo de Graceful Shutdown.
- [x] **Factoría de Parsers** modular e implementación de `StaticCSSParser` basada en `aiohttp`.
- [x] **Control de Resiliencia Inteligente**: Diferenciación de excepciones y reintentos con backoff dinámico en SQS.
- [x] **Módulo de Rotación de Proxies**: Soporte para Static Pool y Backconnect con Sticky Sessions.
- [x] **Suite de Tests de Integración**: Cobertura de tests asíncronos y simulaciones de fallos en red.
- [ ] **Parsers Dinámicos**: Implementación de extracción mediante Playwright/Selenium para webs dinámicas con renderizado JS.
- [ ] **Almacenamiento en S3**: Integración asíncrona para guardar el contenido o capturas extraídas directamente en buckets.
- [ ] **Base de Datos de Estado**: Guardado de estados intermedios y de-duplicación agresiva de URLs para evitar procesados dobles.
- [ ] **Despliegue Continuo (CD)** automatizado para subidas directas a la nube.
- [ ] **Migración a K8s & Terraform**: Despliegue en clusters elásticos basados en Kubernetes con aprovisionamiento IaaC.
- [ ] **Forward Proxy de Infraestructura**: Configuración opcional basada en proxies robustos (Envoy / mitmproxy) para rotación a bajo nivel de red.
- [ ] **Dashboard de Observabilidad**: Panel de control interactivo (Redis + Grafana o Streamlit) para monitoreo de colas y rendimiento del clúster de workers.
- [ ] **IA Parsing**: Integración de LLMs locales y de API para el parseado adaptativo de webs sin depender de selectores rígidos.
