"""Lambda: UpdateReservation — Actualiza el estado u otros campos de una reserva."""
import json
import os
from datetime import datetime, timezone

import boto3
from boto3.dynamodb.conditions import Key

dynamodb = boto3.resource("dynamodb")
RESERVATIONS_TABLE = os.environ["RESERVATIONS_TABLE"]

VALID_STATUSES = {"SIMULADA", "PENDIENTE", "CONFIRMADA"}
UPDATABLE_FIELDS = {"status", "details", "price", "currency", "date", "notes"}


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
    reservation_id = path_params.get("reservationId", "").strip()
    if not reservation_id:
        return _response(400, {"error": "El parámetro 'reservationId' es obligatorio en la URL."})

    # Verificar que la reserva pertenece al usuario
    table = dynamodb.Table(RESERVATIONS_TABLE)
    existing = table.get_item(Key={"userId": user_id, "reservationId": reservation_id}).get("Item")
    if not existing:
        return _response(404, {"error": f"No se encontró la reserva '{reservation_id}' para este usuario."})

    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _response(400, {"error": "El cuerpo no es JSON válido."})

    # Validar status
    if "status" in body:
        status = body["status"].upper() if isinstance(body["status"], str) else ""
        if status not in VALID_STATUSES:
            return _response(400, {
                "error": f"Estado inválido. Valores permitidos: {sorted(VALID_STATUSES)}"
            })
        body["status"] = status

    if "price" in body and body["price"] is not None:
        try:
            body["price"] = str(float(body["price"]))
        except (ValueError, TypeError):
            return _response(400, {"error": "'price' debe ser un número."})

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

    response = table.update_item(
        Key={"userId": user_id, "reservationId": reservation_id},
        UpdateExpression="SET " + ", ".join(update_expr_parts),
        ExpressionAttributeNames=expr_attr_names,
        ExpressionAttributeValues=expr_attr_values,
        ReturnValues="ALL_NEW",
    )

    return _response(200, {
        "message": "Reserva actualizada correctamente.",
        "reservation": response.get("Attributes", {}),
    })
