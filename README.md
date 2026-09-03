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
| **Capa Activa de Seguridad Anti-Bot**| Filtrado activo preventivo de Honeypots (`HoneypotGuard`) en HTML estático (bs4) y en lote dinámico (~15 ms) en Chromium (Playwright). |
| **Resiliencia & Backoff Dinámico**| Mecanismo de reintentos inteligente con cálculo de tiempo de espera dinámico adaptado a la categoría del error (bloqueos, timeouts, caídas 5xx).  |
| **Almacenamiento en S3 (Sink)**   | Estructuración en JSON Lines (.jsonl) y guardado asíncrono en S3 mediante un buffer en memoria RAM para amortizar costes de red y CPU.       |
| **Infraestructura Cloud-Native**  | Preparado para entornos AWS (SQS/S3/DLQ) y emulado localmente de forma eficiente con **Floci** (emulador open source sin restricciones de LocalStack).|


---

## 🏗️ Arquitectura del Sistema

El sistema implementa el patrón **Productor-Cola-Consumidor** con desacoplamiento total entre quien genera el trabajo (Producer) y quien lo ejecuta (Worker).

Esto permite que ambos lados escalen de forma independiente y que ninguna tarea se pierda aunque los workers sufran caídas críticas.

```mermaid
graph TD
    Client[Cliente / API Request] -->|POST /scraping/batch| Producer[Producer API - FastAPI]
    Producer -->|202 Accepted| Client
    
    Producer -->|Router: static| SQSStatic[(SQS - scraping-tasks-static)]
    Producer -->|Router: dynamic| SQSDynamic[(SQS - scraping-tasks-dynamic)]
    
    subgraph Cluster de Workers Estáticos
        WorkerS1[Worker Static 1]
        WorkerS2[Worker Static 2]
    end

    subgraph Cluster de Workers Dinámicos
        WorkerD1[Worker Dynamic 1]
        WorkerD2[Worker Dynamic 2]
    end

    SQSStatic -->|Fetch batch| WorkerS1
    SQSStatic -->|Fetch batch| WorkerS2
    
    SQSDynamic -->|Fetch batch| WorkerD1
    SQSDynamic -->|Fetch batch| WorkerD2

    WorkerS1 -.->|Fallo crítico > Max Retries| DLQStatic[(DLQ - scraping-tasks-dlq-static)]
    WorkerD1 -.->|Fallo crítico > Max Retries| DLQDynamic[(DLQ - scraping-tasks-dlq-dynamic)]
    
    WorkerS2 -->|ACK - Delete Batch| SQSStatic
    WorkerD2 -->|ACK - Delete Batch| SQSDynamic
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
- **Doble cola física por tipo de tarea**: Se utilizan colas independientes para tareas estáticas (`scraping-tasks-static`) y dinámicas (`scraping-tasks-dynamic`). Esto evita que el procesamiento pesado de navegadores dinámicos bloquee las tareas estáticas ultrarrápidas.
- **Desacoplamiento total**: El Producer puede encolar 100.000 tareas en segundos; el clúster de Workers las irá procesando a su propio ritmo de manera fluida.
- **Garantía de entrega (At-least-once)**: Si un Worker falla a mitad del procesamiento de una tarea, el mensaje vuelve a estar visible en la cola después de que expire su tiempo de visibilidad para ser procesado por otro Worker saludable.
- **DLQs Dedicadas (Dead Letter Queue)**: Cuando una tarea supera el número máximo de reintentos (`max_retries`, por defecto 10), SQS la desvía automáticamente a su correspondiente cola de descarte (`scraping-tasks-dlq-static` o `scraping-tasks-dlq-dynamic`). Esto evita que un error persistente (como una URL inexistente o formato corrupto) sature los hilos de ejecución indefinidamente.
- **Fetch Concurrente Seguro**: El adaptador SQS realiza peticiones paralelas de descarga y espera a que todas finalicen (`asyncio.gather`), evitando cancelaciones abruptas de peticiones en vuelo que causarían "fugas de visibilidad" en SQS.

---

### 3. Worker — El Motor de Extracción (100% Funcional)

El **Worker** es un microservicio autónomo y altamente concurrente encargado de la ejecución del scraping. Consume mensajes de SQS, analiza su carga útil, asigna los recursos necesarios y delega la extracción a su factoría de parsers.

#### Características Clave del Worker:
- **Concurrencia Controlada**: Utiliza un semáforo asíncrono (`asyncio.Semaphore`) limitado por `WORKER_NUM_MAX_CONCURRENT_TASKS` para evitar saturar el ancho de banda local y prevenir bloqueos de CPU por exceso de tareas en vuelo.
- **Apagado Seguro (Graceful Shutdown)**: Captura las señales de parada del sistema (`SIGINT`, `SIGTERM`). Ante una señal de detención, el motor de forma coordinada:
  1. Deja de recibir nuevas tareas de SQS de forma inmediata.
  2. Espera a que todas las tareas en vuelo terminen de procesarse de manera limpia.
  3. Realiza un vaciado síncrono del buffer de memoria (**drenado de RAM**) hacia S3 para todos los trabajos activos, previniendo la pérdida de datos volátiles.
  4. Encola los ACKs de los mensajes correspondientes y espera a que el flusher asíncrono termine de vaciar la cola de borrados en SQS.
  5. Cierra las conexiones y sockets del cliente de almacenamiento (S3) y del broker (SQS) de forma limpia.
- **Factoría y Extractor Universal de Parsers (`ParserFactory` & `UniversalDOMExtractor`)**: Mapea dinámicamente el campo `parser_type` de la tarea al motor de ejecución adecuado e integra el extractor estructurado universal:
  - `STATIC_CSS`: Extractor estático basado en `httpx` / BeautifulSoup, libre de bloqueos TLS 403.
  - `DINAMIC_PLAYWRIGHT`: Extractor para contenido dinámico con renderizado Javascript en Chromium headless y evasión antibot (`playwright-stealth`).
  - **Esquema de Extracción Universal**: Soporta tanto **Entidades Únicas** como **Colecciones de Ítems** mediante `container` y selección de atributos HTML (`href`, `src`, `data-*`) vía `FieldSpec`.
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

### 5. Almacenamiento y Buffer de Datos (S3Data Lake)

El sistema implementa un sumidero de datos (**Data Sink**) asíncrono y resiliente basado en Amazon S3. El objetivo es estructurar, empaquetar y almacenar los datos raspados de forma masiva reduciendo al mínimo el tráfico y las llamadas de I/O de red.

#### Componentes delData Lake:
- **BaseStorageRepository / S3StorageRepository**: Interfaz de almacenamiento abstracto que conecta asíncronamente con Amazon S3 mediante la librería `aioboto3`. Utiliza una única conexión persistente (Singleton) optimizada para soportar un pool de hasta 250 conexiones simultáneas con reintentos automáticos.
- **Buffer de Trabajos (`JobBufferService`)**: Un servicio intermedio que acumula los resultados procesados (`ParseResult`) en la memoria RAM del Worker. Los registros se almacenan segregados por su identificador de trabajo (`job_id`).
- **Serialización Anticipada**: Los datos se serializan a formato JSON inmediatamente al ser recibidos en memoria. Esto amortiza el coste de CPU y permite evaluar en tiempo real el tamaño acumulado en bytes, evitando picos de latencia de red.

#### Criterios de Volcado (Flush):
El buffer realiza la escritura a S3 cuando se cumple cualquiera de las siguientes condiciones:
1. **Límite de tamaño**: El buffer de un Job alcanza o supera el tamaño configurado en bytes (por defecto, `3 MB` en `max_bytes`).
2. **Límite de tiempo**: El buffer de un Job ha estado en memoria por más tiempo del configurado (por defecto, `60 segundos` en `max_seconds`).
3. **Ticker Supervisor**: Un bucle asíncrono en segundo plano inspecciona la memoria RAM cada 5 segundos y fuerza el volcado de los buffers de baja frecuencia de entrada que hayan superado el tiempo de expiración.

#### Particionado Hive en S3:
El volcado masivo concatena los registros en formato **JSON Lines (.jsonl)** y los guarda utilizando una estructura de directorios compatible con motores de consulta analítica (como Athena o Spark) mediante particiones Hive:
```
raw-data/job_id={job_id}/part-{worker_id}-{timestamp}.jsonl
```

#### Garantía At-Least-Once:
Para garantizar la integridad total de los datos y evitar pérdidas ante apagados abruptos:
1. El Worker extrae los datos de la tarea.
2. Los datos se inyectan en el `JobBufferService`.
3. Al cumplirse un límite, los datos agrupados se escriben físicamente en S3.
4. **Solo cuando la subida a S3 es exitosa**, las tareas correspondientes se envían a la cola `ack_queue` para ser eliminadas de SQS. Si la subida a S3 falla, las tareas no se confirman y volverán a estar visibles en la cola SQS de manera automática tras expirar su Visibility Timeout.

---

## 📊 Resumen de Componentes

| Servicio | Rol | Protocolo / Transporte | Estado |
| :--- | :--- | :--- | :--- |
| **Producer** | Ingesta, Validación (Pydantic), Encolado (Batching) | HTTP REST (FastAPI) | **Completado** |
| **Transporte** | Búfer temporal y persistencia de tareas | AWS SQS | **Completado** |
| **Dead Letter** | Captura y cuarentena de tareas defectuosas | AWS SQS DLQ | **Completado** |
| **Worker** | Consumo asíncrono, Concurrencia, Parsers, Reintentos | asyncio + aiohttp + Playwright | **Completado** |
| **Proxies** | Evasión de bloqueos, Sticky Sessions | Static Pool & Backconnect | **Completado** |
| **Almacenamiento** | Data Lake (JSON Lines particionado por Job) | aioboto3 (S3 API) | **Completado** |

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
│   │   ├── storage/                # Persistencia de datos raspados (Base, S3Adapter)
│   │   └── task/                   # Adaptador de consumo asíncrono de SQS
│   ├── scraping/                   # Motores de análisis y extracción
│   │   ├── parsers/                # Motores de parseo (StaticCSSParser, DynamicPlaywrightParser) y factorías
│   │   ├── security/               # Capa activa de seguridad Anti-Bot (HoneypotGuard)
│   │   ├── services/               # Servicios de scraping (JobBufferService)
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
SQS_QUEUE_URL=http://emulator-aws:4566/000000000000/scraping-tasks-static
SQS_QUEUE_URL_DYNAMIC=http://emulator-aws:4566/000000000000/scraping-tasks-dynamic
SQS_REGION=us-east-1

# S3
S3_ENDPOINT_URL=http://emulator-aws:4566
S3_BUCKET_NAME=scraping-raw-data
S3_REGION=us-east-1

# Servicios
NUM_MAX_TASKS=10
PRODUCER_PORT=8000
PRODUCER_HOST=0.0.0.0
DEBUG_MODE=True
WORKER_NUM_MAX_CONCURRENT_TASKS_STATIC=60
WORKER_NUM_MAX_CONCURRENT_TASKS_DYNAMIC=3

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
1. **`emulator-aws` (Floci)** en el puerto `4566`. Ejecuta automáticamente `infra/init-aws.sh` inicializando las colas principales (`scraping-tasks-static` y `scraping-tasks-dynamic`) junto con sus respectivas Dead Letter Queues.
2. **`producer`** en el puerto `8000`. Expone la API para inyectar tareas.
3. **`worker-static`** y **`worker-dynamic`**. Consumidores especializados de tareas en segundo plano. Puedes escalar los pools de workers independientemente si quieres comprobar el reparto de carga:
   ```bash
   docker-compose up -d --scale worker-static=3 --scale worker-dynamic=2
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
Para consumir tareas de la cola estática:
```bash
cd worker
uv sync
SQS_QUEUE_URL=http://localhost:4566/000000000000/scraping-tasks-static uv run python main.py
```
Para consumir tareas de la cola dinámica (Playwright):
```bash
cd worker
uv sync
SQS_QUEUE_URL=http://localhost:4566/000000000000/scraping-tasks-dynamic uv run python main.py
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
- [x] **Parsers Dinámicos**: Implementación de extracción mediante Playwright/Selenium para webs dinámicas con renderizado JS (usando `playwright-stealth`).
- [x] **Almacenamiento en S3**: Integración asíncrona para guardar el contenido extraído en formato JSON Lines segregado por Job con control de reintentos.
- [ ] **Base de Datos de Estado**: Guardado de estados intermedios y de-duplicación agresiva de URLs para evitar procesados dobles.
- [ ] **Despliegue Continuo (CD)** automatizado para subidas directas a la nube.
- [ ] **Migración a K8s & Terraform**: Despliegue en clusters elásticos basados en Kubernetes con aprovisionamiento IaaC.
- [ ] **Forward Proxy de Infraestructura**: Configuración opcional basada en proxies robustos (Envoy / mitmproxy) para rotación a bajo nivel de red.
- [ ] **Dashboard de Observabilidad**: Panel de control interactivo (Redis + Grafana o Streamlit) para monitoreo de colas y rendimiento del clúster de workers.
- [ ] **IA Parsing**: Integración de LLMs locales y de API para el parseado adaptativo de webs sin depender de selectores rígidos.
