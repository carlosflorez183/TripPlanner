# TripPlanner AI — Trabajo Final · Programación Agéntica

Plataforma web inteligente para planificar y gestionar viajes, construida sobre una arquitectura multiagente (Strands Agents + Amazon Bedrock AgentCore) con backend serverless en AWS (Lambda, API Gateway, DynamoDB) y frontend estático en S3.

## Arquitectura (dos canales independientes)

```
                USUARIO
                  │
          ┌───────┴───────┐
          │   FRONTEND    │  HTML/CSS/JS · Amazon S3 (CloudFront opcional)
          │   (S3)        │
          └───────┬───────┘
                  │
     ┌────────────┴────────────┐
     │                         │
CANAL 1 (negocio)        CANAL 2 (agéntico)
REST/JSON                HTTP Streaming (directo, sin API Gateway)
     │                         │
  API Gateway              Bedrock AgentCore
  (JWT Cognito)            Orchestrator Agent
     │                         │
  9 Lambdas                 ├── Flight Agent  → MCP de vuelos
  (validación)              ├── Weather Agent → MCP de clima
     │                      └── Tools de negocio → Lambdas
     └──────┬──────────────┬──────┘
            │              │
         DynamoDB (fuente de verdad)
         Users · Preferences · Trips · Itineraries · Reservations
```

- **Canal 1** (operaciones de negocio): frontend → API Gateway (autorizador JWT Cognito) → Lambda → DynamoDB.
- **Canal 2** (interacción agéntica): frontend → HTTP Streaming directo a AgentCore (Orchestrator → Flight/Weather Agents + Tools). No pasa por API Gateway.
- IAM con privilegios mínimos; CORS restringido; sin credenciales AWS en el frontend.

## Agentes (multiagente conservada, no simulación monolítica)

| Agente | Protocolo | Rol |
|---|---|---|
| Orchestrator Agent | HTTP (streaming) | Interactúa con el usuario, coordina especialistas |
| Flight Agent | A2A | Consulta vuelos vía MCP |
| Weather Agent | A2A | Consulta clima vía MCP |

## Modelo de datos (DynamoDB)

| Tabla | PK / SK | Atributos clave |
|---|---|---|
| `TripPlanner-Users-prod` (implícito en Cognito) | — | userId (email), nombre |
| `TripPlanner-Trips-prod` | `userId` (PK) + `tripId` (SK) | destination, origin, startDate, endDate, travelers, budget, currency, preferences, notes, status |
| `TripPlanner-Itineraries-prod` | `tripId` (PK) + `itineraryId` (SK) | days[{date, activities[{time,description,location,type}]}], notes |
| `TripPlanner-Reservations-prod` | `userId` (PK) + `reservationId` (SK) | tripId, type (FLIGHT/HOTEL/ACTIVITY), details, price, currency, date, status (SIMULADA/PENDIENTE/CONFIRMADA) |

Estados de reserva: `SIMULADA` (registrada por el sistema, caso esperado), `PENDIENTE`, `CONFIRMADA` (solo si se integra proveedor real). Etiquetas visibles en la UI.

## APIs (API Gateway `x22euoqhp8`, autorizador JWT Cognito)

| Método | Ruta | Lambda |
|---|---|---|
| GET | /trips | GetTrips |
| POST | /trips | CreateTrip |
| PUT | /trips/{tripId} | UpdateTrip |
| DELETE | /trips/{tripId} | DeleteTrip (cascade itinerarios+reservas) |
| GET | /trips/{tripId}/itinerary | (handler en GetTrips/itinerary) |
| POST | /trips/{tripId}/itinerary | CreateItinerary |
| PUT | /trips/{tripId}/itinerary/{itineraryId} | UpdateItinerary |
| GET | /reservations | GetReservations |
| POST | /reservations | CreateReservation |
| PUT | /reservations/{reservationId} | UpdateReservation |

## Tools del agente (respaldadas por Lambdas)

CreateTrip · GetTrips · UpdateTrip · DeleteTrip · CreateItinerary · UpdateItinerary · CreateReservation · GetReservations · UpdateReservation

## Integración de agentes

- Frontend consume el endpoint de HTTP streaming de AgentCore **directamente** (no vía API Gateway): `POST …/runtimes/<ARN>/invocations` con `Authorization: Bearer <IdToken>` y `X-Amzn-Bedrock-AgentCore-Runtime-Session-Id`.
- El Orchestrator reenvía el token del usuario a la API (JWT) para operaciones de negocio.
- Los agentes Flight/Weather usan MCP para datos externos.
- Los agentes devuelven texto + datos estructurados; el frontend los renderiza como componentes (Agent-Driven UI: tarjetas de vuelos, tarjetas de clima, agenda de itinerario, tarjetas de viajes, listado de reservas).

## Funcionalidades opcionales implementadas

- **Mapas y rutas**: embed de Google Maps en la vista de itinerario (destino).
- **Exportación PDF**: botón "Descargar PDF" (impresión con CSS dedicado).

## Instrucciones de ejecución (desde cero)

### Pre-requisitos
- Python 3.12, AWS CLI configurado (cuenta `762233733426`, región `us-east-1`), AWS SAM CLI, AgentCore CLI, Node.js 20+.

### 1. Backend serverless
```bash
cd backend
sam build
sam deploy --guided   # Stack: TripPlanner-Backend, Stage: prod
# Copiar ApiBaseUrl del output
```

### 2. Configurar agentcore.json
Editar `agentcore/agentcore.json` → `API_BASE_URL` con la URL del API Gateway.

### 3. Desplegar agentes
```bash
agentcore deploy --target dev -y
```

### 4. Frontend
Subir `frontend/*` a un bucket S3 con hosting estático:
```bash
aws s3 sync frontend/ s3://<bucket>/ --delete
```
Actualizar `CONFIG.AGENT_URL` y `CONFIG.API_BASE_URL` en `frontend/app.js`.

### 5. Verificar
Abrir la URL del sitio S3, iniciar sesión con un usuario de Cognito y ejecutar el escenario obligatorio (Cartagena, 4 días, Bogotá, 2 viajeros, presupuesto 5M).

## Seguridad
- JWT de Cognito en API Gateway (valida `aud` con el IdToken).
- `userId` derivado del token, nunca del header `X-User-Id` (anti-spoofing).
- CORS restringido a orígenes específicos.
- IAM mínimo privilegio; validación de tipos/rangos/fechas/estados en Lambda.
- Sin almacenamiento de datos de tarjetas (reservas simuladas).

## Créditos
Asignatura: Programación Agéntica · Programa: Ingeniería de Sistemas · Plazo: 15 días.
