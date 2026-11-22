"""Lambda: UpdateItinerary — Modifica el itinerario de un viaje."""
import json
import os
from datetime import datetime, timezone

import boto3
from boto3.dynamodb.conditions import Key

dynamodb = boto3.resource("dynamodb")
TRIPS_TABLE = os.environ["TRIPS_TABLE"]
ITINERARIES_TABLE = os.environ["ITINERARIES_TABLE"]

UPDATABLE_FIELDS = {"title", "days", "notes"}


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
    itinerary_id = path_params.get("itineraryId", "").strip()

    if not trip_id or not itinerary_id:
        return _response(400, {"error": "Los parámetros 'tripId' e 'itineraryId' son obligatorios en la URL."})

    # Verificar que el viaje pertenece al usuario
    trips_table = dynamodb.Table(TRIPS_TABLE)
    trip = trips_table.get_item(Key={"userId": user_id, "tripId": trip_id}).get("Item")
    if not trip:
        return _response(404, {"error": f"No se encontró el viaje '{trip_id}' para este usuario."})

    # Verificar que el itinerario existe
    it_table = dynamodb.Table(ITINERARIES_TABLE)
    existing = it_table.get_item(Key={"tripId": trip_id, "itineraryId": itinerary_id}).get("Item")
    if not existing:
        return _response(404, {"error": f"No se encontró el itinerario '{itinerary_id}'."})

    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _response(400, {"error": "El cuerpo no es JSON válido."})

    update_expr_parts = []
    expr_attr_names = {}
    expr_attr_values = {}

    for field, value in body.items():
        if field not in UPDATABLE_FIELDS:
            continue
        placeholder = f"#f_{field}"
        value_placeholder = f":v_{field}"
        update_expr_parts.append(f"{placeholder} = {value_placeholder}")
        expr_attr_names[placeholder] = field
        expr_attr_values[value_placeholder] = value

    if not update_expr_parts:
        return _response(400, {"error": "No se proporcionaron campos válidos para actualizar."})

    update_expr_parts.append("#f_updatedAt = :v_updatedAt")
    expr_attr_names["#f_updatedAt"] = "updatedAt"
    expr_attr_values[":v_updatedAt"] = datetime.now(timezone.utc).isoformat()

    response = it_table.update_item(
        Key={"tripId": trip_id, "itineraryId": itinerary_id},
        UpdateExpression="SET " + ", ".join(update_expr_parts),
        ExpressionAttributeNames=expr_attr_names,
        ExpressionAttributeValues=expr_attr_values,
        ReturnValues="ALL_NEW",
    )

    return _response(200, {
        "message": "Itinerario actualizado correctamente.",
        "itinerary": response.get("Attributes", {}),
    })
