# Jobs — Catálogo de Trabajos Programados y Procesamiento Batch

El módulo de **Jobs** es un contenedor extensible diseñado para alojar tareas programadas, scripts de mantenimiento, procesos de ETL y cualquier tarea batch periódica necesaria para la plataforma de scraping distribuido. 

Este directorio está diseñado para crecer de forma modular, permitiendo añadir nuevos scripts independientes sin interferir con la lógica de los trabajos existentes.

---

## 📂 Estructura del Módulo

Cualquier nuevo script batch debe colocarse en la raíz de este módulo y definir sus propios esquemas o interfaces dentro de las carpetas correspondientes:

```text
jobs/
├── config/              # Parámetros y variables de entorno globales
├── interfaces/          # Modelos de validación (Pydantic) específicos de cada job
├── test/                # Suite de pruebas unitarias e integración de los jobs
├── README.md            # Documentación general y catálogo de jobs
└── compact_s3.py        # [Job] Compactador de Data Lake (Primer job activo)
```

---

## 📖 Catálogo de Jobs Activos

---

### 1. Compactador de Data Lake (`compact_s3.py`)

Este job es responsable de mitigar el **problema de los archivos pequeños** (*Small Files Problem*). Consolida los ficheros JSON Lines de la Landing Zone en S3, transformándolos a formato columnar estructurado de alta densidad (**Parquet**) con compresión **ZSTD**.

#### 🏗️ Arquitectura del Job de Compactación
El proceso se ejecuta de manera asíncrona concurrente con `asyncio` y un pool de hilos nativos para la compresión CPU-bound:

```mermaid
graph TD
    LandingZone[(S3 Landing Zone - raw-data/)] -->|1. List prefixes| Discovery[Descubrimiento de Carpetas]
    Discovery -->|2. Concurrencia Limitada| Metadata[Análisis de Metadatos y Tamaños]
    Metadata -->|3. Evaluar Umbrales| Criteria{¿>= 300MB o 30m Inactivo?}
    
    Criteria -->|No| Ignore[Ignorar Job]
    Criteria -->|Sí| Compactor[Motor de Compactación - process_job]
    
    subgraph Ciclo de Vida de la Compactación
        Compactor -->|4. Descarga asíncrona| Stream[Stream get_object]
        Stream -->|5. Validación| Validate[ParseResult Pydantic V2]
        Validate -->|6. Búfer RAM Columnar| Buffer[Tabla PyArrow]
        Buffer -->|7. Elastic Chunking| LimitCheck{¿Alcanza 1M de Registros?}
        
        LimitCheck -->|Zona Elástica / Absorción| Buffer
        LimitCheck -->|Flush / Split| Serialize[Serializar Parquet C++ ZSTD en RAM]
    end
    
    Serialize -->|8. Subida PUT| CompactedZone[(S3 Compacted Zone - compacted-data/)]
    CompactedZone -->|9. Confirmar Persistencia| Cleaner[Limpieza Landing Zone]
    Cleaner -->|10. Borrado Masivo S3| LandingZone
```

#### 🚀 Características Clave del Job
* **Elastic Chunking (Look-Ahead)**: Cuando el acumulador alcanza el límite de `MAX_RECORDS_PER_FILE` (1,000,000), inspecciona la cola pendiente en S3. Si el resto de registros caben dentro del margen de elasticidad (`TOLERANCIA_COLA = 200,000`), el motor absorbe todo en un único archivo Parquet óptimo en lugar de fragmentarlo.
* **Serialización RAM + Subida Asíncrona**: Para evitar las incompatibilidades del multipart de PyArrow C++ en entornos locales de simulación/mocks, la conversión columnar se realiza en memoria en un hilo secundario (`asyncio.to_thread`) y la red se delega a `aioboto3` mediante peticiones PUT asíncronas no bloqueantes.
* **Esquema Resiliente Columnar**: Estructura las consultas mediante tipado fuerte en las columnas de control analítico, pero empaqueta el contenido útil polimórfico en una columna `data` de tipo cadena JSON para prevenir roturas ante cambios en los campos extraídos de la web.

---

### 2. [Futuros Jobs] (Mantenimiento, Backups, Cargas ETL, etc.)
Para añadir un nuevo Job:
1. Crea tu archivo script en la raíz de `jobs/` (ej. `limpieza_temporales.py`).
2. Agrega las configuraciones necesarias en [jobs/config/settings.py](file:///C:/proyectos/WebScrapingDistributed/jobs/config/settings.py).
3. Añade su descripción técnica y su flujo en este catálogo para mantener la visibilidad arquitectónica del sistema.

---

## ⚙️ Configuración y Variables de Entorno Globales

El módulo comparte un objeto centralizado de configuración mediante `jobs/config/settings.py` alimentado por el archivo `.env` en su raíz:

| Variable | Tipo | Por Defecto | Descripción |
| :--- | :--- | :--- | :--- |
| `S3_ENDPOINT_URL` | String | `http://localhost:4566` | Endpoint del almacenamiento de objetos S3. |
| `S3_BUCKET_NAME` | String | `scraping-raw-data` | Nombre del bucket del Data Lake. |
| `S3_PREFIX_RAW_ZONE` | String | `row-data` | Carpeta virtual de la Landing Zone de entrada. |
| `S3_PREFIX_COMPACTED_DATA` | String | `compacted-data` | Carpeta virtual de destino para datos compactados Parquet. |
| `AWS_ACCESS_KEY_ID` | String | `test` | Clave de acceso de AWS. |
| `AWS_SECRET_ACCESS_KEY` | String | `test` | Clave secreta de acceso de AWS. |
| `DEFAULT_REGION_AWS` | String | `us-east-1` | Región por defecto de AWS. |

---

## 🛠️ Ejecución y Suite de Pruebas

### Lanzamiento de Jobs Individuales
Los jobs se invocan de forma independiente desde su raíz usando el gestor de dependencias `uv`:
```bash
# Lanzar el compactador de S3
uv run compact_s3.py

# Lanzar futuros jobs (ejemplo)
uv run limpieza_temporales.py
```

### Batería de Pruebas Integradas
Las pruebas se localizan en `jobs/test/` y simulan la infraestructura AWS usando un servidor HTTP local de Moto en memoria para aislamiento absoluto:
```bash
# Ejecutar todos los tests del módulo
uv run pytest

# Inspeccionar reporte de cobertura de código
uv run pytest --cov=. --cov-report=term-missing
```
