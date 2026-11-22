"""Lambda: UpdateTrip — Actualiza un viaje existente."""
import json
import os
from datetime import datetime, timezone

import boto3
from boto3.dynamodb.conditions import Key

dynamodb = boto3.resource("dynamodb")
TRIPS_TABLE = os.environ["TRIPS_TABLE"]

# Campos actualizables y sus validaciones básicas
UPDATABLE_FIELDS = {
    "destination", "origin", "startDate", "endDate",
    "travelers", "budget", "currency", "preferences", "notes", "status",
}
VALID_STATUSES = {"PLANNED", "IN_PROGRESS", "COMPLETED", "CANCELLED"}


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

    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _response(400, {"error": "El cuerpo no es JSON válido."})

    # Verificar que el viaje existe y pertenece al usuario
    table = dynamodb.Table(TRIPS_TABLE)
    existing = table.get_item(Key={"userId": user_id, "tripId": trip_id}).get("Item")
    if not existing:
        return _response(404, {"error": f"No se encontró el viaje '{trip_id}' para este usuario."})

    # Validar campos
    if "status" in body and body["status"] not in VALID_STATUSES:
        return _response(400, {"error": f"Estado inválido. Valores permitidos: {sorted(VALID_STATUSES)}"})

    if "travelers" in body:
        try:
            body["travelers"] = int(body["travelers"])
            if body["travelers"] < 1:
                return _response(400, {"error": "El número de viajeros debe ser al menos 1."})
        except (ValueError, TypeError):
            return _response(400, {"error": "'travelers' debe ser un entero."})

    if "budget" in body and body["budget"] is not None:
        try:
            body["budget"] = str(float(body["budget"]))
        except (ValueError, TypeError):
            return _response(400, {"error": "'budget' debe ser un número."})

    # Construir expresión de actualización
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

    # Siempre actualizar updatedAt
    update_expr_parts.append("#f_updatedAt = :v_updatedAt")
    expr_attr_names["#f_updatedAt"] = "updatedAt"
    expr_attr_values[":v_updatedAt"] = datetime.now(timezone.utc).isoformat()

    response = table.update_item(
        Key={"userId": user_id, "tripId": trip_id},
        UpdateExpression="SET " + ", ".join(update_expr_parts),
        ExpressionAttributeNames=expr_attr_names,
        ExpressionAttributeValues=expr_attr_values,
        ReturnValues="ALL_NEW",
    )

    return _response(200, {
        "message": "Viaje actualizado correctamente.",
        "trip": response.get("Attributes", {}),
    })
