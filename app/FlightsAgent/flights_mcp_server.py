"""Servidor MCP local de vuelos - datos simulados pero realistas.

Demuestra el protocolo MCP con tools de búsqueda de vuelos.
Los precios varían según la fecha para simular disponibilidad real.
Corre por stdio y puede conectarse desde FlightsAgent.
"""
import hashlib
from mcp.server.mcpserver import MCPServer

mcp = MCPServer("trip-flights-mcp")

# Base de datos simulada de vuelos (precios base)
FLIGHTS_DB = {
    ("BAQ", "MAD"): [
        {"airline": "Avianca", "flight": "AV010", "depart": "08:15", "arrive": "22:30", "price_usd": 540, "stops": 1, "duration": "14h 15m"},
        {"airline": "Iberia", "flight": "IB6584", "depart": "21:40", "arrive": "11:55", "price_usd": 610, "stops": 0, "duration": "9h 15m"},
        {"airline": "Air Europa", "flight": "UX54", "depart": "14:20", "arrive": "06:45", "price_usd": 485, "stops": 1, "duration": "16h 25m"},
    ],
    ("MAD", "BAQ"): [
        {"airline": "Avianca", "flight": "AV011", "depart": "10:30", "arrive": "15:45", "price_usd": 520, "stops": 1, "duration": "11h 15m"},
        {"airline": "Iberia", "flight": "IB6585", "depart": "13:15", "arrive": "18:20", "price_usd": 595, "stops": 0, "duration": "10h 5m"},
    ],
    ("BOG", "MAD"): [
        {"airline": "Avianca", "flight": "AV001", "depart": "23:55", "arrive": "16:15", "price_usd": 680, "stops": 0, "duration": "10h 20m"},
        {"airline": "Iberia", "flight": "IB6844", "depart": "12:30", "arrive": "05:45", "price_usd": 720, "stops": 0, "duration": "9h 15m"},
    ],
    ("MAD", "BOG"): [
        {"airline": "Avianca", "flight": "AV002", "depart": "17:30", "arrive": "22:15", "price_usd": 650, "stops": 0, "duration": "10h 45m"},
    ],
    ("MIA", "MAD"): [
        {"airline": "American", "flight": "AA69", "depart": "21:00", "arrive": "12:30", "price_usd": 450, "stops": 0, "duration": "8h 30m"},
        {"airline": "Iberia", "flight": "IB6624", "depart": "16:45", "arrive": "08:15", "price_usd": 520, "stops": 0, "duration": "8h 30m"},
    ],
    ("MAD", "MIA"): [
        {"airline": "American", "flight": "AA68", "depart": "10:30", "arrive": "15:45", "price_usd": 480, "stops": 0, "duration": "9h 15m"},
    ],
}


def _date_variation(date: str, flight_index: int) -> float:
    """Calcula una variación de precio basada en la fecha y el vuelo.
    
    Usa hash de la fecha para generar variación determinística pero diferente
    entre fechas. Simula fluctuaciones de precios reales.
    """
    # Hash de la fecha + índice del vuelo para variación única
    seed = f"{date}-{flight_index}"
    hash_val = int(hashlib.md5(seed.encode()).hexdigest()[:8], 16)
    # Variación entre -15% y +25%
    variation = ((hash_val % 4000) / 10000.0) - 0.15
    return variation


@mcp.tool()
def search_flights(origin: str, destination: str, date: str) -> str:
    """Busca vuelos entre dos aeropuertos para una fecha dada.

    Args:
        origin: Código IATA del aeropuerto de origen (ej: BAQ, BOG, MIA, MAD)
        destination: Código IATA del aeropuerto de destino
        date: Fecha de salida en formato YYYY-MM-DD

    Returns:
        Lista de vuelos disponibles con aerolínea, horario y precio.
        Los precios varían según la fecha simulando disponibilidad real.
    """
    origin = origin.upper().strip()
    destination = destination.upper().strip()

    # Buscar vuelos directos
    base_flights = FLIGHTS_DB.get((origin, destination), [])

    if not base_flights:
        # Intentar ruta inversa para sugerir
        reverse = FLIGHTS_DB.get((destination, origin), [])
        if reverse:
            return f"No hay vuelos directos de {origin} a {destination}. Hay vuelos de {destination} a {origin} disponibles."
        return f"No se encontraron vuelos de {origin} a {destination} para la fecha {date}."

    # Aplicar variación de precios según fecha
    flights = []
    for i, f in enumerate(base_flights):
        variation = _date_variation(date, i)
        adjusted_price = int(f["price_usd"] * (1 + variation))
        flights.append({**f, "price_usd": adjusted_price})

    # Ordenar por precio (más barato primero)
    flights.sort(key=lambda x: x["price_usd"])

    # Formatear respuesta
    lines = [f"Vuelos de {origin} a {destination} para el {date}:", ""]
    for i, f in enumerate(flights, 1):
        stops_str = "Directo" if f["stops"] == 0 else f"{f['stops']} escala(s)"
        lines.extend([
            f"{i}. {f['airline']} {f['flight']}",
            f"   Salida: {f['depart']} | Llegada: {f['arrive']}",
            f"   Duración: {f['duration']} | Escalas: {stops_str}",
            f"   Precio: USD ${f['price_usd']}",
            ""
        ])

    lines.append(f"Total: {len(flights)} vuelo(s) encontrado(s)")
    return "\n".join(lines)


@mcp.tool()
def get_flight_details(flight_number: str) -> str:
    """Obtiene detalles específicos de un vuelo por su número.

    Args:
        flight_number: Número de vuelo (ej: AV010, IB6584)

    Returns:
        Detalles completos del vuelo si existe.
    """
    flight_number = flight_number.upper().strip()

    for (origin, dest), flights in FLIGHTS_DB.items():
        for f in flights:
            if f["flight"].upper() == flight_number:
                return (
                    f"Detalles del vuelo {flight_number}:\n"
                    f"Ruta: {origin} → {dest}\n"
                    f"Aerolínea: {f['airline']}\n"
                    f"Salida: {f['depart']}\n"
                    f"Llegada: {f['arrive']}\n"
                    f"Duración: {f['duration']}\n"
                    f"Escalas: {f['stops']}\n"
                    f"Precio: USD ${f['price_usd']}"
                )

    return f"No se encontró el vuelo {flight_number}."


if __name__ == "__main__":
    mcp.run(transport="stdio")
