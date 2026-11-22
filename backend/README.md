# TripPlanner AI — Backend Serverless

Backend implementado con **AWS SAM** (Serverless Application Model).  
Incluye 8 funciones Lambda, API Gateway y 4 tablas DynamoDB.

---

## Arquitectura (Canal 1 — Operaciones de Negocio)

```
Frontend → API Gateway → Lambda → DynamoDB
```

---

## Tablas DynamoDB

| Tabla | PK | SK | Propósito |
|---|---|---|---|
| `TripPlanner-Users-{stage}` | `userId` | — | Datos del usuario |
| `TripPlanner-Trips-{stage}` | `userId` | `tripId` | Viajes |
| `TripPlanner-Itineraries-{stage}` | `tripId` | `itineraryId` | Itinerarios |
| `TripPlanner-Reservations-{stage}` | `userId` | `reservationId` | Reservas |

---

## Endpoints de API Gateway

| Método | Ruta | Lambda | Descripción |
|---|---|---|---|
| POST | `/trips` | CreateTrip | Crear un viaje |
| GET | `/trips` | GetTrips | Consultar viajes del usuario |
| PUT | `/trips/{tripId}` | UpdateTrip | Actualizar un viaje |
| POST | `/trips/{tripId}/itinerary` | CreateItinerary | Crear itinerario |
| PUT | `/trips/{tripId}/itinerary/{itineraryId}` | UpdateItinerary | Actualizar itinerario |
| POST | `/reservations` | CreateReservation | Registrar reserva |
| GET | `/reservations` | GetReservations | Consultar reservas |
| PUT | `/reservations/{reservationId}` | UpdateReservation | Actualizar reserva |

**Header obligatorio:** `X-User-Id: <userId>` en todas las peticiones.

**Query params opcionales:**  
- `GET /reservations?tripId=<tripId>` — filtra reservas por viaje

---

## Despliegue

### Pre-requisitos
- AWS CLI configurado (`aws configure`)
- AWS SAM CLI instalado (`pip install aws-sam-cli`)
- Bucket S3 para artifacts SAM

### 1. Build
```bash
cd backend/
sam build
```

### 2. Deploy (primera vez — modo interactivo)
```bash
sam deploy --guided
```

Cuando pregunte:
- **Stack name:** `TripPlanner-Backend`
- **Region:** `us-east-1`
- **Stage:** `prod`
- Acepta los valores por defecto para el resto

### 3. Deploy (siguientes veces)
```bash
sam deploy
```

### 4. Obtener la URL del API Gateway
```bash
aws cloudformation describe-stacks \
  --stack-name TripPlanner-Backend \
  --query "Stacks[0].Outputs[?OutputKey=='ApiBaseUrl'].OutputValue" \
  --output text
```

Copia esta URL y pégala en `frontend/app.js` en la variable `CONFIG.API_BASE_URL`.

---

## Variables de entorno de las Lambdas

Las Lambdas reciben automáticamente del template SAM:
- `TRIPS_TABLE` — nombre de la tabla de viajes
- `ITINERARIES_TABLE` — nombre de la tabla de itinerarios
- `RESERVATIONS_TABLE` — nombre de la tabla de reservas
- `USERS_TABLE` — nombre de la tabla de usuarios
- `CORS_ORIGIN` — `*` (permitir todos los orígenes)

---

## Validaciones implementadas

| Lambda | Validaciones |
|---|---|
| CreateTrip | destination, startDate, endDate obligatorios; travelers ≥ 1; budget ≥ 0 |
| UpdateTrip | Verifica existencia y pertenencia al usuario; status en [PLANNED, IN_PROGRESS, COMPLETED, CANCELLED] |
| CreateItinerary | Verifica que el viaje existe; days debe ser lista |
| UpdateItinerary | Verifica viaje e itinerario; solo actualiza campos permitidos |
| CreateReservation | type en [FLIGHT, HOTEL, ACTIVITY]; verifica que el viaje existe |
| UpdateReservation | status en [SIMULADA, PENDIENTE, CONFIRMADA]; verifica pertenencia |
| GetTrips | Ordena por startDate descendente |
| GetReservations | Filtra opcionalmente por tripId |

---

## Seguridad

- **IAM mínimo privilegio:** un rol compartido con permisos DynamoDB solo sobre las 4 tablas propias
- **Sin credenciales en frontend:** el frontend usa `X-User-Id` como identificador, no credenciales AWS
- **Control de acceso por usuario:** todas las Lambdas verifican que el recurso pertenece al `userId` de la petición
- **Sin datos de tarjetas:** las reservas son simuladas, no se solicita ni almacena información de pago
- **CORS restringido:** configurado en API Gateway y en cada Lambda
