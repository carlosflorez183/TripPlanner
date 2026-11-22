# TripPlanner AI — Documentación Técnica

## Diagrama de Arquitectura

```
                        👤 USUARIO
                           │
                    ┌──────▼──────┐
                    │  FRONTEND   │
                    │  (HTML/JS)  │
                    │  Amazon S3  │
                    └──┬──────┬───┘
                       │      │
          ─────────────┘      └───────────────────
          CANAL 1                              CANAL 2
     Operaciones de Negocio              Interacción Agéntica
     REST/JSON                           HTTP Streaming
          │                                    │
   ┌──────▼──────┐                    ┌────────▼────────┐
   │ API Gateway │                    │  Amazon Bedrock │
   │   HTTP      │                    │   AgentCore     │
   └──────┬──────┘                    └────────┬────────┘
          │                                    │
   ┌──────▼──────┐                    ┌────────▼────────┐
   │  8 Lambda   │                    │  Orchestrator   │
   │  Functions  │                    │     Agent       │
   └──────┬──────┘                    └───┬──────┬──────┘
          │                              ask_   ask_
   ┌──────▼──────┐                    flights weather
   │  DynamoDB   │                        │      │
   │  4 tables   │               ┌────────▼┐    ┌▼──────────┐
   └─────────────┘               │ Flights │    │  Weather  │
                                 │  Agent  │    │   Agent   │
                                 └────┬────┘    └───────────┘
                                      │
                                 ┌────▼────┐
                                 │   MCP   │
                                 │ Server  │
                                 └─────────┘
```

---

## Dos Canales Independientes

### Canal 1 — Operaciones de Negocio
`Frontend → API Gateway → Lambda → DynamoDB`

Usado para: crear/consultar viajes, itinerarios y reservas.  
Protocolo: REST/JSON sobre HTTPS.

### Canal 2 — Interacción Agéntica
`Frontend → HTTP Streaming → AgentCore → OrchestratorAgent`

Usado para: chat con el asistente, coordinación de agentes especialistas.  
Protocolo: HTTP streaming (SSE / chunked transfer).  
**Nota:** API Gateway NO es intermediario de este canal.

---

## Componentes

| Componente | Responsabilidad | Tecnología |
|---|---|---|
| Frontend | Experiencia de usuario — 6 módulos | HTML/CSS/JavaScript |
| Amazon S3 | Hosting del frontend estático | AWS S3 (static website) |
| API Gateway | Publicar APIs REST del Canal 1 | Amazon API Gateway HTTP |
| Lambda | Lógica de negocio y validaciones (8 funciones) | Python 3.12 |
| DynamoDB | Persistencia de usuarios, viajes, itinerarios, reservas | Amazon DynamoDB |
| AgentCore | Runtime de agentes HTTP streaming | Amazon Bedrock AgentCore |
| OrchestratorAgent | Coordina especialistas + 10 tools | Python + Strands Agents |
| FlightsAgent | Especialista en vuelos (MCP) | Python + Strands + MCP |
| WeatherAgent | Especialista en clima | Python + Strands |
| MCP Server | Herramientas de vuelos | MCP (Smithery / local) |
| IAM | Roles de mínimo privilegio | AWS IAM |

---

## Modelo de Datos DynamoDB

### Tabla: `TripPlanner-Trips`
```
PK: userId (String)   SK: tripId (String, UUID)
├── destination (String)       — "Cartagena"
├── origin (String)            — "Bogotá"
├── startDate (String)         — "2027-01-15"
├── endDate (String)           — "2027-01-19"
├── travelers (Number)         — 3
├── budget (String)            — "5000000"
├── currency (String)          — "COP"
├── preferences (Map)          — {description: "..."}
├── notes (String)
├── status (String)            — PLANNED | IN_PROGRESS | COMPLETED | CANCELLED
├── createdAt (String, ISO)
└── updatedAt (String, ISO)
```

### Tabla: `TripPlanner-Itineraries`
```
PK: tripId (String)   SK: itineraryId (String, UUID)
├── userId (String)
├── title (String)
├── days (List)                — [{date, activities:[{time, description, location, type}]}]
├── notes (String)
├── createdAt (String, ISO)
└── updatedAt (String, ISO)
```

### Tabla: `TripPlanner-Reservations`
```
PK: userId (String)   SK: reservationId (String, UUID)
├── tripId (String)
├── type (String)              — FLIGHT | HOTEL | ACTIVITY
├── status (String)            — SIMULADA | PENDIENTE | CONFIRMADA
├── details (Map)              — {description: "Avianca AV9814..."}
├── price (String)             — "420000"
├── currency (String)          — "COP"
├── date (String)              — "2027-01-15"
├── notes (String)
├── createdAt (String, ISO)
└── updatedAt (String, ISO)
```

**Patrón de acceso:** consulta por usuario (`userId`) en viajes y reservas, por viaje (`tripId`) en itinerarios.

---

## Tools del OrchestratorAgent

| Tool | Lambda | Propósito |
|---|---|---|
| `ask_flights` | — | Delega en FlightsAgent (A2A) |
| `ask_weather` | — | Delega en WeatherAgent (A2A) |
| `CreateTrip` | CreateTrip | Crea un viaje en DynamoDB |
| `GetTrips` | GetTrips | Lista los viajes del usuario |
| `UpdateTrip` | UpdateTrip | Actualiza datos de un viaje |
| `CreateItinerary` | CreateItinerary | Crea itinerario de un viaje |
| `UpdateItinerary` | UpdateItinerary | Modifica un itinerario |
| `CreateReservation` | CreateReservation | Registra una reserva (SIMULADA) |
| `GetReservations` | GetReservations | Lista las reservas del usuario |
| `UpdateReservation` | UpdateReservation | Actualiza estado de reserva |

Las 8 tools de negocio llaman al backend mediante HTTP (`httpx`) usando `API_BASE_URL`.

---

## APIs — Referencia Completa

### POST /trips
Crea un viaje.  
**Headers:** `X-User-Id: <userId>`  
**Body:**
```json
{
  "destination": "Cartagena",
  "origin": "Bogotá",
  "startDate": "2027-01-15",
  "endDate": "2027-01-19",
  "travelers": 3,
  "budget": 5000000,
  "currency": "COP",
  "notes": "Vacaciones familiares"
}
```
**Respuesta 201:**
```json
{ "message": "Viaje creado exitosamente.", "trip": { "tripId": "uuid", ... } }
```

### GET /trips
Lista viajes del usuario.  
**Headers:** `X-User-Id: <userId>`  
**Respuesta 200:**
```json
{ "userId": "user-001", "count": 2, "trips": [...] }
```

### PUT /trips/{tripId}
Actualiza un viaje. Campos opcionales: `destination`, `startDate`, `endDate`, `travelers`, `budget`, `status`, `notes`.

### POST /trips/{tripId}/itinerary
Crea itinerario. **Body:** `{ "days": [{...}], "title": "...", "notes": "..." }`

### PUT /trips/{tripId}/itinerary/{itineraryId}
Actualiza itinerario. Campos: `days`, `title`, `notes`.

### POST /reservations
Registra reserva.  
**Body:** `{ "tripId": "...", "type": "FLIGHT|HOTEL|ACTIVITY", "details": "...", "price": 420000, "currency": "COP", "date": "2027-01-15" }`  
**Estados posibles:** `SIMULADA` (default), `PENDIENTE`, `CONFIRMADA`

### GET /reservations
Lista reservas. **Query:** `?tripId=<tripId>` (opcional).

### PUT /reservations/{reservationId}
Actualiza reserva. Campos: `status`, `details`, `price`, `notes`.

---

## Integración de Agentes

### Cómo el frontend usa HTTP streaming (Canal 2)

```javascript
const response = await fetch(AGENT_URL, {
  method: "POST",
  headers: { "Content-Type": "application/json", "X-User-Id": userId },
  body: JSON.stringify({ prompt: userMessage }),
});
// Leer chunks del streaming
const reader = response.body.getReader();
while (true) {
  const { done, value } = await reader.read();
  if (done) break;
  // Procesar chunk y actualizar UI
}
```

### Cómo los agentes usan MCP

`FlightsAgent` conecta a un servidor MCP con prioridad:
1. Smithery (`TRAVEL_MCP_URL`) — Google Flights real
2. Lambda propia (`MCP_SERVER_URL`) — API de vuelos
3. Servidor local stdio — datos simulados (fallback)

---

## Instrucciones de Despliegue Completo

### 1. Backend (SAM)
```bash
cd a2a-trip-planner/TripPlanner/backend/
sam build
sam deploy --guided    # primera vez
# Anotar la URL del output "ApiBaseUrl"
```

### 2. OrchestratorAgent — agregar variable API_BASE_URL
Editar `agentcore/agentcore.json`, en el runtime `OrchestratorAgent`, agregar:
```json
{ "name": "API_BASE_URL", "value": "https://YOUR_API_ID.execute-api.us-east-1.amazonaws.com/prod" }
```
Luego redesplegar:
```bash
agentcore deploy
```

### 3. Frontend
```bash
# Editar frontend/app.js — actualizar CONFIG.AGENT_URL y CONFIG.API_BASE_URL
# Luego subir a S3:
aws s3 sync frontend/ s3://YOUR_FRONTEND_BUCKET/ --delete
aws s3 website s3://YOUR_FRONTEND_BUCKET/ --index-document index.html
```

### 4. Verificar
```bash
# Invocar OrchestratorAgent directamente:
agentcore invoke --name OrchestratorAgent --prompt "Hola, busca vuelos de BOG a CTG para el 2027-01-15"
```

---

## Estados de Reservas

| Estado | Significado | Indicación en UI |
|---|---|---|
| `SIMULADA` | Registrada sin proveedor externo | Badge azul "SIMULADA" |
| `PENDIENTE` | Registrada, esperando confirmación | Badge amarillo "PENDIENTE" |
| `CONFIRMADA` | Confirmada por proveedor real | Badge verde "CONFIRMADA" |

Las reservas se crean siempre en estado `SIMULADA`. El usuario puede actualizarlas manualmente o pedirle al agente que lo haga.

---

## Memoria de los Agentes

El `OrchestratorAgent` usa **AgentCore Memory** con 4 estrategias:

| Estrategia | Nombre | Qué almacena |
|---|---|---|
| `USER_PREFERENCE` | TravelerPreferences | Preferencias de aerolínea, horario, tipo de alojamiento |
| `SUMMARIZATION` | SessionSummarizer | Resumen de cada sesión de planificación |
| `SEMANTIC` | TravelFacts | Hechos relevantes del viaje (recuperables por significado) |
| `EPISODIC` | TripEpisodes | Episodios concretos de planificación con reflexión |

**La memoria ≠ persistencia:** la memoria mejora la conversación; DynamoDB es la fuente de verdad de viajes y reservas.
