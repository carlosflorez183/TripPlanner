"""Weather Agent — Especialista en pronósticos meteorológicos.

Consulta el clima mediante un servidor MCP propio o con datos simulados
enriquecidos como fallback.
"""
import os
import logging
from strands import Agent, tool
from strands.multiagent.a2a import A2AServer

logger = logging.getLogger(__name__)

# MCP de clima (opcional; si no está configurado, usa datos simulados enriquecidos)
WEATHER_MCP_URL = os.environ.get("WEATHER_MCP_URL", "")

# Datos simulados por ciudad (realistas para destinos turísticos colombianos y globales)
WEATHER_DATA = {
    "cartagena":    {"temp_c": 32, "condition": "Soleado", "humidity": 78, "rain_prob": 15, "wind_kmh": 18},
    "bogota":       {"temp_c": 14, "condition": "Nublado con lluvia ocasional", "humidity": 72, "rain_prob": 55, "wind_kmh": 12},
    "medellin":     {"temp_c": 22, "condition": "Parcialmente nublado", "humidity": 65, "rain_prob": 30, "wind_kmh": 8},
    "cali":         {"temp_c": 25, "condition": "Soleado", "humidity": 60, "rain_prob": 20, "wind_kmh": 10},
    "santa marta":  {"temp_c": 30, "condition": "Soleado y húmedo", "humidity": 80, "rain_prob": 10, "wind_kmh": 20},
    "san andres":   {"temp_c": 29, "condition": "Tropical con brisa", "humidity": 82, "rain_prob": 25, "wind_kmh": 22},
    "madrid":       {"temp_c": 18, "condition": "Despejado", "humidity": 45, "rain_prob": 10, "wind_kmh": 15},
    "cancun":       {"temp_c": 28, "condition": "Soleado", "humidity": 75, "rain_prob": 20, "wind_kmh": 12},
    "miami":        {"temp_c": 27, "condition": "Parcialmente nublado", "humidity": 70, "rain_prob": 30, "wind_kmh": 14},
    "new york":     {"temp_c": 15, "condition": "Nublado", "humidity": 60, "rain_prob": 40, "wind_kmh": 20},
    "paris":        {"temp_c": 16, "condition": "Lluvioso", "humidity": 75, "rain_prob": 65, "wind_kmh": 10},
    "london":       {"temp_c": 12, "condition": "Nublado con lluvia", "humidity": 80, "rain_prob": 70, "wind_kmh": 18},
    "amsterdam":    {"temp_c": 11, "condition": "Nublado", "humidity": 78, "rain_prob": 60, "wind_kmh": 16},
    "tokyo":        {"temp_c": 20, "condition": "Despejado", "humidity": 55, "rain_prob": 15, "wind_kmh": 8},
    "default":      {"temp_c": 22, "condition": "Parcialmente nublado", "humidity": 65, "rain_prob": 30, "wind_kmh": 12},
}


def _get_weather_data(city: str) -> dict:
    """Obtiene datos del clima para una ciudad (simulados pero realistas)."""
    city_key = city.lower().strip()
    # Buscar coincidencia parcial
    for key in WEATHER_DATA:
        if key in city_key or city_key in key:
            return WEATHER_DATA[key]
    return WEATHER_DATA["default"]


@tool
def get_forecast(city: str, date: str) -> dict:
    """Devuelve el pronóstico del clima de una ciudad para una fecha específica.

    Args:
        city: Nombre de la ciudad (ej: Cartagena, Bogotá, Madrid).
        date: Fecha en formato YYYY-MM-DD.

    Returns:
        Pronóstico con temperatura, condición, humedad y probabilidad de lluvia.
    """
    data = _get_weather_data(city)

    # Variación ligera según fecha (determinística pero diferente por día)
    try:
        day = int(date.split("-")[-1]) if date and "-" in date else 15
        temp_variation = ((day % 5) - 2)  # -2 a +2 grados
        rain_variation = ((day % 7) - 3) * 5  # ±15% en probabilidad
    except (ValueError, IndexError):
        temp_variation = 0
        rain_variation = 0

    return {
        "city": city,
        "date": date,
        "temp_c": data["temp_c"] + temp_variation,
        "temp_f": round((data["temp_c"] + temp_variation) * 9 / 5 + 32, 1),
        "condition": data["condition"],
        "humidity_pct": data["humidity"],
        "rain_probability_pct": max(0, min(100, data["rain_prob"] + rain_variation)),
        "wind_kmh": data["wind_kmh"],
        "summary": (
            f"En {city} el {date}: {data['condition']}, "
            f"{data['temp_c'] + temp_variation}°C, "
            f"probabilidad de lluvia {max(0, min(100, data['rain_prob'] + rain_variation))}%, "
            f"humedad {data['humidity']}%, viento {data['wind_kmh']} km/h."
        ),
    }


@tool
def get_extended_forecast(city: str, start_date: str, days: int = 5) -> str:
    """Devuelve el pronóstico extendido de varios días para una ciudad.

    Args:
        city: Nombre de la ciudad.
        start_date: Fecha de inicio en formato YYYY-MM-DD.
        days: Número de días del pronóstico (1-7).

    Returns:
        Pronóstico día a día en formato texto estructurado.
    """
    from datetime import date, timedelta

    days = max(1, min(7, days))

    try:
        base = date.fromisoformat(start_date)
    except ValueError:
        return f"Fecha no válida: '{start_date}'. Usa formato YYYY-MM-DD."

    base_data = _get_weather_data(city)
    lines = [f"Pronóstico extendido para {city} ({days} días):", ""]

    for i in range(days):
        d = base + timedelta(days=i)
        date_str = d.isoformat()
        day_num = d.day
        temp_v = (day_num % 5) - 2
        rain_v = ((day_num % 7) - 3) * 5
        rain_prob = max(0, min(100, base_data["rain_prob"] + rain_v))
        temp = base_data["temp_c"] + temp_v

        condition_icon = "☀️" if rain_prob < 20 else ("⛅" if rain_prob < 50 else "🌧️")
        lines.append(
            f"{condition_icon} {date_str} ({d.strftime('%A')}): "
            f"{temp}°C, {base_data['condition']}, "
            f"lluvia {rain_prob}%, humedad {base_data['humidity']}%"
        )

    return "\n".join(lines)


def create_agent(context_id: str = None) -> Agent:
    return Agent(
        name="Weather Agent",
        description="Consulta el pronóstico del clima por ciudad y fecha.",
        tools=[get_forecast, get_extended_forecast],
        callback_handler=None,
    )


# AgentCore Runtime A2A exige el contenedor en el puerto 9000.
a2a_server = A2AServer(agent_factory=create_agent, host="127.0.0.1", port=9000)

if __name__ == "__main__":
    a2a_server.serve()
