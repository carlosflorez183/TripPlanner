"""Lambda: GetReservations — Consulta reservas del usuario."""
import json
import os

import boto3
from boto3.dynamodb.conditions import Key, Attr

dynamodb = boto3.resource("dynamodb")
RESERVATIONS_TABLE = os.environ["RESERVATIONS_TABLE"]


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

    query_params = event.get("queryStringParameters") or {}
    trip_id_filter = query_params.get("tripId")

    table = dynamodb.Table(RESERVATIONS_TABLE)
    result = table.query(
        KeyConditionExpression=Key("userId").eq(user_id)
    )
    reservations = result.get("Items", [])

    # Filtrar por viaje si se especificó
    if trip_id_filter:
        reservations = [r for r in reservations if r.get("tripId") == trip_id_filter]

    # Ordenar por fecha de creación (más reciente primero)
    reservations.sort(key=lambda r: r.get("createdAt", ""), reverse=True)

    return _response(200, {
        "userId": user_id,
        "count": len(reservations),
        "reservations": reservations,
    })
