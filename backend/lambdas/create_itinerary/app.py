"""Lambda: CreateItinerary — Crea el itinerario de un viaje."""
import json
import os
import uuid
from datetime import datetime, timezone

import boto3
from boto3.dynamodb.conditions import Key

dynamodb = boto3.resource("dynamodb")
TRIPS_TABLE = os.environ["TRIPS_TABLE"]
ITINERARIES_TABLE = os.environ["ITINERARIES_TABLE"]


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

    path_params = event.get("pathParameters") or {}
    trip_id = path_params.get("tripId", "").strip()
    if not trip_id:
        return _response(400, {"error": "El parámetro 'tripId' es obligatorio en la URL."})

    # Verificar que el viaje existe y pertenece al usuario
    trips_table = dynamodb.Table(TRIPS_TABLE)
    trip = trips_table.get_item(Key={"userId": user_id, "tripId": trip_id}).get("Item")
    if not trip:
        return _response(404, {"error": f"No se encontró el viaje '{trip_id}' para este usuario."})

    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _response(400, {"error": "El cuerpo no es JSON válido."})

    # 'days' es una lista de objetos {date, activities:[{time, description, location, type}]}
    days = body.get("days", [])
    if not isinstance(days, list):
        return _response(400, {"error": "'days' debe ser una lista de días."})

    itinerary_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    itinerary = {
        "tripId": trip_id,
        "itineraryId": itinerary_id,
        "userId": user_id,
        "title": body.get("title", f"Itinerario de viaje a {trip.get('destination', trip_id)}"),
        "days": days,
        "notes": body.get("notes", ""),
        "createdAt": now,
        "updatedAt": now,
    }

    it_table = dynamodb.Table(ITINERARIES_TABLE)
    it_table.put_item(Item=itinerary)

    return _response(201, {
        "message": "Itinerario creado exitosamente.",
        "itinerary": itinerary,
    })
