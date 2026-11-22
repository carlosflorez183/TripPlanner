"""Lambda: CreateTrip — Crea un viaje nuevo para el usuario."""
import json
import os
import uuid
from datetime import datetime, timezone

import boto3
from boto3.dynamodb.conditions import Key

dynamodb = boto3.resource("dynamodb")
TRIPS_TABLE = os.environ["TRIPS_TABLE"]


def _cors_headers():
    return {
        "Access-Control-Allow-Origin": os.environ.get("CORS_ORIGIN", "*"),
        "Access-Control-Allow-Headers": "Content-Type,Authorization,X-User-Id",
        "Access-Control-Allow-Methods": "GET,POST,PUT,DELETE,OPTIONS",
        "Content-Type": "application/json",
    }


def _response(status_code: int, body: dict):
    return {
        "statusCode": status_code,
        "headers": _cors_headers(),
        "body": json.dumps(body, ensure_ascii=False, default=str),
    }


def _method(event):
    """Soporta payload format 1.0 y 2.0 de API Gateway."""
    if event.get("httpMethod"):
        return event["httpMethod"]
    return ((event.get("requestContext") or {}).get("http") or {}).get("method")


def _user_id(event):
    """userId desde el JWT de Cognito (authorizer de API Gateway).

    No se confía en el header X-User-Id: el cliente podría enviar cualquier valor.
    """
    claims = (
        ((event.get("requestContext") or {}).get("authorizer") or {}).get("jwt") or {}
    ).get("claims") or {}
    return (
        claims.get("email")
        or claims.get("cognito:username")
        or claims.get("sub")
        or ""
    )


def handler(event, context):
    if _method(event) == "OPTIONS":
        return _response(200, {})

    # Extraer userId del header o query string
    user_id = _user_id(event)
    if not user_id:
        return _response(401, {"error": "No autenticado: falta un token JWT válido."})

    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _response(400, {"error": "El cuerpo de la solicitud no es JSON válido."})

    # Validaciones
    destination = (body.get("destination") or "").strip()
    origin = (body.get("origin") or "").strip()
    start_date = (body.get("startDate") or "").strip()
    end_date = (body.get("endDate") or "").strip()

    if not destination:
        return _response(400, {"error": "El campo 'destination' es obligatorio."})
    if not start_date:
        return _response(400, {"error": "El campo 'startDate' es obligatorio."})
    if not end_date:
        return _response(400, {"error": "El campo 'endDate' es obligatorio."})

    travelers = body.get("travelers", 1)
    if not isinstance(travelers, int) or travelers < 1:
        travelers = 1

    budget = body.get("budget")
    if budget is not None:
        try:
            budget = float(budget)
            if budget < 0:
                return _response(400, {"error": "El presupuesto no puede ser negativo."})
        except (ValueError, TypeError):
            return _response(400, {"error": "El campo 'budget' debe ser un número."})

    # Crear el viaje
    trip_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    trip = {
        "userId": user_id,
        "tripId": trip_id,
        "destination": destination,
        "origin": origin or "No especificado",
        "startDate": start_date,
        "endDate": end_date,
        "travelers": travelers,
        "budget": str(budget) if budget is not None else None,
        "currency": body.get("currency", "COP"),
        "preferences": body.get("preferences", {}),
        "notes": body.get("notes", ""),
        "status": "PLANNED",
        "createdAt": now,
        "updatedAt": now,
    }

    # Eliminar valores None para DynamoDB
    trip = {k: v for k, v in trip.items() if v is not None}

    table = dynamodb.Table(TRIPS_TABLE)
    table.put_item(Item=trip)

    return _response(201, {
        "message": "Viaje creado exitosamente.",
        "trip": trip,
    })
