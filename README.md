# Distributed High-Scale Scraping System

Sistema de ingesta y procesamiento de datos a gran escala diseñado bajo principios de arquitectura distribuida y event-driven design.

Su objetivo principal es permitir el procesamiento asíncrono de millones de URLs, garantizando escalabilidad, resiliencia y desacoplamiento total entre la captación de datos y su ejecución.

Ademas este proyecto lo estoy usando con el objetivo de aprender en el proceso con el enfoque de ver hasta donde puede llegar

## 📌 Visióngenerall

A diferencia de los scrapers tradicionales, este sistema utiliza el patrón "Fire and Forget" (202 Accepted).

El usuario envía una carga masiva de trabajo y recibe una respuesta instantánea, mientras el sistema orquesta la distribución de tareas en segundo plano a través de colas de mensajería (SQS).

### Key Features

| Feature                           | Descripción                                                                             |
| --------------------------------- | --------------------------------------------------------------------------------------- |
| Escalabilidad horizontal          | Diseñado para manejar picos de tráfico distribuyendo la carga en workers independientes |
| Arquitectura asíncrona            | Implementación nativa con FastAPI y `asyncio` para evitar bloqueos de I/O               |
| Contratos de Datos Robustos       | Validación estricta mediante Pydantic                                                   |
| Infraestructura Cloud-Native      | Preparado para AWS (SQS/DLQ) y simulado localmente con LocalStack                       |

## 🏗️ Arquitectura del Sistema

El sistema implementa el patrón Productor-Cola-Consumidor con desacoplamiento total entre quien genera trabajo y quien lo ejecuta.

Esto permite que ambos lados escalen de forma independiente y que ninguna tarea se pierda aunque los workers fallen.

### Producer — El Orquestador

El Producer es una API FastAPI que no ejecuta scraping. Su única responsabilidad es recibir lotes de URLs, validarlos y despacharlos a la cola.

> [!NOTE]  
> Responde con `202 Accepted` de forma inmediata (`patrón Fire and Forget`), sin esperar a que el scraping termine.

#### ¿Por qué FastAPI + aioboto3?

- FastAPI con `asyncio` permite recibir miles de peticiones concurrentes sin bloquear hilos de I/O mientras espera la confirmación de SQS.

- `aioboto3` reutiliza una única conexión al cliente SQS (patrón singleton en \_get_client()), evitando el coste de abrir una nueva conexión por cada lote.

- El envío se hace en lotes (`SendMessageBatch`) de hasta `NUM_MAX_TASKS` mensajes, reduciendo el número de llamadas a la API de SQS.

#### Pydantic para la validación:

- Cada tarea (`ScrapingTask`) lleva consigo toda la configuración que el worker necesita: la URL, el tipo de parser, los selectores CSS, la prioridad y los límites de reintento. Si el contrato falla en el Producer, el error se detecta antes de entrar a la cola.

### SQS — Buffer

SQS actúa como intermediario persistente entre el producer y los workers Si todos los workers se caen, los mensajes permanecen en la cola y se procesan cuando vuelvan.

#### Por qué SQS y no una llamada directa:

- Desacopla completamente el ritmo de producción del de consumo. El producer puede enviar 100.000 tareas en segundos; los workers las procesan a su propio ritmo.

- SQS garantiza entrega at-least-once: si un worker falla a mitad de una tarea, el mensaje vuelve a la cola y otro worker lo reintenta.

- La visibilidad del mensaje (**visibility timeout**) evita que dos workers procesen la misma URL simultáneamente.

### DLQ — La Red de Seguridad

Cuando una tarea supera el número máximo de reintentos (`max_retries`, por defecto `3`), SQS la mueve automáticamente a la Dead Letter Queue.

Esto evita que una URL problemática bloquee la cola principal indefinidamente.

#### Por qué una DLQ separada:

- Permite inspeccionar y reprocesar manualmente las tareas fallidas sin afectar al flujo normal.

### Worker —Se encarga del scraping (escalado horizontal)

El worker es el único componente que ejecuta la lógica de scraping. Consume mensajes de SQS, ejecuta el parser indicado en `parser_type` y procesa el resultado.

> [!NOTE]  
> Actualmente este servicio se encuntra en desarrollo, aun que este es el funcionaminto planeado puede que en un futuro este cambie algunas partes

¿Por qué escala horizontalmente?

- Al estar desacoplado de la cola, se pueden levantar N réplicas del worker con un simple `docker-compose up --scale worker=N`. Cada réplica compite por los mensajes.

  de la cola de forma independiente.

- La carga se distribuye automáticamente: si hay 10 workers y 1.000 tareas en cola, cada worker procesa ~100 tareas en paralelo sin coordinación explícita.

- En producción, esto se mapea directamente a Auto Scaling Groups en AWS ECS/EKS (o el que mejor se adapte, pendiente de definir), escalando según la métrica ApproximateNumberOfMessagesVisible de SQS (este será el número de tareas para levantar más instancias).

### Resumen de los distintos servicios y sus responsabilidades

| Servicio               | Responsavilidad                                                                         |
| ---------------------- | --------------------------------------------------------------------------------------- |
| Producer (API)         | Recibe las peticiones, valida los contratos y fragmenta el trabajo en lotes (batching)  |
| Transporte (SQS)       | Actúa como buffer y garantiza que ninguna tarea se pierda                               |
| DLQ                    | Captura tareas que superan el máximo de reintentos                                      |
| Worker (Consumer)      | (En desarrollo) Consume los mensajes y ejecuta la lógica de scraping                    |

## 📁 Organización del Proyecto

La estructura sigue principios de Clean Architecture, separando la lógica de negocio de la infraestructura.

```py
WebScrapingDistributed/
│
├── producer/                  # Microservicio: recibe peticiones y orquesta tareas de scraping
│   ├── config/                # Configuración del servicio
│   ├── dependencies/          # Inyección de dependencias
│   ├── infrastructure/        # Adaptadores de infraestructura (SQS, etc.)
│   ├── scraping/              # Lógica de negocio y controladores de scraping
│   ├── main.py                # Configuracion inicial de FastApi
│   ├── Dockerfile
│   └── README.md              # Documentación específica del servicio
│

├── worker/                    # Microservicio: consumidor y procesamiento de tareas (En desarrollo)
│   ├── main.py                # Configuracion inicial
│   └── Dockerfile
│
├── shared/                    # Código compartido entre servicios
│   └── shared/
│       ├── models/            # Contratos Pydantic compartidos
│       ├── logging.py         # Configuración de logging común
│       └── init.py        # Exportaciones públicas del paquete
│
├── infra/                     # Scripts de infraestructura y despliegue
│   └── init-aws.sh            # Inicialización de colas SQS en LocalStack
│
├── docker-compose.yml         # Orquestación de todos los servicios
├── .env.template              # Plantilla de variables de entorno
└── uv.lock                    # Lockfile de dependencias (uv)
```

## 🚀 Guía deinicio rápidoo

### Requisitos previos

| Herramienta         | Versión mínima  | Descripción                                  |
| ------------------- | --------------- | -------------------------------------------- |
| Python              | 3.13+           | Lenguaje base del proyecto                   |
| Docker              | 20.10+          | Contenedores para los servicios              |
| Docker Compose      | v2+             | Orquestación multi-contenedor                |
| uv                  | latest          | Gestor de dependencias y entornos virtuales  |

> [!NOTE]  
> Si solo vas a levantar el proyecto con Docker, no necesitas tener Python ni uv instalados localmente.

---

### 1. Clonar el repositorio

```bash
git clone https://github.com/ArturoCarrilloJimenez/WebScrapingDistributed.git

cd WebScrapingDistributed
```

### 2. Configurar variables de entorno

Copia la plantilla y rellena los valores:

```bash
cp .env.template .env
```

Contenido del .env con valores por defecto para desarrollo local:

```py
# Credenciales AWS (LocalStack)

DEFAULT_REGION_AWS=us-east-1
AWS_ACCESS_KEY_ID=test
AWS_SECRET_ACCESS_KEY=test
LOCALSTACK_AUTH_TOKEN=<tu_token>

# SQS

SQS_QUEUE_URL=http://localstack:4566/000000000000/scraping-tasks

# Producer

NUM_MAX_TASKS=10
PRODUCER_PORT=8000
```

> [!IMPORTANT]
> LOCALSTACK_AUTH_TOKEN es necesario para arrancar LocalStack. Puedes obtener uno gratuito en localstack.cloud.

### 3. Levantar el proyecto

#### Opción A — Con Docker (recomendado)

Levanta toda la infraestructura con un solo comando:

```bash
docker-compose up -d
```

Esto arrancará tres servicios:

| Servicio   | Puerto  | Descripción                                 |
| ---------- | ------- | ------------------------------------------- |
| producer   | 8000    | API FastAPI que orquesta las tareas         |
| worker     | —       | Consumidor de tareas (sin puerto expuesto)  |
| localstack | 4566    | Emulador de AWS (SQS)                       |

Al iniciar, LocalStack ejecuta automáticamente `infra/init-aws.sh`, que crea la cola principal (`scraping-tasks`) y su Dead Letter Queue (`scraping-tasks-dlq`).

#### Opción B — Desarrollo local (solo Producer, worker en desarrollo)

```bash
cd producer

uv sync

uvicorn main:app --reload --port 8000
```

> [!WARNING]  
> En modo local necesitarás tener LocalStack corriendo por separado (`docker-compose up localstack -d`) para que el Producer pueda conectarse a SQS.

### 4. Verificar que funciona.

Una vez levantado, la API del Producer estará disponible en:

API: http://localhost:8000  

Docs (Swagger): http://localhost:8000/docs

## 📈 Roadmap

- [x] Arquitectura asíncrona "Fire and Forget"
- [x] Integración con SQS y manejo de lotes (batching)
- [x] Sistema de logging estructurado con contexto
- [ ] Implementación del worker con Playwright/Selenium
- [ ] Integración de S3 para el guardado de datos de forma dinámica.
- [ ] Integrar una base de datos para la gestión de estados y gestión de tareas duplicadas.
- [ ] Dashboard de Observabilidad (Redis/Streamlit)
- [ ] Integración de modelos de IA para parsing inteligente
