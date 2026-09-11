# Roadmap realista

## Fase 0 - Vertical slice local

Completada.

- Entrada manual de transcripcion.
- Contraste contra snapshot BI local.
- Alertas con evidencia.
- Minuta basica.

## Fase 1 - MVP operativo

- Conector HTTPS de solo lectura a `seguimiento_financiero`. **Completado**
- Validacion, frescura y cache segura de datos. **Completado**
- Perfiles CFO/COO y politica de intervencion. **Completado**
- Persistencia en PostgreSQL.
- Sesiones reales.
- Carga de snapshots BI.
- Historico de transcripcion y alertas.
- Exportacion Markdown/PDF.
- Autenticacion simple.

## Fase 2 - Audio real

- LiveKit para sala realtime.
- Deepgram o Google STT streaming.
- Audio desde microfono de sala.
- Diarizacion inicial.
- Latencia medida en sala real.

## Fase 3 - Conocimiento corporativo

- pgvector.
- Carga de contratos, actas y reportes.
- RAG con fuentes citadas.
- Versionado de documentos.
- Permisos por fuente.

## Fase 4 - Intervencion asistida

- Panel para facilitador.
- Alertas silenciosas.
- Boton humano de intervencion.
- Voz configurable para contradicciones verificadas o solo riesgos criticos. **Completado en modo local**

## Fase 5 - Produccion ejecutiva

- Auditoria.
- Retencion de datos.
- Consentimiento.
- Monitoreo de costos.
- Evaluacion de precision.
- Runbooks de operacion.
