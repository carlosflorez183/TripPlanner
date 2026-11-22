# Guion de Video — Solo codigo: verificar que Flights usa Smithery

**Duracion:** ~5–6 min
**Objetivo del video:** demostrar EN VIVO que el FlightsAgent obtiene sus tools de Smithery (no del Lambda local ni de mocks).
**Formato:** solo editor de codigo + terminal. Sin diapositivas.

> No mostrar la API key en pantalla. Teclearla fuera de camara o difuminar esa linea.

---

## Grabacion 0 — Preparacion (fuera de camara)

Abrir el editor en `C:\Temp\TripPlanner\app\FlightsAgent\main.py` y una terminal PowerShell.

Antes de grabar, dejar estos tres puntos de evidencia a mano:
1. `app/FlightsAgent/main.py` → funcion `create_mcp_client()` (codigo que apunta a Smithery).
2. `agentcore/.env.local` → `TRAVEL_MCP_URL` + `SMITHERY_API_KEY`.
3. Terminal lista para correr `agentcore dev` y ver el log `[MCP] fuente=Smithery`.

---

## Escena 1 — El codigo que usa Smithery (0:00–1:30)

**En pantalla:** `app/FlightsAgent/main.py`, `create_mcp_client()`.

**Narrar mientras recorres:**
> "Aqui esta la pieza clave. El FlightsAgent ya no usa search_flights hardcodeado: crea un cliente MCP contra Smithery. Si hay TRAVEL_MCP_URL, que apunta a Smithery, usamos esa ruta y le pasamos la API key. Solo si no hay Smithery caemos al Lambda propio, y solo si tampoco hay, al servidor local. La prioridad es Smithery."

**Codigo a mostrar (destacar estas lineas):**
```python
TRAVEL_MCP_URL = os.environ.get("TRAVEL_MCP_URL", "")
SMITHERY_API_KEY = os.environ.get("SMITHERY_API_KEY", "")

def create_mcp_client() -> MCPClient:
    if TRAVEL_MCP_URL:
        url = TRAVEL_MCP_URL
        if SMITHERY_API_KEY and "api_key=" not in url:
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}api_key={SMITHERY_API_KEY}"
        headers = {"Content-Type": "application/json"}
        if SMITHERY_API_KEY:
            headers["Authorization"] = f"Bearer {SMITHERY_API_KEY}"
        return MCPClient(url=url, headers=headers, startup_timeout=MCP_TIMEOUT)
    ...
```

---

## Escena 2 — La configuracion (1:30–2:30)

**En pantalla:** `agentcore/.env.local` (difuminar la key).

**Narrar:**
> "Las credenciales van en .env.local, que esta en .gitignore. La URL es la de Smithery: flights--google.run.tools, el servidor Google Flights del catalogo de Smithery. Aqui definimos la fuente; el codigo la respeta."

**Mostrar:**
```
TRAVEL_MCP_URL=https://flights--google.run.tools
SMITHERY_API_KEY=********
```
> "Fijate que ademas hay un MCP_SERVER_URL, que es nuestro Lambda. Pero TRAVEL_MCP_URL tiene prioridad: Smithery gana."

---

## Escena 3 — Verificacion en vivo: fuente=Smithery (2:30–4:00)

**En pantalla:** terminal. Ejecutar el dev server y luego invocar.

```powershell
cd C:\Temp\TripPlanner
agentcore dev --runtime FlightsAgent --skip-deploy
```

En otra terminal (o con el chat UI), invocar:
```powershell
$body = '{"jsonrpc":"2.0","id":1,"method":"message/send","params":{"message":{"role":"user","parts":[{"kind":"text","text":"Vuelos de BAQ a MAD el 2026-11-20"}],"messageId":"m1"}}}'
[System.IO.File]::WriteAllText("$env:TEMP\a2a.json", $body, [System.Text.UTF8Encoding]::new($false))
curl.exe -X POST "http://localhost:9000/" -H "Content-Type: application/json" --data-binary "@$env:TEMP\a2a.json"
```

**Lo que debe aparecer en el log del dev server:**
```
[MCP] fuente=Smithery url=https://flights--google.run.tools
[MCP] tools descubiertas (1): ['search_flights']
```

**Narrar:**
> "Aqui esta la prueba. El log dice fuente igual a Smithery y muestra la URL de Smithery. Y la tool descubierta es search_flights, exactamente la que publica el servidor google/flights de Smithery. No es el Lambda, no es un mock: son las tools que Smithery nos entrega por Streamable HTTP."

> Detalle clave para el video: la URL que aparece es flights--google.run.tools, el dominio de la pasarela de Smithery. Eso demuestra que la fuente es Smithery.

---

## Escena 4 — Respuesta real del servidor (4:00–5:00)

**En pantalla:** la respuesta del agente (vuelos reales).

**Narrar:**
> "Y no es solo la conexion: los datos son reales. Fijate en el detalle, con vuelos LATAM, horarios, numero de vuelo y precio. Eso sale de Google Flights a traves de Smithery. Con la tool local hardcodeada jamas tendriamos esto."

**Mostrar (resultado verificado):** LATAM LA 4145 + IB 156, salida 07:10, USD $890.

**Extra (criterio de exito):** cambiar la fecha y mostrar que vuelven precios/horarios distintos.
> "Misma ruta, otra fecha, otra respuesta. Eso confirma que la tool es dinamica, no un dato fijo."

---

## Escena 5 — Cierre (5:00–5:30)

**Narrar:**
> "Resumiendo: el FlightsAgent apunta a Smithery por variable de entorno, el log confirma fuente igual a Smithery con su URL, y las tools search_flights devuelven vuelos reales. Smithery queda como nuestra pasarela MCP, con el Lambda propio como respaldo por si la pasarela no responde."

---

## Checklist de tomas

- [ ] `create_mcp_client()` con la rama Smithery resaltada
- [ ] `.env.local` con `TRAVEL_MCP_URL` de Smithery (key difuminada)
- [ ] Terminal: `agentcore dev --runtime FlightsAgent --skip-deploy`
- [ ] Log en vivo: `[MCP] fuente=Smithery url=https://flights--google.run.tools`
- [ ] Log en vivo: `tools descubiertas (1): ['search_flights']`
- [ ] Respuesta con vuelos reales (LATAM / precios)
- [ ] Cambiar fecha → respuesta distinta

## Evidencia de que es Smithery (para el video)

| Senal | Valor |
|---|---|
| `TRAVEL_MCP_URL` | `https://flights--google.run.tools` (dominio de la pasarela de Smithery) |
| Log del agente | `[MCP] fuente=Smithery url=https://flights--google.run.tools` |
| Tool descubierta | `search_flights` (la del servidor `google/flights` de Smithery) |
| Datos devueltos | Vuelos reales (SerpAPI/Google Flights), no mocks |
| Prioridad en codigo | `TRAVEL_MCP_URL` > `MCP_SERVER_URL` (Lambda) > local |
