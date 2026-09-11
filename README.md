# A365 VigIA

**Asistente de reuniones ejecutivas con IA que escucha, detecta afirmaciones de negocio, las contrasta contra fuentes oficiales y genera alertas con evidencia.**

---

## Que es VigIA

VigIA no es un LLM que escucha todo y opina sobre todo. Es un pipeline donde cada etapa reduce ruido y solo usa IA generativa cuando existe un caso relevante. El sistema:

- **Escucha** el audio de una reunion ejecutiva en tiempo real.
- **Transcribe** la voz a texto con Google Cloud Speech-to-Text.
- **Detecta** afirmaciones de negocio verificables (KPIs, metricas, campanias).
- **Contrasta** los datos mencionados contra `seguimiento_financiero` o un snapshot certificado.
- **Alerta** con evidencia cuando hay contradicciones o riesgos disfrazados.
- **Actúa** con perfil de Director Financiero, Director de Operaciones o perfil mixto.
- **Genera** una minuta ejecutiva al cerrar la sesion.

---

## Arquitectura

```text
Audio / texto
  │
  ▼
┌─────────────────────────┐
│   STT Streaming         │  Google Cloud Speech-to-Text
│   (WebM/Opus → texto)   │  WebSocket en tiempo real
└───────────┬─────────────┘
            │
            ▼
┌─────────────────────────┐
│   Normalizacion         │  Limpieza, lowercase, deduccion
│   de transcripcion      │
└───────────┬─────────────┘
            │
            ▼
┌─────────────────────────┐
│   Detector rapido       │  Regex + diccionarios
│   de afirmaciones       │  KPIs, campanias, stance, valores
└───────────┬─────────────┘
            │
            ▼
┌─────────────────────────┐
│   Consulta a fuentes    │  seguimiento_financiero (HTTPS)
│   oficiales             │  o snapshot local validado
└───────────┬─────────────┘
            │
            ▼
┌─────────────────────────┐
│   Motor de contraste    │  Comparacion deterministica
│                         │  ¿Valor dicho vs. valor real?
│                         │  ¿Riesgo disfrazado de normalidad?
└───────────┬─────────────┘
            │
            ▼
┌─────────────────────────┐
│   Alerta + Evidencia    │  Severidad: silent / warning / critical
│                         │  Mensaje con fuente, metrica y valor
└───────────┬─────────────┘
            │
            ▼
┌─────────────────────────┐
│   Minuta ejecutiva      │  Decisiones, riesgos, alertas
│   (cierre de sesion)    │  Exportable en texto
└─────────────────────────┘
```

### Principios de diseno

| Principio | Implementacion |
|---|---|
| **Silencio inteligente** | Si no hay contradiccion verificable, VigIA no interviene. |
| **Evidencia obligada** | Toda alerta incluye fuente oficial, metrica, valor actual y meta. |
| **Motor deterministico** | La deteccion de afirmaciones usa regex y diccionarios, no LLM. |
| **LLM como redactor** | El LLM solo se usa para redactar, no como fuente de verdad. |
| **Arquitectura por capas** | Cada etapa es independiente, testeable y reemplazable. |

---

## Stack tecnico

| Capa | Tecnologia | Estado |
|---|---|---|
| **Frontend** | React + Vite + TypeScript | Operativo |
| **Backend** | Python + FastAPI | Operativo |
| **STT** | Google Cloud Speech-to-Text (streaming) | Operativo |
| **Datos oficiales** | JSON validado o API HTTPS `seguimiento_financiero` | Operativo |
| **Base de datos** | PostgreSQL + pgvector | Preparado (Docker Compose) |
| **Cache** | Redis | Preparado (Docker Compose) |
| **Audio realtime** | LiveKit / WebRTC | Planeado |
| **IA generativa** | Gemini como redactor, nunca como fuente de verdad | Operativo |
| **Contenedores** | Docker Compose | Configurado |

---

## Experiencia web

| Ruta | Uso |
|---|---|
| `/` | Landing publica y explicacion del producto |
| `/login` | Acceso protegido al workspace |
| `/app` | Flujo unico: preparacion, reunion en vivo, resumen y datos financieros |

La interfaz es responsive, incluye navegacion lateral en escritorio y menu
compacto en movil. La reunion se gestiona en tres pasos dentro del mismo modulo:
preparar, ejecutar en vivo y revisar el resumen. El panel financiero muestra
seis cuentas por pagina, permite buscar por nombre y conserva como unica fuente
los datos reales de `seguimiento_financiero`.

---

## Estructura del proyecto

```text
vigia-pry/
├── apps/
│   ├── api/                        Backend FastAPI
│   │   ├── main.py                 Endpoints REST + WebSocket
│   │   ├── vigia/auth.py           Sesion firmada y autenticacion
│   │   ├── requirements.txt        Dependencias Python
│   │   ├── vigia/
│   │   │   ├── core.py             Motor de deteccion y contraste
│   │   │   ├── financial_data.py   Conector y validacion de seguimiento_financiero
│   │   │   └── google_speech.py    Integracion Google STT
│   │   └── tests/
│   │       └── test_core.py        Tests del motor de analisis
│   └── web/                        Frontend React/Vite
│       ├── src/
│       │   ├── App.tsx             Vision ejecutiva y datos oficiales
│       │   ├── AppShell.tsx        Navegacion del workspace
│       │   ├── LandingPage.tsx     Landing publica
│       │   ├── LoginPage.tsx       Acceso al sistema
│       │   ├── jarvis/             Sala de voz IA
│       │   ├── design.css          Sistema visual responsive
│       │   ├── types.ts            Tipos TypeScript
│       │   └── main.tsx            Router y control de acceso
│       ├── index.html
│       ├── package.json
│       ├── vite.config.ts
│       └── tsconfig.json
├── data/
│   └── bi_snapshot.json            Fuente oficial simulada (KPIs)
├── docs/
│   ├── architecture.md             Diseno tecnico detallado
│   └── roadmap.md                  Fases de implementacion
├── .secrets/                       Credenciales (gitignored)
├── .env.example                    Plantilla de variables de entorno
├── docker-compose.yml              PostgreSQL + Redis
├── .gitignore
└── README.md
```

---

## Quick start

### 1. Backend (FastAPI)

```bash
cd apps/api
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload --host 127.0.0.1 --port 8080
```

El backend arranca en `http://127.0.0.1:8080`.

### 2. Frontend (React + Vite)

```bash
cd apps/web
npm install
npm run dev
```

El frontend arranca en `http://localhost:5173` y se conecta al backend automaticamente.

Para cambiar la URL del backend:

```bash
VITE_API_BASE_URL=http://127.0.0.1:8080 npm run dev
```

### 3. Docker (aplicación completa)

```bash
cp .env.example .env  # solo si todavía no existe .env
docker compose up -d --build
docker compose ps
```

Esto levanta:

| Servicio | Puerto | Imagen |
|---|---|---|
| VigIA web | 5173 | Nginx + frontend compilado |
| VigIA API | 8080 | Python 3.12 + FastAPI |
| PostgreSQL + pgvector | 5432 | `pgvector/pgvector:pg16` |
| Redis | 6379 | `redis:7-alpine` |

Abre `http://localhost:5173`; la landing es publica y el workspace solicita las
credenciales configuradas en `.env`. La web sirve la API y el WebSocket de
transcripcion por el mismo origen. Para
revisar la ejecución o reconstruir después de un cambio:

```bash
docker compose logs -f api web
docker compose up -d --build
```

---

## API Endpoints

Las rutas bajo `/api/*` requieren una sesion valida, excepto `/api/auth/*`.
Configura las credenciales y la firma de sesion en `.env`:

```bash
VIGIA_ADMIN_USER=admin
VIGIA_ADMIN_PASSWORD=cambia-esta-contrasena
VIGIA_SESSION_SECRET=genera-un-secreto-aleatorio-largo
VIGIA_SESSION_TTL_SECONDS=28800
VIGIA_COOKIE_SECURE=false  # usa true al publicar exclusivamente con HTTPS
```

### Autenticacion

```http
POST /api/auth/login
GET /api/auth/me
POST /api/auth/logout
```

El login crea una cookie `HttpOnly`, firmada, con expiracion y `SameSite=Lax`.
Los WebSockets de voz y transcripcion tambien validan la sesion.

### Health check

```http
GET /health
```

```json
{ "status": "ok" }
```

### Estado de Google Speech

```http
GET /api/speech-status
```

```json
{
  "configured": true,
  "credentials_path": "/path/to/.secrets/google-speech.json",
  "language": "es-PE",
  "model": "default"
}
```

### Snapshot BI (datos oficiales)

```http
GET /api/bi-snapshot
```

Devuelve el JSON completo con campanias, metricas, metas y valores actuales.

### Estado de seguimiento_financiero

```http
GET /api/data-source/status
POST /api/data-source/refresh
```

Informa proveedor, fuente, fecha de corte, antiguedad, disponibilidad y si la lectura es suficientemente vigente para corregir a un participante.

### Transcripcion individual (audio → texto)

```http
POST /api/transcribe
Content-Type: multipart/form-data
```

| Parametro | Tipo | Descripcion |
|---|---|---|
| `audio` | File | Archivo WebM/Opus (max 8MB) |

```json
{
  "text": "El margen bruto de Claro esta en 21.6%",
  "confidence": 0.87,
  "provider": "google-cloud-speech",
  "content_type": "audio/webm"
}
```

### Transcripcion streaming (WebSocket)

```http
WS /api/transcribe-stream?mime_type=audio/webm;codecs=opus
```

El cliente envia chunks de audio binarios. El servidor responde con mensajes JSON:

```json
{ "type": "ready" }
{ "type": "transcript", "text": "El SLA esta...", "is_final": false, "confidence": null }
{ "type": "transcript", "text": "El SLA esta normal.", "is_final": true, "confidence": 0.91 }
{ "type": "closed" }
```

### Analisis de afirmacion

```http
POST /api/analyze
Content-Type: application/json
```

```json
{
  "speaker": "Gerente BCP",
  "role": "Gerente Operaciones",
  "text": "BCP esta estable, el SLA esta normal y sin riesgo."
}
```

Respuesta:

```json
{
  "should_respond": true,
  "severity": "critical",
  "category": "business_fact_check",
  "message": "ALERTA: Gerente BCP menciono un dato que requiere contraste...",
  "detected_claims": [...],
  "evidence": [
    {
      "source": "BI A365 - Corte 2025-07-01",
      "campaign": "BCP",
      "metric": "SLA contractual",
      "actual": 88.2,
      "target": 95.0,
      "direction": "min",
      "unit": "%"
    }
  ]
}
```

### Cierre de sesion (minuta)

```http
POST /api/end-session
Content-Type: application/json
```

```json
{
  "meeting_type": "Comite ejecutivo",
  "transcript": [...],
  "alerts": [...],
  "session_started_at": "2025-07-02T14:00:00Z",
  "session_ended_at": "2025-07-02T15:30:00Z",
  "duration_seconds": 5400
}
```

Devuelve resumen estadistico, decisiones detectadas, riesgos y minuta en formato Markdown.

---

## Motor de deteccion

El motor de analisis (`apps/api/vigia/core.py`) detecta afirmaciones verificables sin depender de un LLM:

### Metricas reconocidas

| Clave | Alias detectados en texto |
|---|---|
| `sla` | sla, nivel de servicio, servicio contractual |
| `gross_margin` | margen, margen bruto, gross margin, rentabilidad |
| `ebitda` | ebitda, utilidad operativa |
| `attrition` | rotacion, atricion, bajas |

### Posturas detectadas

| Postura | Palabras clave |
|---|---|
| **Positiva** | estable, normal, bien, cumple, dentro de rango, sin riesgo |
| **Negativa** | critico, riesgo, debajo, incumple, fuera de rango, caida |
| **Neutral** | Sin coincidencia con las anteriores |

### Reglas de alerta

| Tipo | Severidad | Condicion |
|---|---|---|
| **Riesgo disfrazado** | `critical` | Dato presentado como "normal" pero BI muestra incumplimiento |
| **Valor incorrecto** | `warning` | Valor mencionado supera la tolerancia del KPI (0.5 puntos por defecto para %) |
| **Falso riesgo** | `warning` | Se declara como critico un KPI que si cumple |
| **Reconocimiento correcto** | `info` | La persona reconoce correctamente una brecha; no se interrumpe |
| **Sin contradiccion** | `silent` | No se genera alerta (silencio inteligente) |

---

## Funcionalidades actuales

- Captura de audio desde el navegador con `getUserMedia`.
- Streaming de audio al backend via WebSocket.
- Transcripcion en tiempo real con Google Cloud Speech-to-Text.
- Fallback a reconocimiento del navegador si Google STT no esta disponible.
- Visualizacion de nivel de audio en vivo.
- Transcripcion manual como alternativa.
- Deteccion automatica de KPIs, campanias y valores mencionados.
- Deteccion de varias metricas y valores dentro de una misma frase.
- Contraste contra snapshot local o API HTTPS de `seguimiento_financiero`.
- Validacion estricta del contrato, control de frescura y cache de ultima lectura valida.
- Contexto reciente de reunion para inferir la campania cuando la frase no la repite.
- Modo directivo: confronta datos erroneos, promesas no respaldadas y afirmaciones generales de "todo bajo control".
- Perfiles CFO, COO y mixto con acciones recomendadas distintas.
- Umbral de voz configurable y cooldown de alertas repetidas.
- Recomendaciones operativas, financieras y comerciales dentro de la intervencion.
- Validacion de promesas contra historico minimo del snapshot BI.
- Alertas criticas con evidencia citada.
- Intervencion por voz con Gemini 3.1 TTS preview y fallback latinoamericano de Google Cloud TTS.
- Cierre de sesion con minuta ejecutiva automatica.
- Panel de datos oficiales en tiempo real.
- Landing publica, login y rutas privadas con sesion firmada.
- Panel responsive con busqueda y paginacion de cuentas.
- Sala de voz rediseñada con estados visuales y animaciones discretas.

## Integracion con seguimiento_financiero

Configura el acceso de solo lectura en `.env`:

```bash
VIGIA_DATA_PROVIDER=http
SEGUIMIENTO_FINANCIERO_URL=http://host.docker.internal:3000/api/external
SEGUIMIENTO_FINANCIERO_SENSITIVE_URL=http://app:3000/api/external  # opcional, solo red privada
SEGUIMIENTO_FINANCIERO_API_KEY=api-key-de-solo-lectura
VIGIA_ALLOW_INSECURE_DATA_URL=true  # solo para el enlace Docker local
VIGIA_ALLOW_INSECURE_SENSITIVE_DATA_URL=true
VIGIA_DATA_REFRESH_SECONDS=300
VIGIA_DATA_MAX_AGE_SECONDS=900
```

El adaptador detecta el último período disponible y cubre todas las fuentes del
contrato SIFO 1.1: cuentas, campañas, histórico, facturación, presupuesto,
dotación, estructura, ratios, operación, asistencia, calidad, IFC y detalle
individual de planilla. Los conjuntos grandes se consultan con paginación bajo
demanda; la planilla individual exige el permiso `agents.read_sensitive` y no se
incorpora al contexto ejecutivo general. Nunca se completan datos con métricas
ficticias. El contrato JSON, reglas de seguridad y opciones de autenticación se detallan en
[`docs/integration-seguimiento-financiero.md`](docs/integration-seguimiento-financiero.md).

---

## Google Speech-to-Text

Para activar la transcripcion real con Google Cloud:

1. Crea una cuenta de servicio en Google Cloud Console.
2. Descarga el archivo JSON de credenciales.
3. Colocalo en:

```text
.secrets/google-speech.json
```

4. Configura las variables en `.env`:

```bash
GOOGLE_APPLICATION_CREDENTIALS=.secrets/google-speech.json
GOOGLE_SPEECH_LANGUAGE=es-PE
GOOGLE_SPEECH_MODEL=default
GOOGLE_SPEECH_HINT_BOOST=16
```

VigIA envia a Speech-to-Text los nombres y metricas cargados desde
`seguimiento_financiero` para mejorar el reconocimiento de cuentas como FUVEX,
ENTEL CH o CLARO PERÚ.

La carpeta `.secrets/` esta excluida de Git por `.gitignore`.

---

## Tests

```bash
cd apps/api
python3 -m unittest discover -s tests
```

Los tests cubren el motor de deteccion, contraste, integracion de datos y
autenticacion sin dependencias externas.

---

## Roadmap

### Fase 0 — Vertical slice local (actual)

- Entrada manual + transcripcion por audio.
- Contraste contra snapshot BI local.
- Alertas con evidencia.
- Minuta basica.

### Fase 1 — MVP operativo

- Persistencia en PostgreSQL.
- Sesiones reales con historico.
- Carga de snapshots BI.
- Exportacion Markdown/PDF.
- Gestion de usuarios y roles persistentes.

### Fase 2 — Audio real

- LiveKit para sala realtime (WebRTC).
- Deepgram o Google STT streaming continuo.
- Audio desde microfono de sala.
- Diarizacion por hablante.
- Medicion de latencia en sala real.

### Fase 3 — Conocimiento corporativo

- pgvector para embeddings.
- Carga de contratos, actas y reportes.
- RAG con fuentes citadas.
- Versionado de documentos.
- Permisos por fuente.

### Fase 4 — Intervencion asistida

- Panel para facilitador.
- Alertas silenciosas (solo facilitador ve).
- Boton de intervencion humana.
- Voz activa solo para riesgos criticos.

### Fase 5 — Produccion ejecutiva

- Auditoria completa.
- Retencion de datos.
- Consentimiento y privacidad.
- Monitoreo de costos (STT, LLM).
- Evaluacion de precision.
- Runbooks de operacion.

---

## Licencia

Proyecto privado — A365.
