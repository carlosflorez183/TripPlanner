"""Lambda: DeleteTrip — Elimina un viaje y sus datos asociados.

DELETE /trips/{tripId}

Borra el viaje de la tabla Trips y, en cascada, su itinerario y sus reservas.
El usuario se toma del header X-User-Id (misma convención que el resto de Lambdas).
"""
import json
import os

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

dynamodb = boto3.resource("dynamodb")

TRIPS_TABLE = os.environ["TRIPS_TABLE"]
ITINERARIES_TABLE = os.environ.get("ITINERARIES_TABLE", "")
RESERVATIONS_TABLE = os.environ.get("RESERVATIONS_TABLE", "")


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


def _header(headers: dict, name: str):
    """Busca un header sin importar mayúsculas/minúsculas."""
    name = name.lower()
    for key, value in (headers or {}).items():
        if key.lower() == name:
            return value
    return None


def _method(event: dict):
    """Soporta payload format 1.0 y 2.0."""
    if event.get("httpMethod"):
        return event["httpMethod"]
    return ((event.get("requestContext") or {}).get("http") or {}).get("method")


def _delete_itineraries(trip_id: str) -> int:
    """Elimina todos los itinerarios del viaje (PK = tripId)."""
    if not ITINERARIES_TABLE:
        return 0

    table = dynamodb.Table(ITINERARIES_TABLE)
    deleted = 0
    kwargs = {"KeyConditionExpression": Key("tripId").eq(trip_id)}
    while True:
        result = table.query(**kwargs)
        for item in result.get("Items", []):
            table.delete_item(Key={"tripId": trip_id, "itineraryId": item["itineraryId"]})
            deleted += 1
        if "LastEvaluatedKey" not in result:
            break
        kwargs["ExclusiveStartKey"] = result["LastEvaluatedKey"]
    return deleted


def _delete_reservations(user_id: str, trip_id: str) -> int:
    """Elimina las reservas del usuario que pertenecen al viaje (PK = userId)."""
    if not RESERVATIONS_TABLE:
        return 0

    table = dynamodb.Table(RESERVATIONS_TABLE)
    deleted = 0
    kwargs = {"KeyConditionExpression": Key("userId").eq(user_id)}
    while True:
        result = table.query(**kwargs)
        for item in result.get("Items", []):
            if item.get("tripId") == trip_id:
                table.delete_item(
                    Key={"userId": user_id, "reservationId": item["reservationId"]}
                )
                deleted += 1
        if "LastEvaluatedKey" not in result:
            break
        kwargs["ExclusiveStartKey"] = result["LastEvaluatedKey"]
    return deleted


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

    trip_id = (event.get("pathParameters") or {}).get("tripId")
    if not trip_id:
        return _response(400, {"error": "tripId es requerido"})

    trips = dynamodb.Table(TRIPS_TABLE)

    try:
        trips.delete_item(
            Key={"userId": user_id, "tripId": trip_id},
            ConditionExpression="attribute_exists(tripId)",
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return _response(
                404,
                {
                    "error": "Viaje no encontrado",
                    "tripId": trip_id,
                    "userId": user_id,
                },
            )
        raise

    # Cascada: itinerario + reservas asociadas
    itineraries_deleted = _delete_itineraries(trip_id)
    reservations_deleted = _delete_reservations(user_id, trip_id)

    return _response(
        200,
        {
            "message": "Viaje eliminado",
            "tripId": trip_id,
            "userId": user_id,
            "deleted": {
                "itineraries": itineraries_deleted,
                "reservations": reservations_deleted,
            },
        },
    )
