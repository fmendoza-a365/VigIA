# A365 VigIA

Primer vertical slice para un asistente de reuniones ejecutivas que escucha, detecta afirmaciones de negocio, contrasta contra fuentes oficiales y genera alertas con evidencia.

Este repositorio no intenta simular que ya existe un "Jarvis" completo. La base creada aqui separa el producto en piezas reales:

- Consola web para reunion en vivo.
- API Python para analisis y cierre de sesion.
- Motor testeable de deteccion de afirmaciones y contraste.
- Snapshot BI local como fuente oficial inicial.
- Preparacion para integrar LiveKit, STT streaming y RAG.

## Stack propuesto

- Frontend: React + Vite + TypeScript.
- Backend: Python + FastAPI.
- Datos iniciales: JSON local.
- Produccion futura: PostgreSQL + pgvector + Redis.
- Audio futuro: LiveKit Agents + Deepgram o Google Speech-to-Text.
- IA futura: LLM solo para redaccion/razonamiento cuando el motor detecte un caso relevante.

## Estructura

```text
apps/
  api/                 Backend FastAPI y nucleo de analisis
  web/                 Frontend React/Vite
data/
  bi_snapshot.json     Fuente oficial simulada para KPIs
docs/
  architecture.md      Diseno tecnico recomendado
  roadmap.md           Fases realistas de implementacion
```

## Ejecutar backend

```bash
cd apps/api
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload --host 127.0.0.1 --port 8080
```

## Ejecutar frontend

```bash
cd apps/web
npm install
npm run dev
```

Por defecto el frontend usa `http://127.0.0.1:8080` como API. Puede cambiarse con:

```bash
VITE_API_BASE_URL=http://127.0.0.1:8080 npm run dev
```

## Verificaciones locales sin dependencias externas

El nucleo de analisis puede probarse sin instalar FastAPI:

```bash
cd apps/api
python3 -m unittest discover -s tests
```

## Que hace hoy

- Puede capturar audio desde el navegador y enviar chunks cortos a Google Cloud Speech-to-Text.
- Mantiene entrada manual como fallback de prueba.
- Extrae posibles KPIs, campanas y valores.
- Consulta `data/bi_snapshot.json`.
- Detecta contradicciones contra la fuente oficial.
- Devuelve alerta, severidad y evidencia.
- Genera un cierre de reunion basico con decisiones, riesgos y alertas.

## Que falta para que sea producto real

- Streaming profesional con LiveKit/WebRTC.
- STT streaming continuo con Google Speech-to-Text o Deepgram.
- Diarizacion confiable por hablante.
- Conectores reales a BI/base de datos.
- Persistencia en PostgreSQL.
- Memoria semantica con pgvector.
- Seguridad, auditoria, consentimiento y retencion.

## Google Speech-to-Text local

Para probar transcripcion real con Google, coloca la cuenta de servicio en:

```text
.secrets/google-speech.json
```

El backend la detecta automaticamente. La carpeta `.secrets/` esta ignorada por Git.

La UI envia audio `webm/opus` en chunks cortos a:

```text
POST /api/transcribe
```

Esto es suficiente para una prueba local. Para produccion, el siguiente paso es reemplazar chunks HTTP por streaming WebRTC/LiveKit.
