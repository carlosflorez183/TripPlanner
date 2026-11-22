import contextvars
import json
import logging
import os
import uuid

import boto3
import httpx
from botocore.config import Config
from strands import Agent, tool
from strands.models import BedrockModel
from strands.agent.a2a_agent import A2AAgent
from strands.agent.conversation_manager import NullConversationManager
from bedrock_agentcore.memory.integrations.strands.config import AgentCoreMemoryConfig
from bedrock_agentcore.memory.integrations.strands.session_manager import (
    AgentCoreMemorySessionManager,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from bedrock_agentcore.memory import MemoryClient
from bedrock_agentcore.runtime import BedrockAgentCoreApp

# Orígenes permitidos por CORS (el navegador llama directo al runtime).
ALLOWED_ORIGINS = [
    o.strip()
    for o in os.environ.get(
        "ALLOWED_ORIGINS",
        "http://tripplanner-ai-frontend-762233733426.s3-website-us-east-1.amazonaws.com,"
        "http://localhost:3000,http://localhost:8000,http://127.0.0.1:3000",
    ).split(",")
    if o.strip()
]

logger = logging.getLogger(__name__)

app = BedrockAgentCoreApp()

# ---------------------------------------------------------------------------
# CORS: el navegador invoca el runtime directamente (con JWT de Cognito).
# Sin esto, el preflight OPTIONS falla y el chat cae siempre en error.
# ---------------------------------------------------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,  # no usamos cookies; evita el wildcard inválido
    allow_methods=["POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id"],
    expose_headers=["X-Amzn-Bedrock-AgentCore-Runtime-Session-Id"],
    max_age=300,
)


# (Middleware CORS arriba ya responde el preflight OPTIONS /invocations automáticamente;
# no hace falta registrar la ruta OPTIONS manualmente — BedrockAgentCoreApp no la soporta.)

# ---------------------------------------------------------------------------
# Configuración de endpoints de agentes especialistas
# ---------------------------------------------------------------------------
FLIGHTS_ARN = os.environ.get("FLIGHTS_AGENT_ARN") or None
WEATHER_ARN = os.environ.get("WEATHER_AGENT_ARN") or None
FLIGHTS_URL = os.environ.get("FLIGHTS_AGENT_URL") or "http://localhost:9000/"
WEATHER_URL = os.environ.get("WEATHER_AGENT_URL") or "http://localhost:9001/"
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")

# Memoria gestionada
MEMORY_ID = os.environ.get("TRIP_MEMORY_ID") or None

# Timeout para llamadas a especialistas (segundos)
SPECIALIST_TIMEOUT = int(os.environ.get("SPECIALIST_TIMEOUT", "30"))

# ---------------------------------------------------------------------------
# URL base de la API Gateway (backend Lambda + DynamoDB)
# Se inyecta como variable de entorno en AgentCore: API_BASE_URL
# ---------------------------------------------------------------------------
API_BASE_URL = os.environ.get("API_BASE_URL", "").rstrip("/")

# Modelo de Bedrock. Nova Lite es el equilibrio calidad/precio (~$0.06/1M input).
# Para gastar aún menos: us.amazon.nova-micro-v1:0 (~$0.035/1M input).
BEDROCK_MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", "us.amazon.nova-lite-v1:0")

# Token JWT del usuario que invocó al agente. Se propaga en el payload
# ("authToken") y se reenvía a la API, que ahora exige un token de Cognito.
_AUTH_TOKEN: contextvars.ContextVar[str] = contextvars.ContextVar("auth_token", default="")


# ---------------------------------------------------------------------------
# Invocación de agentes especialistas (FlightsAgent / WeatherAgent)
# ---------------------------------------------------------------------------

def _invoke_remote_runtime(agent_arn: str, request: str) -> str:
    """Invoca un runtime A2A desplegado vía boto3 con firma SigV4."""
    client = boto3.client(
        "bedrock-agentcore",
        region_name=AWS_REGION,
        config=Config(read_timeout=SPECIALIST_TIMEOUT, connect_timeout=10, retries={"max_attempts": 1}),
    )
    message_id = uuid.uuid4().hex
    payload = {
        "jsonrpc": "2.0",
        "id": message_id,
        "method": "message/send",
        "params": {
            "message": {
                "kind": "message",
                "role": "user",
                "parts": [{"kind": "text", "text": request}],
                "messageId": message_id,
            }
        },
    }
    response = client.invoke_agent_runtime(
        agentRuntimeArn=agent_arn,
        runtimeSessionId=uuid.uuid4().hex + uuid.uuid4().hex,
        payload=json.dumps(payload).encode("utf-8"),
        contentType="application/json",
        accept="application/json",
    )
    raw = response.get("response")
    if hasattr(raw, "read"):
        raw = raw.read()
    text = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
    return _extract_a2a_text(text)


def _extract_a2a_text(raw: str) -> str:
    """Extrae el texto de una respuesta A2A (JSON-RPC Message/Task)."""
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return raw

    result = data.get("result", data)
    if isinstance(result, dict) and result.get("kind") == "message":
        return _join_parts(result.get("parts", []))
    if isinstance(result, dict) and result.get("kind") == "task":
        texts = []
        for artifact in result.get("artifacts", []) or []:
            texts.append(_join_parts(artifact.get("parts", [])))
        if texts:
            return "\n".join(t for t in texts if t)
    return raw


def _join_parts(parts: list) -> str:
    return " ".join(
        p.get("text", "") for p in (parts or []) if isinstance(p, dict) and p.get("kind") == "text"
    ).strip()


async def _invoke_local_async(target_url: str, request: str) -> str:
    agent = A2AAgent(agent_url=target_url)
    result = await agent.send_message(request)
    return str(result)


def _invoke_specialist(target_arn, target_url: str, request: str) -> str:
    if target_arn:
        return _invoke_remote_runtime(target_arn, request)
    from strands._async import run_async
    return run_async(lambda: _invoke_local_async(target_url, request))


# ---------------------------------------------------------------------------
# Tools: Especialistas
# ---------------------------------------------------------------------------

@tool
def ask_flights(request: str) -> str:
    """Delega en el Flights Agent la búsqueda de vuelos.

    Úsala para buscar vuelos entre aeropuertos, comparar precios y obtener
    detalles de vuelos específicos.
    """
    try:
        return _invoke_specialist(FLIGHTS_ARN, FLIGHTS_URL, request)
    except Exception as e:
        logger.warning("FlightsAgent no disponible: %s", e)
        return (
            "El servicio de vuelos no está disponible en este momento. "
            f"Error: {type(e).__name__}. Intenta de nuevo más tarde."
        )


@tool
def ask_weather(request: str) -> str:
    """Delega en el Weather Agent la consulta del clima.

    Úsala para obtener pronósticos meteorológicos por ciudad y fecha.
    """
    try:
        return _invoke_specialist(WEATHER_ARN, WEATHER_URL, request)
    except Exception as e:
        logger.warning("WeatherAgent no disponible: %s", e)
        return (
            "El servicio de clima no está disponible en este momento. "
            f"Error: {type(e).__name__}. Intenta de nuevo más tarde."
        )


# ---------------------------------------------------------------------------
# Helper HTTP para llamar al backend Lambda
# ---------------------------------------------------------------------------

def _api_call(method: str, path: str, user_id: str, body: dict = None, params: dict = None) -> dict:
    """Realiza una llamada HTTP al backend Lambda a través de API Gateway."""
    if not API_BASE_URL:
        return {"error": "API_BASE_URL no está configurada. El backend no está disponible."}

    url = f"{API_BASE_URL}{path}"
    headers = {
        "Content-Type": "application/json",
        "X-User-Id": user_id,
    }

    # La API Gateway valida el JWT de Cognito: reenviamos el token del usuario.
    token = _AUTH_TOKEN.get()
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        with httpx.Client(timeout=15) as client:
            if method.upper() == "GET":
                response = client.get(url, headers=headers, params=params or {})
            elif method.upper() == "POST":
                response = client.post(url, headers=headers, json=body or {})
            elif method.upper() == "PUT":
                response = client.put(url, headers=headers, json=body or {})
            elif method.upper() == "DELETE":
                response = client.delete(url, headers=headers)
            else:
                return {"error": f"Método HTTP no soportado: {method}"}

        if response.status_code == 401:
            return {
                "error": "No autorizado: falta el token del usuario. "
                "Invocá al agente desde la web (que envía authToken) o configura "
                "un token de servicio."
            }

        if response.status_code >= 400:
            try:
                detail = response.json()
            except Exception:
                detail = response.text[:300]
            return {"error": f"HTTP {response.status_code}: {detail}"}

        return response.json()
    except Exception as e:
        logger.error("Error llamando a API Gateway (%s %s): %s", method, url, e)
        return {"error": f"No se pudo conectar al backend: {type(e).__name__}: {e}"}


def _format_api_result(result: dict) -> str:
    """Convierte la respuesta de la API en texto legible para el agente."""
    if "error" in result:
        return f"Error: {result['error']}"
    return json.dumps(result, ensure_ascii=False, indent=2, default=str)


# ---------------------------------------------------------------------------
# Tools: Viajes (CRUD)
# ---------------------------------------------------------------------------

@tool
def CreateTrip(
    user_id: str,
    destination: str,
    start_date: str,
    end_date: str,
    origin: str = "",
    travelers: int = 1,
    budget: float = None,
    currency: str = "COP",
    preferences: str = "",
    notes: str = "",
) -> str:
    """Crea un nuevo viaje y lo guarda en la base de datos.

    Args:
        user_id: Identificador del usuario (obligatorio).
        destination: Destino del viaje, p.ej. 'Cartagena' (obligatorio).
        start_date: Fecha de inicio en formato YYYY-MM-DD (obligatorio).
        end_date: Fecha de fin en formato YYYY-MM-DD (obligatorio).
        origin: Ciudad o aeropuerto de origen, p.ej. 'Bogotá'.
        travelers: Número de viajeros (mínimo 1).
        budget: Presupuesto total del viaje (número).
        currency: Moneda del presupuesto (COP, USD, EUR).
        preferences: Preferencias del viaje como texto libre.
        notes: Notas adicionales del viaje.

    Returns:
        Confirmación del viaje creado con su ID.
    """
    body = {
        "destination": destination,
        "startDate": start_date,
        "endDate": end_date,
        "origin": origin,
        "travelers": travelers,
        "currency": currency,
        "preferences": {"description": preferences} if preferences else {},
        "notes": notes,
    }
    if budget is not None:
        body["budget"] = budget

    result = _api_call("POST", "/trips", user_id, body=body)
    return _format_api_result(result)


@tool
def GetTrips(user_id: str) -> str:
    """Consulta todos los viajes guardados del usuario.

    Args:
        user_id: Identificador del usuario (obligatorio).

    Returns:
        Lista de viajes del usuario con destino, fechas y estado.
    """
    result = _api_call("GET", "/trips", user_id)
    return _format_api_result(result)


@tool
def UpdateTrip(
    user_id: str,
    trip_id: str,
    destination: str = None,
    start_date: str = None,
    end_date: str = None,
    travelers: int = None,
    budget: float = None,
    currency: str = None,
    status: str = None,
    notes: str = None,
) -> str:
    """Actualiza los datos de un viaje existente.

    Args:
        user_id: Identificador del usuario (obligatorio).
        trip_id: ID del viaje a actualizar (obligatorio).
        destination: Nuevo destino.
        start_date: Nueva fecha de inicio (YYYY-MM-DD).
        end_date: Nueva fecha de fin (YYYY-MM-DD).
        travelers: Nuevo número de viajeros.
        budget: Nuevo presupuesto.
        currency: Nueva moneda.
        status: Nuevo estado (PLANNED, IN_PROGRESS, COMPLETED, CANCELLED).
        notes: Nuevas notas.

    Returns:
        Viaje actualizado con sus nuevos datos.
    """
    body = {}
    if destination is not None:
        body["destination"] = destination
    if start_date is not None:
        body["startDate"] = start_date
    if end_date is not None:
        body["endDate"] = end_date
    if travelers is not None:
        body["travelers"] = travelers
    if budget is not None:
        body["budget"] = budget
    if currency is not None:
        body["currency"] = currency
    if status is not None:
        body["status"] = status
    if notes is not None:
        body["notes"] = notes

    result = _api_call("PUT", f"/trips/{trip_id}", user_id, body=body)
    return _format_api_result(result)


# ---------------------------------------------------------------------------
# Tools: Itinerarios
# ---------------------------------------------------------------------------

@tool
def CreateItinerary(
    user_id: str,
    trip_id: str,
    days: str,
    title: str = "",
    notes: str = "",
) -> str:
    """Crea el itinerario de actividades para un viaje.

    Args:
        user_id: Identificador del usuario (obligatorio).
        trip_id: ID del viaje al que pertenece el itinerario (obligatorio).
        days: JSON string con la lista de días y actividades. Formato:
              '[{"date":"2027-01-15","activities":[{"time":"09:00","description":"Visita a la Ciudad Amurallada","location":"Cartagena","type":"TOURISM"}]}]'
        title: Título del itinerario.
        notes: Notas generales del itinerario.

    Returns:
        Confirmación del itinerario creado con su ID.
    """
    try:
        days_list = json.loads(days) if isinstance(days, str) else days
    except (json.JSONDecodeError, TypeError):
        # Si no es JSON válido, crear estructura básica con el texto
        days_list = [{"date": "", "activities": [{"description": days, "time": "", "location": "", "type": "ACTIVITY"}]}]

    body = {
        "days": days_list,
        "title": title,
        "notes": notes,
    }
    result = _api_call("POST", f"/trips/{trip_id}/itinerary", user_id, body=body)
    return _format_api_result(result)


@tool
def UpdateItinerary(
    user_id: str,
    trip_id: str,
    itinerary_id: str,
    days: str = None,
    title: str = None,
    notes: str = None,
) -> str:
    """Modifica el itinerario de un viaje.

    Args:
        user_id: Identificador del usuario (obligatorio).
        trip_id: ID del viaje (obligatorio).
        itinerary_id: ID del itinerario a modificar (obligatorio).
        days: JSON string con los días actualizados (opcional).
        title: Nuevo título del itinerario (opcional).
        notes: Nuevas notas (opcional).

    Returns:
        Itinerario actualizado.
    """
    body = {}
    if days is not None:
        try:
            body["days"] = json.loads(days) if isinstance(days, str) else days
        except (json.JSONDecodeError, TypeError):
            body["days"] = days
    if title is not None:
        body["title"] = title
    if notes is not None:
        body["notes"] = notes

    result = _api_call("PUT", f"/trips/{trip_id}/itinerary/{itinerary_id}", user_id, body=body)
    return _format_api_result(result)


# ---------------------------------------------------------------------------
# Tools: Reservas
# ---------------------------------------------------------------------------

@tool
def CreateReservation(
    user_id: str,
    trip_id: str,
    reservation_type: str,
    details: str = "",
    price: float = None,
    currency: str = "COP",
    date: str = "",
    notes: str = "",
) -> str:
    """Registra una reserva (vuelo, hotel o actividad) asociada a un viaje.

    Args:
        user_id: Identificador del usuario (obligatorio).
        trip_id: ID del viaje al que pertenece la reserva (obligatorio).
        reservation_type: Tipo de reserva: FLIGHT, HOTEL o ACTIVITY (obligatorio).
        details: Descripción o detalles de la reserva (aerolínea, número de vuelo, hotel, etc.).
        price: Precio de la reserva (número).
        currency: Moneda (COP, USD, EUR).
        date: Fecha de la reserva en formato YYYY-MM-DD.
        notes: Notas adicionales.

    Returns:
        Confirmación de la reserva creada. Las reservas son SIMULADAS por defecto.
    """
    details_obj = {"description": details} if isinstance(details, str) else details

    body = {
        "tripId": trip_id,
        "type": reservation_type.upper(),
        "details": details_obj,
        "currency": currency,
        "date": date,
        "notes": notes,
        "status": "SIMULADA",
    }
    if price is not None:
        body["price"] = price

    result = _api_call("POST", "/reservations", user_id, body=body)
    return _format_api_result(result)


@tool
def GetReservations(user_id: str, trip_id: str = "") -> str:
    """Consulta las reservas del usuario, opcionalmente filtradas por viaje.

    Args:
        user_id: Identificador del usuario (obligatorio).
        trip_id: ID del viaje para filtrar reservas (opcional).

    Returns:
        Lista de reservas con tipo, estado, precio y detalles.
    """
    params = {}
    if trip_id:
        params["tripId"] = trip_id

    result = _api_call("GET", "/reservations", user_id, params=params)
    return _format_api_result(result)


@tool
def UpdateReservation(
    user_id: str,
    reservation_id: str,
    status: str = None,
    details: str = None,
    price: float = None,
    notes: str = None,
) -> str:
    """Actualiza el estado u otros campos de una reserva.

    Args:
        user_id: Identificador del usuario (obligatorio).
        reservation_id: ID de la reserva a actualizar (obligatorio).
        status: Nuevo estado: SIMULADA, PENDIENTE o CONFIRMADA.
        details: Nuevos detalles de la reserva.
        price: Nuevo precio.
        notes: Nuevas notas.

    Returns:
        Reserva actualizada con su nuevo estado.
    """
    body = {}
    if status is not None:
        body["status"] = status.upper()
    if details is not None:
        body["details"] = {"description": details} if isinstance(details, str) else details
    if price is not None:
        body["price"] = price
    if notes is not None:
        body["notes"] = notes

    result = _api_call("PUT", f"/reservations/{reservation_id}", user_id, body=body)
    return _format_api_result(result)


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """Eres TripPlanner AI, un asistente inteligente de planificación de viajes.

Tienes acceso a las siguientes herramientas:

CONSULTA DE INFORMACIÓN:
- ask_flights: busca vuelos entre aeropuertos por ruta y fecha
- ask_weather: obtiene pronóstico del clima por ciudad y fecha

GESTIÓN DE VIAJES (requieren user_id del usuario actual):
- CreateTrip: crea un viaje con destino, fechas, viajeros y presupuesto
- GetTrips: lista todos los viajes guardados del usuario
- UpdateTrip: actualiza datos de un viaje existente

ITINERARIOS:
- CreateItinerary: genera y guarda el itinerario de actividades de un viaje
- UpdateItinerary: modifica actividades de un itinerario existente

RESERVAS:
- CreateReservation: registra una reserva (FLIGHT, HOTEL, ACTIVITY) — siempre SIMULADA
- GetReservations: consulta las reservas del usuario
- UpdateReservation: actualiza estado o detalles de una reserva

INSTRUCCIONES IMPORTANTES:
1. Cuando el usuario pida información de vuelos o clima, usa ask_flights y ask_weather.
2. Cuando crees viajes, itinerarios o reservas, usa el user_id proporcionado en el contexto.
   Si no hay user_id, usa 'default-user'.
3. Las reservas son siempre SIMULADAS (el sistema no procesa pagos reales).
4. Presenta los resultados de vuelos como tarjetas comparativas y el clima como pronóstico visual.
5. Cuando generes un itinerario, estructura los días con actividades concretas con horario y lugar.
6. Si tienes preferencias del usuario en el contexto, úsalas para personalizar las recomendaciones.
"""


# ---------------------------------------------------------------------------
# Memoria
# ---------------------------------------------------------------------------

def _build_session_manager(session_id: str, actor_id: str):
    if not MEMORY_ID:
        return None
    config = AgentCoreMemoryConfig(
        memory_id=MEMORY_ID,
        session_id=session_id,
        actor_id=actor_id,
    )
    return AgentCoreMemorySessionManager(config, region_name=AWS_REGION)


def strip_trailing_tool_use(messages: list) -> list:
    if not messages:
        return messages
    cleaned = list(messages)
    while cleaned:
        last = cleaned[-1]
        if not isinstance(last, dict):
            break
        blocks = last.get("content", [])
        if not isinstance(blocks, list):
            break
        has_tool_use = any(isinstance(b, dict) and "toolUse" in b for b in blocks)
        has_tool_result = any(isinstance(b, dict) and "toolResult" in b for b in blocks)
        if has_tool_use and not has_tool_result and last.get("role") == "assistant":
            cleaned.pop()
            continue
        break
    return cleaned


def _extract_prompt(payload: dict) -> str:
    prompt = payload.get("prompt", "")
    if not isinstance(prompt, str):
        raise ValueError("prompt debe ser un string")
    return prompt


def _extract_messages(payload: dict):
    messages = payload.get("messages")
    if messages is None:
        return None
    if not isinstance(messages, list):
        raise ValueError("messages debe ser una lista")
    return strip_trailing_tool_use(messages)


def _memory_client():
    if not MEMORY_ID:
        return None
    try:
        return MemoryClient(region_name=AWS_REGION)
    except Exception:
        return None


def recall_preferences(actor_id: str, query: str = "preferencias de viaje del usuario") -> str:
    client = _memory_client()
    if client is None:
        return ""
    try:
        records = client.retrieve_memories(
            memory_id=MEMORY_ID,
            namespace=f"/preferences/{actor_id}/",
            query=query,
            actor_id=actor_id,
            top_k=3,
        )
    except Exception:
        return ""
    texts = []
    for r in records or []:
        content = (r.get("content") or {}) if isinstance(r, dict) else {}
        text = content.get("text", "") if isinstance(content, dict) else str(content)
        if text:
            texts.append(text)
    return "\n".join(texts)


def get_or_create_agent(session_id: str, actor_id: str, messages=None) -> Agent:
    session_manager = _build_session_manager(session_id, actor_id)
    prefs = recall_preferences(actor_id)
    system_prompt = SYSTEM_PROMPT
    if prefs:
        system_prompt += (
            "\n\nPreferencias recordadas del usuario (usarlas al recomendar):\n" + prefs
        )

    agent_kwargs = {
        "name": "TripPlanner Orchestrator",
        "model": BedrockModel(model_id=BEDROCK_MODEL_ID),
        "system_prompt": system_prompt,
        "tools": [
            ask_flights,
            ask_weather,
            CreateTrip,
            GetTrips,
            UpdateTrip,
            CreateItinerary,
            UpdateItinerary,
            CreateReservation,
            GetReservations,
            UpdateReservation,
        ],
        "callback_handler": None,
    }
    if messages is not None:
        agent_kwargs["messages"] = messages
    if session_manager is not None:
        agent_kwargs["session_manager"] = session_manager
    else:
        agent_kwargs["conversation_manager"] = NullConversationManager()
    return Agent(**agent_kwargs)


def build_agent() -> Agent:
    return get_or_create_agent("default-session", "default-user")


# ---------------------------------------------------------------------------
# Entrypoint HTTP (AgentCore Runtime)
# ---------------------------------------------------------------------------

def _actor_from_context(context) -> str:
    """Identifica al usuario desde el JWT (ya validado por el authorizer del runtime).

    Usa el claim `sub` (UUID de Cognito, solo letras/números/guiones) para que la
    memoria de preferencias sea por usuario. Sin token válido no hay actor.
    """
    import base64
    headers = getattr(context, "request_headers", None) or {}
    auth = ""
    for k, v in headers.items():
        if k.lower() == "authorization":
            auth = v or ""
            break
    if not auth.lower().startswith("bearer "):
        return "anonymous"
    try:
        segment = auth.split(" ", 1)[1].split(".")[1]
        segment += "=" * (-len(segment) % 4)
        claims = json.loads(base64.urlsafe_b64decode(segment))
        sub = claims.get("sub") or ""
        return sub if sub else "anonymous"
    except Exception:
        return "anonymous"

@app.entrypoint
async def invoke(payload, context):
    session_id = getattr(context, "session_id", "default-session") or "default-session"
    actor_id = _actor_from_context(context)
    prompt = _extract_prompt(payload)
    messages = _extract_messages(payload)

    # Token del usuario (lo envía la web). Permite que el agente llame a la API
    # autenticado con las credenciales del propio usuario.
    _AUTH_TOKEN.set(payload.get("authToken") or "")

    agent = get_or_create_agent(session_id, actor_id, messages=messages)

    # Filtra los bloques <think> del stream para que no lleguen al frontend.
    # Mantiene streaming para el contenido útil (fuera de <think>).
    OPEN_TAG = "<thinking>"
    CLOSE_TAG = "</thinking>"
    buf = ""
    state = "normal"  # normal | thinking

    def _partial_tail(s, tag):
        s_low = s.lower()
        tag_low = tag.lower()
        maxk = min(len(s_low), len(tag_low) - 1)
        # Require at least 2 chars to hold back, so a lone "<" is never
        # delayed (avoids flicker when "<" is a normal character).
        for k in range(maxk, 1, -1):
            if s_low.endswith(tag_low[:k]):
                return k
        return 0

    async for event in agent.stream_async(prompt):
        if not (isinstance(event, dict) and "data" in event and isinstance(event["data"], str)):
            continue
        buf += event["data"]
        out = ""
        while True:
            if state == "normal":
                low = buf.lower()
                idx = low.find(OPEN_TAG.lower())
                if idx == -1:
                    hold = _partial_tail(buf, OPEN_TAG)
                    safe_len = len(buf) - hold
                    out += buf[:safe_len]
                    buf = buf[safe_len:]
                    break
                else:
                    out += buf[:idx]
                    buf = buf[idx:]
                    if len(buf) >= len(OPEN_TAG):
                        buf = buf[len(OPEN_TAG):]
                        state = "thinking"
                    else:
                        break
            else:  # thinking
                low = buf.lower()
                idx = low.find(CLOSE_TAG.lower())
                if idx == -1:
                    hold = _partial_tail(buf, CLOSE_TAG)
                    buf = buf[-hold:] if hold else ""
                    break
                else:
                    buf = buf[idx + len(CLOSE_TAG):]
                    state = "normal"
        if out:
            yield out

    # Flush final: solo si quedó en estado normal con contenido útil
    if state == "normal" and buf:
        hold = _partial_tail(buf, OPEN_TAG)
        safe = buf[:-hold] if hold else buf
        if safe:
            yield safe
    # Si quedó en thinking (razonamiento incompleto), se descarta.


if __name__ == "__main__":
    app.run()
