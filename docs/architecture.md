# Arquitectura A365 VigIA

## Principio central

VigIA no debe funcionar como un LLM escuchando todo y opinando sobre todo. El diseno correcto es un pipeline donde cada etapa reduce ruido y solo usa IA generativa cuando existe un caso relevante.

```text
Audio / texto
  -> STT streaming
  -> normalizacion de transcripcion
  -> detector rapido de afirmaciones
  -> consulta a fuentes oficiales
  -> motor de contraste
  -> LLM redactor si corresponde
  -> alerta, voz o minuta
```

## Capas

### 1. Capa de reunion

En produccion, usar LiveKit como capa realtime. Permite salas WebRTC y agentes Python/Node que pueden entrar como participantes. Esto evita construir audio WebRTC desde cero.

### 2. Capa de transcripcion

Usar Deepgram o Google Cloud Speech-to-Text al inicio. Whisper local queda como alternativa offline, pero no como primera opcion para baja latencia.

### 3. Capa de afirmaciones

El motor debe detectar piezas verificables:

- campana,
- KPI,
- valor mencionado,
- periodo,
- afirmacion positiva/negativa,
- responsable,
- posible decision.

Esta etapa debe ser deterministica o semi-deterministica, no solo LLM.

### 4. Capa de fuentes oficiales

Primera version:

- snapshots BI certificados,
- CSV/Excel controlados,
- API interna,
- PostgreSQL.

Version avanzada:

- warehouse,
- contratos indexados,
- actas pasadas,
- documentos con embeddings en pgvector.

### 5. Capa de decision

Reglas iniciales:

- Si un KPI esta fuera de umbral y alguien lo presenta como normal, alertar.
- Si el valor dicho difiere de la fuente oficial, alertar.
- Si no hay evidencia suficiente, no corregir; pedir validacion.
- Si el tema no es verificable, guardar silencio.

### 6. Capa generativa

El LLM debe redactar alertas, minutas y resumenes. No debe ser la fuente de verdad.

## Stack recomendado

```text
React/Vite
FastAPI Python
LiveKit Agents
Deepgram o Google STT
PostgreSQL + pgvector
Redis
LLM provider intercambiable
Docker Compose
```

## Por que no Go como nucleo

Go es bueno para concurrencia y servicios livianos, pero el trabajo principal aqui esta en IA, audio, embeddings, extraccion semantica y RAG. Python reduce riesgo de implementacion por ecosistema.

## Por que no C# como nucleo

C# encaja mejor si Microsoft 365, Teams, Azure AD o SQL Server son requisitos centrales. Si el producto es independiente para el director, esa ventaja baja.

