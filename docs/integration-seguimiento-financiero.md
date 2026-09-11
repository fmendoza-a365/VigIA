# Integración con SIFO (`seguimiento_financiero`)

VigIA consume una vista de solo lectura. Nunca escribe, aprueba ni modifica datos financieros. Puede usar directamente la API externa del proyecto A365 o un endpoint que entregue el contrato canónico descrito más abajo.

## API externa A365 (integración instalada)

Cuando la URL termina en `/api/external`, VigIA usa el adaptador nativo del
contrato SIFO 1.1:

```env
VIGIA_DATA_PROVIDER=http
SEGUIMIENTO_FINANCIERO_URL=http://host.docker.internal:3000/api/external
SEGUIMIENTO_FINANCIERO_SENSITIVE_URL=http://app:3000/api/external
SEGUIMIENTO_FINANCIERO_API_KEY=api-key-de-solo-lectura
VIGIA_ALLOW_INSECURE_DATA_URL=true
VIGIA_ALLOW_INSECURE_SENSITIVE_DATA_URL=true
SEGUIMIENTO_FINANCIERO_LOOKBACK_MONTHS=24
VIGIA_FINANCIAL_HISTORY_MONTHS=36
VIGIA_DATA_REFRESH_SECONDS=300
```

El adaptador busca el último mes que contenga datos y cubre las fuentes de
decisión publicadas por la API: dashboard, histórico financiero completo,
catálogo de campañas, agentes, facturación y sus reglas de cálculo, presupuesto,
KPI diarios, planilla directa y de estructura, detalle individual de planilla,
ratios de supervisión, colas y SLA, métricas operativas, asistencia SIOP
agregada, calidad de datos y estados IFC mensual y anual.

La dotación se consulta mediante `/workforce` (con `/agents` como alias de
compatibilidad) y se interpreta por grupos de cuenta, campaña, rol y estado. El
campo `total` representa personas y `total_groups` representa filas paginables;
VigIA conserva ambos sin confundirlos. Operaciones, métricas operativas y
asistencia respetan la paginación del backend PHP de SIFO y también funcionan
con el backend Node, que puede devolver el conjunto agregado completo.

El snapshot ejecutivo y los catálogos se guardan en caché; los conjuntos grandes
se mantienen disponibles bajo demanda y se entregan al asistente en páginas
acotadas. Así VigIA puede consultar toda la cobertura publicada sin colocar miles
de filas en cada conversación. DNI, nombres, códigos y conceptos salariales se
excluyen del contexto ejecutivo general, pero pueden consultarse bajo demanda en
`/agents-sensitive`. Esa ruta exige el permiso adicional
`agents.read_sensitive`, aplica paginación y registra cada consulta. Todos los
decimales entregados al modelo se redondean a un máximo de dos posiciones.

`SEGUIMIENTO_FINANCIERO_SENSITIVE_URL` es opcional. Permite que solo esa ruta
use una conexión privada de SIFO mientras el resto de las consultas conserva
la URL oficial principal. HTTP requiere la habilitación explícita y debe
limitarse a una red interna controlada, como la red Docker compartida.

“Toda la información” incluye todos los recursos autorizados por la API de
lectura y el detalle individual de planilla. No incluye credenciales ni
configuración interna de SIFO.

El resumen verificable incluye margen bruto calculado desde ingreso neto y
planilla, facturación, provisión, planilla directa y de estructura, penalidades,
agentes, productividad y cumplimiento presupuestal. SLA, EBITDA u otros
indicadores solo se usan cuando la fuente los entrega. Los datos IFC conservan
su cobertura, advertencias y estado de cierre; un pre-cierre o cobertura parcial
nunca se presenta como resultado definitivo.

Los planes de acción son sugerencias. VigIA relaciona históricos financieros y
operativos para proponer acciones, responsable sugerido, plazo, indicador de
control y condición de revisión, pero no aprueba ni toma decisiones por la
empresa.

## Endpoint canónico alternativo

```env
VIGIA_DATA_PROVIDER=http
SEGUIMIENTO_FINANCIERO_URL=https://finanzas.interno.example/api/v1/vigia/snapshot
SEGUIMIENTO_FINANCIERO_TOKEN=token-de-solo-lectura
VIGIA_DATA_REFRESH_SECONDS=60
VIGIA_DATA_MAX_AGE_SECONDS=900
VIGIA_DATA_TIMEOUT_SECONDS=5
```

También se admite `SEGUIMIENTO_FINANCIERO_API_KEY`, enviado como `X-API-Key`. Si se configura un token, se envía como `Authorization: Bearer ...`.

HTTP sin TLS solo se admite en `localhost`. Para un entorno interno aislado que todavía no tenga HTTPS puede habilitarse explícitamente `VIGIA_ALLOW_INSECURE_DATA_URL=true`, aunque no se recomienda.

## Contrato de lectura

El endpoint puede devolver el objeto directamente o dentro de `data`, `result` o `snapshot`.

```json
{
  "generated_at": "2026-08-05T16:30:00-05:00",
  "source": "seguimiento_financiero / cierre operativo",
  "campaigns": [
    {
      "id": "cliente-a",
      "name": "Cliente A",
      "aliases": ["Cuenta A", "Operación A"],
      "owner": "Gerencia de Operaciones",
      "metrics": {
        "gross_margin": {
          "label": "Margen bruto",
          "aliases": ["margen", "rentabilidad"],
          "actual": 18.3,
          "target": 20.0,
          "direction": "min",
          "unit": "%",
          "tolerance": 0.5,
          "history": [
            {"period": "2026-06", "actual": 19.4},
            {"period": "2026-07", "actual": 18.8}
          ]
        }
      }
    }
  ]
}
```

Campos obligatorios:

- `generated_at`: fecha de corte ISO-8601 con zona horaria.
- `source`: nombre auditable de la vista o reporte.
- `campaigns`: al menos una cuenta, campaña, unidad o centro de negocio.
- `metrics.*.actual` y `target`: números.
- `metrics.*.direction`: `min` cuando un valor mayor es mejor; `max` cuando un valor menor es mejor.
- `metrics.*.unit`: por ejemplo `%`, `S/`, `USD`, `s` o `personas`.

Campos recomendados:

- `aliases` de campaña y métrica: mejoran el reconocimiento del lenguaje hablado.
- `tolerance`: diferencia absoluta aceptada por redondeo. Para porcentajes el valor por defecto es `0.5`.
- `history`: permite rechazar promesas que requieren una mejora no sustentada por la tendencia.

## Seguridad y comportamiento ante fallos

Antes de usar una lectura, VigIA valida por completo su estructura. Una respuesta parcial o inválida nunca reemplaza la última lectura válida.

VigIA no corrige a una persona por voz cuando:

- no hay una lectura válida;
- la fecha de corte excede `VIGIA_DATA_MAX_AGE_SECONDS`;
- la fuente no puede verificarse;
- la diferencia está dentro de la tolerancia configurada.

El estado puede consultarse en `GET /api/data-source/status` y actualizarse manualmente con `POST /api/data-source/refresh`. El estado incluye `complete`, `available_sections`, `missing_sections` y `coverage`, con los totales informados por SIFO para campañas, dotación, operación, métricas, asistencia e IFC.

## Si el esquema actual es diferente

El adaptador HTTP espera este contrato canónico. Si `seguimiento_financiero` usa otras tablas o nombres, se debe añadir una transformación específica en `vigia/financial_data.py` o publicar una vista/endpoint de compatibilidad. Para construirla se necesitan:

1. URL o tipo de acceso (REST, PostgreSQL, SQL Server, etc.).
2. Ejemplo anonimizado de la respuesta o esquema de tablas.
3. Claves que identifican cuenta/campaña y período.
4. Definición de meta, dirección y unidad de cada KPI.
5. Método de autenticación de solo lectura.
