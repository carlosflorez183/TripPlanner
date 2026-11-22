import logging
import os
import sys
import time
from pathlib import Path

from strands import Agent
from strands.multiagent.a2a import A2AServer
from strands.tools.mcp import MCPClient

logger = logging.getLogger(__name__)

# Smithery (pasarela HTTP) - fuente primaria de tools
TRAVEL_MCP_URL = os.environ.get("TRAVEL_MCP_URL", "")
SMITHERY_API_KEY = os.environ.get("SMITHERY_API_KEY", "")

# Servidor MCP propio en Lambda - alternativa
MCP_SERVER_URL = os.environ.get("MCP_SERVER_URL", "")

# Timeout para operaciones MCP (segundos)
MCP_TIMEOUT = int(os.environ.get("MCP_TIMEOUT", "15"))

# Ruta al servidor MCP local (fallback)
MCP_SERVER_PATH = Path(__file__).parent / "flights_mcp_server.py"
PYTHON_PATH = sys.executable


def create_mcp_client() -> MCPClient:
    """Crea un cliente MCP: Smithery -> Lambda -> local stdio."""
    if TRAVEL_MCP_URL:
        # Smithery pasa la API key como query param (?api_key=...)
        url = TRAVEL_MCP_URL
        if SMITHERY_API_KEY and "api_key=" not in url:
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}api_key={SMITHERY_API_KEY}"
        headers = {"Content-Type": "application/json"}
        if SMITHERY_API_KEY:
            headers["Authorization"] = f"Bearer {SMITHERY_API_KEY}"
        logger.info("MCP: usando Smithery (%s)", TRAVEL_MCP_URL)
        return MCPClient(url=url, headers=headers, startup_timeout=MCP_TIMEOUT)

    if MCP_SERVER_URL:
        # Conexión HTTP al servidor MCP remoto (Lambda) con timeout explícito
        logger.info("MCP: usando Lambda (%s)", MCP_SERVER_URL)
        return MCPClient(
            url=MCP_SERVER_URL,
            headers={"Content-Type": "application/json"},
            startup_timeout=MCP_TIMEOUT,
        )

    # Fallback: servidor local via stdio
    logger.info("MCP: usando servidor local stdio (%s)", MCP_SERVER_PATH)
    from mcp import StdioServerParameters
    from mcp.client.stdio import stdio_client

    server_params = StdioServerParameters(
        command=PYTHON_PATH,
        args=[str(MCP_SERVER_PATH)],
    )

    def transport_callable():
        return stdio_client(server_params)

    return MCPClient(transport_callable)


# Cliente MCP global - se conecta una vez al inicio
_mcp_client = None


def mcp_source() -> str:
    """Devuelve un identificador legible de la fuente MCP activa."""
    if TRAVEL_MCP_URL:
        return "Smithery"
    if MCP_SERVER_URL:
        return "Lambda"
    return "local-stdio"


def get_mcp_tools():
    """Obtiene las tools del servidor MCP. Abre la sesión si es necesario."""
    global _mcp_client
    if _mcp_client is None:
        _mcp_client = create_mcp_client()
        _mcp_client.start()
    tools = _mcp_client.list_tools_sync()
    names = [getattr(t, "tool_name", None) or getattr(t, "name", "?") for t in tools]
    print(f"[MCP] fuente={mcp_source()} url={TRAVEL_MCP_URL or MCP_SERVER_URL or MCP_SERVER_PATH}")
    print(f"[MCP] tools descubiertas ({len(names)}): {names}")
    return tools


def create_agent(context_id: str = None) -> Agent:
    """Crea el agente Flights con tools MCP."""
    # Obtener tools del servidor MCP
    mcp_tools = get_mcp_tools()

    return Agent(
        name="Flights Agent",
        description="Busca vuelos entre ciudades usando herramientas MCP.",
        tools=mcp_tools,
        callback_handler=None,
    )


# Fallback honesto si MCP no está disponible
def create_agent_with_fallback(context_id: str = None) -> Agent:
    """Crea el agente con fallback honesto si MCP falla o supera el timeout."""
    from strands import tool

    @tool
    def search_flights_fallback(origin: str, destination: str, date: str) -> str:
        """Busca vuelos (indicando que el servicio MCP no está disponible)."""
        return (
            f"No pude buscar vuelos de {origin} a {destination} para el {date}: "
            "el servicio de vuelos (MCP) no responde en este momento. "
            "No tengo datos de vuelos para mostrar. Intenta de nuevo más tarde."
        )

    mcp_start = time.monotonic()
    try:
        mcp_tools = get_mcp_tools()
        elapsed = time.monotonic() - mcp_start
        logger.info("MCP conectado en %.2fs, %d tools", elapsed, len(mcp_tools))
        if elapsed > MCP_TIMEOUT:
            logger.warning("MCP conectado pero lento (%.2fs > %ss)", elapsed, MCP_TIMEOUT)
        tools = mcp_tools
    except Exception as e:
        elapsed = time.monotonic() - mcp_start
        logger.warning("MCP no disponible tras %.2fs (%s): %s", elapsed, type(e).__name__, e)
        tools = [search_flights_fallback]

    return Agent(
        name="Flights Agent",
        description="Busca vuelos entre ciudades usando herramientas MCP.",
        tools=tools,
        callback_handler=None,
    )


# Crear servidor A2A (con fallback honesto si MCP falla)
a2a_server = A2AServer(agent_factory=create_agent_with_fallback, host="127.0.0.1", port=9000)


if __name__ == "__main__":
    print(f"Iniciando Flights Agent en puerto 9000...")
    if MCP_SERVER_URL:
        print(f"Servidor MCP remoto: {MCP_SERVER_URL}")
    else:
        print(f"Servidor MCP local: {MCP_SERVER_PATH}")
    a2a_server.serve()
