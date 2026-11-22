"""Lambda: CreateReservation — Registra una reserva asociada a un viaje."""
import json
import os
import uuid
from datetime import datetime, timezone

import boto3
from boto3.dynamodb.conditions import Key

dynamodb = boto3.resource("dynamodb")
TRIPS_TABLE = os.environ["TRIPS_TABLE"]
RESERVATIONS_TABLE = os.environ["RESERVATIONS_TABLE"]

VALID_TYPES = {"FLIGHT", "HOTEL", "ACTIVITY"}
VALID_STATUSES = {"SIMULADA", "PENDIENTE", "CONFIRMADA"}


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

    user_id = _user_id(event)
    if not user_id:
        return _response(401, {"error": "No autenticado: falta un token JWT válido."})

    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _response(400, {"error": "El cuerpo no es JSON válido."})

    # Validaciones obligatorias
    reservation_type = (body.get("type") or "").upper()
    if reservation_type not in VALID_TYPES:
        return _response(400, {
            "error": f"El campo 'type' es obligatorio. Valores permitidos: {sorted(VALID_TYPES)}"
        })

    trip_id = (body.get("tripId") or "").strip()
    if not trip_id:
        return _response(400, {"error": "El campo 'tripId' es obligatorio."})

    # Verificar que el viaje pertenece al usuario
    trips_table = dynamodb.Table(TRIPS_TABLE)
    trip = trips_table.get_item(Key={"userId": user_id, "tripId": trip_id}).get("Item")
    if not trip:
        return _response(404, {"error": f"No se encontró el viaje '{trip_id}' para este usuario."})

    status = (body.get("status") or "SIMULADA").upper()
    if status not in VALID_STATUSES:
        status = "SIMULADA"

    reservation_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    reservation = {
        "userId": user_id,
        "reservationId": reservation_id,
        "tripId": trip_id,
        "type": reservation_type,
        "status": status,
        "details": body.get("details", {}),
        "price": str(body["price"]) if body.get("price") is not None else None,
        "currency": body.get("currency", "COP"),
        "date": body.get("date", ""),
        "notes": body.get("notes", ""),
        "createdAt": now,
        "updatedAt": now,
    }

    # Eliminar None
    reservation = {k: v for k, v in reservation.items() if v is not None}

    res_table = dynamodb.Table(RESERVATIONS_TABLE)
    res_table.put_item(Item=reservation)

    return _response(201, {
        "message": "Reserva registrada exitosamente.",
        "reservation": reservation,
    })
