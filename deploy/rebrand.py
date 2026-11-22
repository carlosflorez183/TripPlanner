"""Renombra la marca en los 3 archivos del frontend."""
import pathlib

BASE = pathlib.Path(r"c:\Users\Yanet Zota\agentic-workshop\a2a-trip-planner\TripPlanner\frontend")

REPLACEMENTS = [
    ("TripPlanner AI — Your AI Travel Companion", "TRIPLANNER TU AGENTE DE VIAJE"),
    ("TripPlanner AI — Estilos globales",          "TRIPLANNER TU AGENTE DE VIAJE — Estilos"),
    ("TripPlanner AI — JavaScript Principal",      "TRIPLANNER TU AGENTE DE VIAJE — JavaScript"),
    ("Your AI Travel Companion",                   "TU AGENTE DE VIAJE"),
    ("TripPlanner <span>AI</span>",                "TRIPLANNER <span>TU AGENTE</span>"),
    ("TripPlanner AI",                             "TRIPLANNER"),
    ("Soy TripPlanner AI, tu asistente",           "Soy TRIPLANNER, tu agente"),
    ("TripPlanner AI está procesando",             "TRIPLANNER está procesando"),
    ("Itinerario generado por TripPlanner AI",     "Itinerario generado por TRIPLANNER"),
    ("🔐 Autenticación segura · Amazon Cognito · TripPlanner AI",
     "🔐 Autenticación segura · Amazon Cognito · TRIPLANNER"),
]

files = ["index.html", "styles.css", "app.js", "auth.js"]

for fname in files:
    path = BASE / fname
    if not path.exists():
        print(f"SKIP (no existe): {fname}")
        continue
    text = path.read_text(encoding="utf-8")
    changes = 0
    for old, new in REPLACEMENTS:
        count = text.count(old)
        if count:
            text = text.replace(old, new)
            changes += count
            print(f"  [{fname}] {count}x: '{old[:50]}' → '{new[:50]}'")
    path.write_text(text, encoding="utf-8")
    print(f"✅ {fname} — {changes} cambio(s)")

print("\nListo.")
