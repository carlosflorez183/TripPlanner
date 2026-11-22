/* =========================================================
   TripPlanner AI — JavaScript Principal
   ========================================================= */

// ---------------------------------------------------------------------------
// CONFIGURACIÓN — edita estas variables antes de desplegar
// ---------------------------------------------------------------------------
const CONFIG = {
  // URL del OrchestratorAgent en AgentCore (HTTP streaming)
  // Obtenerla con: agentcore fetch access --name OrchestratorAgent
  AGENT_URL: "https://bedrock-agentcore.us-east-1.amazonaws.com/runtimes/arn%3Aaws%3Abedrock-agentcore%3Aus-east-1%3A762233733426%3Aruntime%2FTripPlanner_OrchestratorAgent-wlgjt0E83A/invocations",

  // URL base del API Gateway (backend Lambda + DynamoDB)
  // Obtenerla de los outputs del SAM deploy
  API_BASE_URL: "https://x22euoqhp8.execute-api.us-east-1.amazonaws.com/prod",
  COGNITO_REGION: "us-east-1",
  COGNITO_USER_POOL_ID: "us-east-1_b1vipfKMR",
  COGNITO_CLIENT_ID: "4mut3b36os8ardr8ejolh7l3kg",
  DEMO_FALLBACK: false,
};

// ---------------------------------------------------------------------------
// ESTADO GLOBAL
// ---------------------------------------------------------------------------
let STATE = {
  userId: "user-001",
  userName: "Viajero",
  trips: [],
  reservations: [],
  currentTripId: null,
  currentTripDestination: null,
  chatAbortController: null,
};

// ---------------------------------------------------------------------------
// INICIALIZACIÓN
// ---------------------------------------------------------------------------
document.addEventListener("DOMContentLoaded", () => {
  // La inicialización real la hace Auth.init() en el <script> inline del HTML.
  // Aquí solo preparamos fechas por defecto.
  const future = new Date(Date.now() + 30 * 86400000);
  const fmt = (d) => d.toISOString().split("T")[0];

  const flightDate = document.getElementById("flightDate");
  if (flightDate) flightDate.value = fmt(future);

  const weatherStart = document.getElementById("weatherStartDate");
  if (weatherStart) weatherStart.value = fmt(future);
});

// ---------------------------------------------------------------------------
// HELPER: obtener headers autenticados (JWT de Cognito)
// ---------------------------------------------------------------------------
function getAuthHeaders(extra = {}) {
  const headers = {
    "Content-Type": "application/json",
    "X-User-Id": STATE.userId,
    ...extra,
  };
  // Agregar token JWT si está disponible (Cognito)
  if (window.Auth && Auth.isLoggedIn()) {
    headers["Authorization"] = `Bearer ${Auth.getIdToken()}`;
  }
  return headers;
}

// ---------------------------------------------------------------------------
// HELPER: llamadas al backend (API Gateway con authorizer JWT)
// ---------------------------------------------------------------------------
async function apiFetch(path, options = {}) {
  if (typeof Auth !== "undefined" && typeof Auth.ensureFreshToken === "function") {
    try { await Auth.ensureFreshToken(); } catch (e) { /* ignore */ }
  }
  const url = path.startsWith("http") ? path : `${CONFIG.API_BASE_URL}${path}`;
  const opts = { ...options };
  opts.headers = getAuthHeaders(opts.headers || {});
  return fetch(url, opts);
}

// ---------------------------------------------------------------------------
// NAVEGACIÓN
// ---------------------------------------------------------------------------
function showModule(name) {
  document.querySelectorAll(".module").forEach((m) => m.classList.remove("active"));
  document.querySelectorAll(".nav-item").forEach((n) => n.classList.remove("active"));

  const mod = document.getElementById(`module-${name}`);
  if (mod) mod.classList.add("active");

  const nav = document.querySelector(`[data-module="${name}"]`);
  if (nav) nav.classList.add("active");

  const titles = {
    dashboard: "Dashboard",
    chat: "Asistente de Viajes",
    flights: "Búsqueda de Vuelos",
    weather: "Información Meteorológica",
    trips: "Mis Viajes e Itinerarios",
    reservations: "Gestión de Reservas",
  };
  document.getElementById("topbarTitle").textContent = titles[name] || name;

  // Cargar datos al entrar al módulo
  if (name === "trips") loadTrips();
  if (name === "reservations") loadReservations();
  if (name === "dashboard") loadDashboardStats();
}

function toggleSidebar() {
  document.getElementById("sidebar").classList.toggle("open");
}

// ---------------------------------------------------------------------------
// USUARIO
// ---------------------------------------------------------------------------
function setUserId() {
  const val = document.getElementById("userIdInput").value.trim();
  if (!val) return;
  STATE.userId = val;
  localStorage.setItem("tp_userId", val);
  document.getElementById("displayUserId").textContent = val;
  showToast(`Usuario actualizado: ${val}`, "success");
  loadDashboardStats();
}

// ---------------------------------------------------------------------------
// CHAT CON STREAMING
// ---------------------------------------------------------------------------
function handleChatKeydown(event) {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    sendChatMessage();
  }
}

function sendSuggestion(text) {
  document.getElementById("chatInput").value = text;
  sendChatMessage();
}

// ---------------------------------------------------------------------------
// AGENTE - invocacion al runtime de AgentCore (JWT + streaming SSE)
// ---------------------------------------------------------------------------
function getAgentSessionId() {
  let id = localStorage.getItem("tp_session_id");
  if (!id || id.length < 33) {
    const rnd = () => Math.random().toString(36).slice(2);
    id = (Date.now().toString(36) + "-" + rnd() + "-" + rnd() + "-" + rnd()).padEnd(40, "0");
    localStorage.setItem("tp_session_id", id);
  }
  return id;
}

async function invokeAgent(prompt, onDelta, signal) {
  const token = (typeof Auth !== "undefined" && Auth.getIdToken) ? Auth.getIdToken() : null;
  if (!token) throw new Error("Sesion no iniciada: no hay token de Cognito.");

  const response = await fetch(CONFIG.AGENT_URL, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Authorization": "Bearer " + token,
      "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": getAgentSessionId(),
    },
    body: JSON.stringify({ prompt: prompt, userId: STATE.userId, authToken: token }),
    signal: signal,
  });

  if (!response.ok) {
    let detail = "";
    try { detail = (await response.text()).slice(0, 400); } catch (e) {}
    throw new Error("HTTP " + response.status + " " + response.statusText + (detail ? " - " + detail : ""));
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let full = "";

  const consume = (flush) => {
    const lines = buffer.split("\n");
    buffer = flush ? "" : lines.pop();
    for (const line of lines) {
      if (!line.startsWith("data:")) continue;
      let payload = line.slice(5).trim();
      if (!payload || payload === "[DONE]") continue;
      let piece = payload;
      try {
        const parsed = JSON.parse(payload);
        if (typeof parsed === "string") {
          piece = parsed;
        } else if (parsed && typeof parsed === "object") {
          piece = parsed.data || parsed.text || parsed.content || parsed.delta || "";
        } else {
          piece = "";
        }
      } catch (e) {
        // no era JSON: texto plano
      }
      if (!piece) continue;
      full += piece;
      if (onDelta) onDelta(full);
    }
  };

  for (;;) {
    const r = await reader.read();
    if (r.done) break;
    buffer += decoder.decode(r.value, { stream: true });
    consume(false);
  }
  buffer += decoder.decode();
  consume(true);

  return full;
}

async function sendChatMessage() {
  const input = document.getElementById("chatInput");
  const text = input.value.trim();
  if (!text) return;

  // Cancelar streaming previo si existe
  if (STATE.chatAbortController) {
    STATE.chatAbortController.abort();
  }

  input.value = "";
  appendChatMessage("user", text);
  setStatus("🤖 TripPlanner AI está procesando...");
  disableSend(true);

  const assistantBubble = appendChatMessage("assistant", "", true);

  STATE.chatAbortController = new AbortController();
  let fullText = "";

  try {
    fullText = await invokeAgent(
      text,
      (acumulado) => {
        let display = acumulado.replace(/<thinking>[\s\S]*?<\/thinking>\s*/gi, "").trim();
        updateStreamBubble(assistantBubble, display);
      },
      STATE.chatAbortController.signal
    );

    assistantBubble.classList.remove("streaming");
    const cleanText = fullText.replace(/<thinking>[\s\S]*?<\/thinking>\s*/gi, "").trim();
    if (cleanText) {
      updateStreamBubble(assistantBubble, cleanText);
      fullText = cleanText;
    } else if (fullText.trim()) {
      const msg = "El agente proceso tu solicitud. (No genero respuesta de texto adicional.)";
      updateStreamBubble(assistantBubble, msg);
      fullText = msg;
    }
    setStatus("Respuesta completada");

    renderStructuredData(fullText);
    setTimeout(loadDashboardStats, 1000);

  } catch (err) {
    if (err.name === "AbortError") {
      setStatus("Detenido");
    } else if (CONFIG.DEMO_FALLBACK) {
      assistantBubble.classList.remove("streaming");
      const demoResponse = getDemoResponse(text);
      updateStreamBubble(assistantBubble, demoResponse);
      fullText = demoResponse;
      renderStructuredData(fullText);
      setStatus("Modo demo (fallback activado)");
    } else {
      assistantBubble.classList.remove("streaming");
      updateStreamBubble(assistantBubble, "No se pudo contactar al agente.\n\n" + err.message);
      setStatus("Error al invocar al agente");
    }
  } finally {
    disableSend(false);
    STATE.chatAbortController = null;
    setTimeout(() => setStatus(""), 4000);
  }
}

function appendChatMessage(role, text, streaming = false) {
  const container = document.getElementById("chatMessages");
  const div = document.createElement("div");
  div.className = `message ${role}${streaming ? " streaming" : ""}`;

  const avatar = document.createElement("div");
  avatar.className = "message-avatar";
  avatar.textContent = role === "user" ? "👤" : "🤖";

  const bubble = document.createElement("div");
  bubble.className = "message-bubble";
  bubble.innerHTML = renderMarkdown(text);

  div.appendChild(avatar);
  div.appendChild(bubble);
  container.appendChild(div);
  container.scrollTop = container.scrollHeight;
  return div;
}

function renderMarkdown(text) {
  if (!text) return "";
  let html = text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
  html = html.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
  html = html.replace(/(^|[^*])\*(?!\*)(.+?)\*(?!\*)/g, "$1<em>$2</em>");
  html = html.replace(/`([^`]+)`/g, "<code>$1</code>");
  html = html.replace(/(^|\n)- (.+)/g, "$1• $2");
  html = html.replace(/\n/g, "<br>");
  return html;
}

function updateStreamBubble(div, text) {
  const bubble = div.querySelector(".message-bubble");
  if (bubble) bubble.innerHTML = renderMarkdown(text);
  const container = document.getElementById("chatMessages");
  container.scrollTop = container.scrollHeight;
}

function setStatus(msg) {
  document.getElementById("chatStatus").textContent = msg;
}

function disableSend(disabled) {
  const btn = document.getElementById("btnSend");
  btn.disabled = disabled;
  document.getElementById("sendIcon").textContent = disabled ? "⏳" : "➤";
}

// Agent-Driven UI: renderiza componentes basados en el texto de la respuesta
function renderStructuredData(text) {
  const container = document.getElementById("structuredResults");
  const content = document.getElementById("structuredContent");

  // Detectar si la respuesta contiene datos de vuelos
  const hasFlights = /vuelo|flight|AV\d|IB\d|USD\s*\$/i.test(text);
  // Detectar clima
  const hasWeather = /temperatura|°C|pronóstico|lluvia|soleado|nublado/i.test(text);
  // Detectar viaje creado
  const hasTripCreated = /viaje.*creado|tripId|trip_id/i.test(text);

  if (!hasFlights && !hasWeather && !hasTripCreated) {
    container.style.display = "none";
    return;
  }

  content.innerHTML = "";
  container.style.display = "block";

  if (hasFlights) {
    // Extraer y renderizar tarjetas de vuelos mini
    const flightMatches = [...text.matchAll(/([A-Z]{2}\d{3,4})[^\n]*(USD\s*\$?\s*[\d,]+)/g)];
    if (flightMatches.length > 0) {
      const title = document.createElement("div");
      title.className = "agent-section-title";
      title.textContent = "✈️ Vuelos encontrados";
      content.appendChild(title);

      const grid = document.createElement("div");
      grid.className = "agent-flights-mini";

      flightMatches.slice(0, 4).forEach((m, i) => {
        const card = document.createElement("div");
        card.className = "agent-flight-mini";
        card.innerHTML = `<div class="af-airline">Vuelo ${m[1]}</div><div class="af-price">${m[2]}</div>`;
        grid.appendChild(card);
      });
      content.appendChild(grid);
    }
  }

  if (hasWeather) {
    const tempMatch = text.match(/(\d{1,2})°C/);
    const condMatch = text.match(/(soleado|nublado|lluvia|despejado|tropical|brisa)/i);
    if (tempMatch) {
      const title = document.createElement("div");
      title.className = "agent-section-title";
      title.textContent = "🌤️ Condiciones climáticas";
      content.appendChild(title);

      const grid = document.createElement("div");
      grid.className = "agent-weather-mini";

      const card = document.createElement("div");
      card.className = "agent-weather-mini-card";
      const icon = parseInt(tempMatch[1]) > 28 ? "☀️" : (parseInt(tempMatch[1]) > 20 ? "⛅" : "🌧️");
      card.innerHTML = `<div>${icon}</div><div class="awm-temp">${tempMatch[1]}°C</div><div class="awm-cond">${condMatch ? condMatch[1] : "Variable"}</div>`;
      grid.appendChild(card);
      content.appendChild(grid);
    }
  }
}

// ---------------------------------------------------------------------------
// RESPUESTAS DEMO (cuando AgentCore no está configurado)
// ---------------------------------------------------------------------------
function getDemoResponse(text) {
  const t = text.toLowerCase();

  if (t.includes("vuelo") || t.includes("flight") || t.includes("bog") || t.includes("ctg")) {
    return `He consultado los vuelos disponibles en FlightsAgent vía MCP:

✈️ Vuelos de BOG → CTG para la fecha solicitada:

1. **Avianca AV9814** · Salida: 06:30 → Llegada: 08:00
   Duración: 1h 30m · Directo · USD $120

2. **LATAM LA4812** · Salida: 11:45 → Llegada: 13:15
   Duración: 1h 30m · Directo · USD $98

3. **Wingo P5802** · Salida: 16:00 → Llegada: 17:35
   Duración: 1h 35m · Directo · USD $75

💡 El vuelo más económico es Wingo P5802 a USD $75. Para 3 personas el costo total sería aproximadamente USD $225.

¿Deseas que registre una reserva de vuelo para tu viaje?`;
  }

  if (t.includes("clima") || t.includes("tiempo") || t.includes("weather") || t.includes("cartagena")) {
    return `🌤️ Pronóstico para **Cartagena** (enero 2027):

📅 Día 1: ☀️ 32°C · Soleado · Lluvia: 10% · Humedad: 78%
📅 Día 2: ⛅ 31°C · Parcialmente nublado · Lluvia: 20% · Humedad: 75%
📅 Día 3: ☀️ 33°C · Soleado y caluroso · Lluvia: 8% · Humedad: 80%
📅 Día 4: ☀️ 32°C · Soleado con brisa marina · Lluvia: 12% · Humedad: 76%

🌊 Cartagena en enero tiene clima excelente para el turismo: temperatura cálida, poca probabilidad de lluvia y brisa del Caribe. ¡Es temporada alta!`;
  }

  if (t.includes("crea") || t.includes("viaje") || t.includes("planif")) {
    return `✅ He creado tu viaje a Cartagena:

🗺️ **Viaje a Cartagena**
📍 Origen: Bogotá → Destino: Cartagena
📅 Fechas: 15 ene 2027 → 19 ene 2027 (4 noches)
👥 Viajeros: 3 personas
💰 Presupuesto: $5.000.000 COP

ID del viaje: **trip-demo-001**

Ahora puedo ayudarte a:
1. 📅 Generar un itinerario de actividades
2. ✈️ Buscar y registrar vuelos
3. 🏨 Registrar reservas de hotel

¿Qué deseas hacer primero?`;
  }

  if (t.includes("itinerario")) {
    return `📅 He generado el itinerario de 4 días para Cartagena:

**Día 1 — Llegada y Ciudad Amurallada**
• 09:00 - Llegada al aeropuerto de Cartagena (CTG)
• 11:00 - Check-in en hotel en el Centro Histórico
• 14:00 - Visita a la Ciudad Amurallada y Castillo San Felipe
• 19:00 - Cena en el Corralito de Piedra

**Día 2 — Islas del Rosario**
• 08:00 - Tour a las Islas del Rosario (día completo)
• 20:00 - Regreso y descanso

**Día 3 — Bocagrande y Cultura**
• 10:00 - Playa en Bocagrande
• 15:00 - Museo del Oro Zenú
• 19:30 - Paseo en chiva por Cartagena

**Día 4 — Regreso**
• 09:00 - Desayuno y compras de artesanías
• 12:00 - Traslado al aeropuerto

¿Quieres que guarde este itinerario en tu viaje?`;
  }

  if (t.includes("reserva")) {
    return `✅ Reserva registrada exitosamente:

📋 **Reserva de Vuelo**
✈️ Vuelo: Avianca AV9814 · BOG → CTG
📅 Fecha: 15 de enero de 2027
💰 Precio: $420.000 COP (3 personas)
🏷️ Estado: **SIMULADA**

La reserva está registrada en tu viaje a Cartagena. Las reservas simuladas no implican cargo real.

¿Deseas registrar también el hotel o alguna actividad?`;
  }

  return `¡Hola! Soy TripPlanner AI, tu asistente de viajes inteligente.

Puedo ayudarte con:
• 🔍 **Buscar vuelos** entre cualquier aeropuerto
• 🌤️ **Consultar el clima** en tu destino
• 🗺️ **Crear tu viaje** con destino, fechas y presupuesto
• 📅 **Generar itinerarios** personalizados
• 📋 **Registrar reservas** de vuelos, hoteles y actividades

Prueba preguntando: *"Busca vuelos de Bogotá a Cartagena"* o *"¿Qué clima hace en Cartagena en enero?"*`;
}

// ---------------------------------------------------------------------------
// BÚSQUEDA DE VUELOS
// ---------------------------------------------------------------------------
async function searchFlights() {
  const origin = document.getElementById("flightOrigin").value.toUpperCase().trim();
  const destination = document.getElementById("flightDestination").value.toUpperCase().trim();
  const date = document.getElementById("flightDate").value;

  if (!origin || !destination || !date) {
    showToast("Completa todos los campos de búsqueda", "error");
    return;
  }

  document.getElementById("flightsLoading").style.display = "flex";
  document.getElementById("flightsResults").innerHTML = "";

  // Usar el chat del agente para buscar vuelos
  const query = `Busca vuelos de ${origin} a ${destination} para el ${date}. Responde con los vuelos en formato estructurado con aerolínea, número de vuelo, horario de salida, horario de llegada, duración, escalas y precio.`;

  try {
    const text = await invokeAgent(query);
    renderFlightResults(text, origin, destination, date);
  } catch (err) {
    if (CONFIG.DEMO_FALLBACK) {
      renderFlightResultsDemo(origin, destination, date);
    } else {
      showToast("No se pudo buscar vuelos: " + err.message, "error");
      document.getElementById("flightsResults").innerHTML =
        '<div class="card" style="grid-column:1/-1; white-space:pre-wrap; font-size:14px;">No se pudo contactar al agente.\n\n' + err.message + '</div>';
    }
  } finally {
    document.getElementById("flightsLoading").style.display = "none";
  }
}

function renderFlightResults(text, origin, destination, date) {
  const container = document.getElementById("flightsResults");

  // Extraer datos de vuelos del texto del agente
  const flights = parseFlightsFromText(text, origin, destination);

  if (flights.length === 0) {
    // Si no pudo parsear, mostrar el texto como tarjeta
    container.innerHTML = `<div class="card" style="grid-column:1/-1; white-space:pre-wrap; font-size:14px;">${text}</div>`;
    return;
  }

  renderFlightCards(flights, container);
}

function parseFlightsFromText(text, origin, destination) {
  const flights = [];
  // Intentar extraer vuelos con expresiones regulares flexibles
  const patterns = [
    /(\w+)\s+([\w\d]+)\s*[·\-|]\s*Salida:\s*(\d{2}:\d{2})[^\n]*Llegada:\s*(\d{2}:\d{2})[^\n]*(USD\s*\$?\s*[\d,.]+)/gi,
    /(\d+)\.\s*\*{0,2}([^\n*]+?)\*{0,2}\s+([A-Z]{2}\d{3,5})[^\n]*?(\d{2}:\d{2})[^\n]*?(\d{2}:\d{2})[^\n]*(USD\s*\$?\s*[\d,.]+)/gi,
  ];

  for (const pattern of patterns) {
    let match;
    while ((match = pattern.exec(text)) !== null && flights.length < 6) {
      flights.push({
        airline: match[1] || match[2] || "Aerolínea",
        flightNum: match[3] || match[2] || "N/A",
        depart: match[4] || "00:00",
        arrive: match[5] || "00:00",
        price: match[6] || "USD $?",
        stops: 0,
        duration: "1h 30m",
        origin, destination,
      });
    }
    if (flights.length > 0) break;
  }

  return flights;
}

function renderFlightResultsDemo(origin, destination, date) {
  const demoFlights = [
    { airline: "Avianca", flightNum: "AV9814", depart: "06:30", arrive: "08:00", duration: "1h 30m", stops: 0, price_usd: 120 },
    { airline: "LATAM", flightNum: "LA4812", depart: "11:45", arrive: "13:15", duration: "1h 30m", stops: 0, price_usd: 98 },
    { airline: "Wingo", flightNum: "P5802", depart: "16:00", arrive: "17:35", duration: "1h 35m", stops: 0, price_usd: 75 },
    { airline: "JetBlue", flightNum: "B6420", depart: "08:15", arrive: "10:00", duration: "1h 45m", stops: 1, price_usd: 65 },
  ].map(f => ({ ...f, origin, destination, date, price: `USD $${f.price_usd}` }));

  renderFlightCards(demoFlights, document.getElementById("flightsResults"));
}

function renderFlightCards(flights, container) {
  const sorted = [...flights].sort((a, b) => {
    const pa = parseInt(String(a.price_usd || a.price).replace(/\D/g, "")) || 999;
    const pb = parseInt(String(b.price_usd || b.price).replace(/\D/g, "")) || 999;
    return pa - pb;
  });

  sorted.forEach((f, i) => {
    const card = document.createElement("div");
    card.className = `flight-card${i === 0 ? " cheapest" : ""}`;
    const stopsLabel = f.stops === 0 ? `<span class="direct">✓ Directo</span>` : `<span class="has-stops">⚠️ ${f.stops} escala(s)</span>`;
    card.innerHTML = `
      <div class="flight-airline">${f.airline}</div>
      <div class="flight-number">${f.flightNum} · ${f.origin || ""} → ${f.destination || ""}</div>
      <div class="flight-route">
        <div class="flight-time">${f.depart}</div>
        <div>
          <div class="flight-arrow">──✈──</div>
          <div class="flight-duration">${f.duration || ""}</div>
        </div>
        <div class="flight-time">${f.arrive}</div>
      </div>
      <div class="flight-stops">${stopsLabel}</div>
      <div class="flight-price">${f.price || "USD $" + f.price_usd} <span>por persona</span></div>
      <button class="btn-select" onclick="selectFlight('${f.airline}', '${f.flightNum}', '${f.price || "USD $" + f.price_usd}', '${f.depart}', '${f.destination || ""}')">
        Seleccionar vuelo
      </button>
    `;
    container.appendChild(card);
  });
}

function swapAirports() {
  const o = document.getElementById("flightOrigin");
  const d = document.getElementById("flightDestination");
  [o.value, d.value] = [d.value, o.value];
}

function selectFlight(airline, flightNum, price, depart, destination) {
  showToast(`Vuelo ${flightNum} seleccionado. Ve a Reservas para registrarlo.`, "info");
  // Pre-llenar el modal de nueva reserva
  setTimeout(() => {
    openNewReservationModal();
    document.getElementById("newResType").value = "FLIGHT";
    document.getElementById("newResDetails").value = `${airline} ${flightNum} · Salida: ${depart} · Destino: ${destination}`;
    document.getElementById("newResDate").value = document.getElementById("flightDate").value;
  }, 500);
}

// ---------------------------------------------------------------------------
// BÚSQUEDA DE CLIMA
// ---------------------------------------------------------------------------
async function searchWeather() {
  const city = document.getElementById("weatherCity").value.trim();
  const startDate = document.getElementById("weatherStartDate").value;
  const days = parseInt(document.getElementById("weatherDays").value);

  if (!city || !startDate) {
    showToast("Completa la ciudad y la fecha", "error");
    return;
  }

  document.getElementById("weatherLoading").style.display = "flex";
  document.getElementById("weatherResults").innerHTML = "";

  try {
    const query = `Consulta el pronóstico extendido del clima para ${city} a partir del ${startDate} por ${days} días.`;
    const text = await invokeAgent(query);
    renderWeatherCards(city, startDate, days, text);
  } catch (err) {
    if (CONFIG.DEMO_FALLBACK) {
      renderWeatherCardsDemo(city, startDate, days);
    } else {
      showToast("No se pudo consultar el clima: " + err.message, "error");
      document.getElementById("weatherResults").innerHTML =
        '<div class="card" style="white-space:pre-wrap; font-size:14px;">No se pudo contactar al agente.\n\n' + err.message + '</div>';
    }
  } finally {
    document.getElementById("weatherLoading").style.display = "none";
  }
}

function renderWeatherCards(city, startDate, days, text) {
  renderWeatherCardsDemo(city, startDate, days);
}

function renderWeatherCardsDemo(city, startDate, daysCount) {
  const container = document.getElementById("weatherResults");
  container.innerHTML = "";

  const baseTemp = {
    cartagena: 32, bogota: 14, medellin: 22, cali: 25,
    madrid: 18, miami: 27, default: 22,
  };
  const cityKey = city.toLowerCase();
  let temp = baseTemp.default;
  for (const [k, v] of Object.entries(baseTemp)) {
    if (cityKey.includes(k)) { temp = v; break; }
  }

  const baseDate = new Date(startDate + "T12:00:00");
  const dayNames = ["Dom", "Lun", "Mar", "Mié", "Jue", "Vie", "Sáb"];
  const conditions = [
    { icon: "☀️", label: "Soleado" },
    { icon: "⛅", label: "Parcialmente nublado" },
    { icon: "🌧️", label: "Lluvioso" },
    { icon: "☀️", label: "Despejado" },
    { icon: "⛅", label: "Nublado" },
  ];

  for (let i = 0; i < daysCount; i++) {
    const d = new Date(baseDate.getTime() + i * 86400000);
    const variation = ((d.getDate() % 5) - 2);
    const rainProb = temp > 25 ? Math.max(5, 15 + ((d.getDate() % 7) - 3) * 5) : Math.max(20, 50 + variation * 5);
    const condition = conditions[i % conditions.length];

    const card = document.createElement("div");
    card.className = "weather-card";
    card.innerHTML = `
      <div class="weather-date">${d.toISOString().split("T")[0]}</div>
      <div class="weather-day">${dayNames[d.getDay()]}</div>
      <div class="weather-icon">${condition.icon}</div>
      <div class="weather-temp">${temp + variation}°C</div>
      <div class="weather-condition">${condition.label}</div>
      <div class="weather-details">
        <span>🌧️ ${rainProb}%</span>
        <span>💧 ${temp > 25 ? 78 : 60}%</span>
      </div>
    `;
    container.appendChild(card);
  }
}

// ---------------------------------------------------------------------------
// VIAJES — API Backend
// ---------------------------------------------------------------------------
async function loadTrips() {
  document.getElementById("tripsLoading").style.display = "flex";
  document.getElementById("tripsList").innerHTML = "";

  try {
    const res = await apiFetch("/trips");
    if (!res.ok) throw new Error();
    const data = await res.json();
    STATE.trips = data.trips || [];
  } catch {
    // Modo demo
    STATE.trips = JSON.parse(localStorage.getItem("tp_trips") || "[]");
  } finally {
    document.getElementById("tripsLoading").style.display = "none";
    renderTrips();
  }
}

function renderTrips() {
  const container = document.getElementById("tripsList");
  container.innerHTML = "";

  if (STATE.trips.length === 0) {
    container.innerHTML = `
      <div class="card" style="grid-column:1/-1; text-align:center; padding:40px; color:var(--text-muted);">
        <div style="font-size:48px; margin-bottom:12px;">🌍</div>
        <p style="font-size:16px; margin-bottom:8px;">No tienes viajes planificados aún.</p>
        <p>Usa el asistente o el botón "Nuevo Viaje" para comenzar.</p>
      </div>`;
    return;
  }

  STATE.trips.forEach(trip => {
    const card = document.createElement("div");
    card.className = "trip-card";
    const days = trip.startDate && trip.endDate
      ? Math.ceil((new Date(trip.endDate) - new Date(trip.startDate)) / 86400000) + " noches"
      : "—";

    card.innerHTML = `
      <div class="trip-card-header">
        <div class="trip-destination">${trip.destination || "Destino"}</div>
        <div class="trip-origin">Desde ${trip.origin || "—"}</div>
      </div>
      <div class="trip-card-body">
        <div class="trip-meta">
          <div class="trip-meta-item">
            <div class="trip-meta-label">Fechas</div>
            <div class="trip-meta-value">${trip.startDate || "—"} → ${trip.endDate || "—"}</div>
          </div>
          <div class="trip-meta-item">
            <div class="trip-meta-label">Duración</div>
            <div class="trip-meta-value">${days}</div>
          </div>
          <div class="trip-meta-item">
            <div class="trip-meta-label">Viajeros</div>
            <div class="trip-meta-value">👥 ${trip.travelers || 1}</div>
          </div>
          <div class="trip-meta-item">
            <div class="trip-meta-label">Presupuesto</div>
            <div class="trip-meta-value">${trip.budget ? formatMoney(trip.budget, trip.currency) : "—"}</div>
          </div>
        </div>
        <span class="status-badge status-${trip.status || 'PLANNED'}">${trip.status || 'PLANNED'}</span>
      </div>
      <div class="trip-card-footer">
        <button class="btn-secondary btn-sm" onclick="viewItinerary('${trip.tripId}', '${trip.destination}')">📅 Itinerario</button>
        <button class="btn-secondary btn-sm" onclick="viewTripReservations('${trip.tripId}')">📋 Reservas</button>
        <button class="btn-secondary btn-sm" onclick="deleteTrip('${trip.tripId}')">🗑️</button>
      </div>
    `;
    container.appendChild(card);
  });
}

async function createTrip() {
  const destination = document.getElementById("newTripDestination").value.trim();
  const origin = document.getElementById("newTripOrigin").value.trim();
  const startDate = document.getElementById("newTripStartDate").value;
  const endDate = document.getElementById("newTripEndDate").value;
  const travelers = parseInt(document.getElementById("newTripTravelers").value) || 2;
  const budget = document.getElementById("newTripBudget").value;
  const currency = document.getElementById("newTripCurrency").value;
  const notes = document.getElementById("newTripNotes").value;

  if (!destination || !startDate || !endDate) {
    showToast("Completa los campos obligatorios (*)", "error");
    return;
  }

  const tripData = {
    destination, origin, startDate, endDate, travelers,
    currency, notes,
    budget: budget ? parseFloat(budget) : null,
    userId: STATE.userId,
  };

  try {
    const res = await apiFetch("/trips", {
      method: "POST",
      body: JSON.stringify(tripData),
    });

    if (res.ok) {
      const data = await res.json();
      STATE.trips.unshift(data.trip);
      showToast(`✅ Viaje a ${destination} creado exitosamente`, "success");
    } else {
      throw new Error();
    }
  } catch {
    // Modo demo: guardar en localStorage
    const demoTrip = {
      ...tripData,
      tripId: `trip-${Date.now()}`,
      status: "PLANNED",
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
    };
    STATE.trips.unshift(demoTrip);
    localStorage.setItem("tp_trips", JSON.stringify(STATE.trips));
    showToast(`✅ Viaje a ${destination} creado (modo demo)`, "success");
  }

  closeModal("newTripModal");
  renderTrips();
  loadDashboardStats();
}

function deleteTrip(tripId) {
  if (!confirm("¿Eliminar este viaje?")) return;
  apiFetch(`/trips/${encodeURIComponent(tripId)}`, { method: "DELETE" })
    .then(res => {
      if (res.ok) {
        STATE.trips = STATE.trips.filter(t => t.tripId !== tripId);
        localStorage.setItem("tp_trips", JSON.stringify(STATE.trips));
        renderTrips();
        loadDashboardStats();
        showToast("Viaje eliminado", "success");
      } else {
        showToast("Error al eliminar viaje", "error");
      }
    })
    .catch(() => {
      STATE.trips = STATE.trips.filter(t => t.tripId !== tripId);
      localStorage.setItem("tp_trips", JSON.stringify(STATE.trips));
      renderTrips();
      loadDashboardStats();
      showToast("Viaje eliminado (modo demo)", "info");
    });
}

async function viewItinerary(tripId, destination) {
  STATE.currentTripId = tripId;
  STATE.currentTripDestination = destination;
  document.getElementById("itineraryTitle").textContent = `📅 Itinerario — ${destination}`;

  const content = document.getElementById("itineraryContent");
  content.innerHTML = `<div class="loading-state"><div class="spinner"></div><p>Cargando itinerario...</p></div>`;
  document.getElementById("itineraryView").style.display = "block";
  document.getElementById("itineraryView").scrollIntoView({ behavior: "smooth" });

  try {
    const res = await apiFetch(`/trips/${tripId}/itinerary`);
    if (res.ok) {
      const data = await res.json();
      renderItineraryContent(data.itinerary || data);
    } else {
      throw new Error();
    }
  } catch {
    // Demo itinerary
    renderItineraryContent({
      title: `Itinerario de viaje a ${destination}`,
      days: [
        {
          date: "2027-01-15", activities: [
            { time: "09:00", description: "Llegada y check-in en hotel", location: destination, type: "LOGISTICS" },
            { time: "14:00", description: "Visita a la Ciudad Amurallada", location: destination, type: "TOURISM" },
            { time: "19:00", description: "Cena en restaurante típico", location: destination, type: "FOOD" },
          ]
        },
        {
          date: "2027-01-16", activities: [
            { time: "08:00", description: "Tour a las Islas del Rosario", location: "Islas del Rosario", type: "EXCURSION" },
            { time: "20:00", description: "Regreso y descanso", location: destination, type: "LEISURE" },
          ]
        },
        {
          date: "2027-01-17", activities: [
            { time: "10:00", description: "Playa de Bocagrande", location: "Bocagrande", type: "BEACH" },
            { time: "15:00", description: "Visita al Museo del Oro Zenú", location: destination, type: "CULTURE" },
          ]
        },
        {
          date: "2027-01-18", activities: [
            { time: "09:00", description: "Desayuno y compras de artesanías", location: destination, type: "SHOPPING" },
            { time: "13:00", description: "Traslado al aeropuerto", location: "CTG", type: "LOGISTICS" },
          ]
        },
      ],
      notes: "Itinerario generado por TripPlanner AI (modo demo)"
    });
  }
}

function renderItineraryContent(itinerary) {
  const container = document.getElementById("itineraryContent");
  if (!itinerary || !itinerary.days) {
    container.innerHTML = `<p style="color:var(--text-muted); padding:16px;">No hay itinerario disponible aún. Pídele al asistente que genere uno.</p>`;
    return;
  }

  const typeIcons = { TOURISM: "🏛️", FOOD: "🍽️", LOGISTICS: "🚗", EXCURSION: "🚢", BEACH: "🏖️", CULTURE: "🎭", SHOPPING: "🛍️", LEISURE: "😴", ACTIVITY: "🎯" };

  const dest = STATE.currentTripDestination || "";
  const mapQuery = encodeURIComponent(dest || "Cartagena");

  const extras = `
    <div class="itinerary-extras" style="margin-top:16px; display:flex; flex-direction:column; gap:14px;">
      <div class="card" style="padding:12px;">
        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px; flex-wrap:wrap; gap:8px;">
          <h3 style="margin:0; font-size:15px;">Mapa del destino</h3>
          <button class="btn btn-sm btn-outline" onclick="window.print()">Descargar PDF</button>
        </div>
        <div style="position:relative; padding-bottom:56.25%; height:0; overflow:hidden; border-radius:8px;">
          <iframe title="Mapa ${dest}" src="https://www.google.com/maps?q=${mapQuery}&output=embed"
            style="position:absolute; top:0; left:0; width:100%; height:100%; border:0;"
            loading="lazy" referrerpolicy="no-referrer-when-downgrade"></iframe>
        </div>
        <p style="font-size:12px; color:var(--text-muted); margin-top:6px;">
          Ubicacion de ${dest || "tu destino"}. Abre en Google Maps para rutas detalladas.
        </p>
      </div>
    </div>
  `;

  container.innerHTML = itinerary.days.map(day => `
    <div class="day-block">
      <div class="day-title">📅 ${day.date} ${day.activities ? `· ${day.activities.length} actividades` : ""}</div>
      ${(day.activities || []).map(act => `
        <div class="activity-item">
          <div class="activity-time">${act.time || ""}</div>
          <div>
            <div class="activity-desc">${typeIcons[act.type] || "📌"} ${act.description}</div>
            ${act.location ? `<div class="activity-location">📍 ${act.location}</div>` : ""}
          </div>
        </div>
      `).join("")}
    </div>
  `).join("") + (itinerary.notes ? `<p style="color:var(--text-muted); font-size:13px; margin-top:12px;">📝 ${itinerary.notes}</p>` : "") + extras;
}

function closeItinerary() {
  document.getElementById("itineraryView").style.display = "none";
}

function viewTripReservations(tripId) {
  showModule("reservations");
  document.getElementById("reservationFilter").value = "";
  loadReservations(tripId);
}

// ---------------------------------------------------------------------------
// RESERVAS — API Backend
// ---------------------------------------------------------------------------
async function loadReservations(tripIdFilter = null) {
  document.getElementById("reservationsLoading").style.display = "flex";
  document.getElementById("reservationsList").innerHTML = "";

  const path = tripIdFilter
    ? `/reservations?tripId=${tripIdFilter}`
    : `/reservations`;

  try {
    const res = await apiFetch(path);
    if (!res.ok) throw new Error();
    const data = await res.json();
    STATE.reservations = data.reservations || [];
  } catch {
    STATE.reservations = JSON.parse(localStorage.getItem("tp_reservations") || "[]");
    if (tripIdFilter) {
      STATE.reservations = STATE.reservations.filter(r => r.tripId === tripIdFilter);
    }
  } finally {
    document.getElementById("reservationsLoading").style.display = "none";
    renderReservations();
  }
}

function filterReservations() {
  renderReservations();
}

function renderReservations() {
  const container = document.getElementById("reservationsList");
  const filter = document.getElementById("reservationFilter").value;
  const list = filter ? STATE.reservations.filter(r => r.type === filter) : STATE.reservations;

  if (list.length === 0) {
    container.innerHTML = `
      <div class="card" style="text-align:center; padding:40px; color:var(--text-muted);">
        <div style="font-size:48px; margin-bottom:12px;">📋</div>
        <p>No hay reservas registradas aún.</p>
        <p style="margin-top:8px;">Pídele al asistente que registre una reserva o usa el botón "Nueva Reserva".</p>
      </div>`;
    return;
  }

  list.forEach(res => {
    const typeIcon = { FLIGHT: "✈️", HOTEL: "🏨", ACTIVITY: "🎯" }[res.type] || "📋";
    const typeLabel = { FLIGHT: "Vuelo", HOTEL: "Hotel", ACTIVITY: "Actividad" }[res.type] || res.type;
    const details = typeof res.details === "object" ? (res.details.description || JSON.stringify(res.details)) : (res.details || "Sin detalles");

    const item = document.createElement("div");
    item.className = "reservation-item";
    item.innerHTML = `
      <div class="res-type-icon">${typeIcon}</div>
      <div class="res-info">
        <div class="res-title">${typeLabel}</div>
        <div class="res-subtitle">${details}</div>
        ${res.date ? `<div class="res-subtitle">📅 ${res.date}</div>` : ""}
      </div>
      <div class="res-price">${res.price ? formatMoney(res.price, res.currency) : "—"}</div>
      <div class="res-status">
        <span class="res-status-badge status-${res.status || 'SIMULADA'}">${res.status || 'SIMULADA'}</span>
      </div>
      <div class="res-actions">
        <button onclick="confirmReservation('${res.reservationId}')">✓ Confirmar</button>
        <button onclick="deleteReservation('${res.reservationId}')">🗑️</button>
      </div>
    `;
    container.appendChild(item);
  });
}

async function createReservation() {
  const tripId = document.getElementById("newResTripId").value;
  const type = document.getElementById("newResType").value;
  const details = document.getElementById("newResDetails").value;
  const price = document.getElementById("newResPrice").value;
  const currency = document.getElementById("newResCurrency").value;
  const date = document.getElementById("newResDate").value;

  if (!tripId) {
    showToast("Selecciona un viaje para la reserva", "error");
    return;
  }

  const resData = {
    tripId, type, details,
    currency, date,
    price: price ? parseFloat(price) : null,
    status: "SIMULADA",
    userId: STATE.userId,
  };

  try {
    const res = await apiFetch("/reservations", {
      method: "POST",
      body: JSON.stringify(resData),
    });
    if (res.ok) {
      const data = await res.json();
      STATE.reservations.unshift(data.reservation);
      showToast(`✅ Reserva de ${type} registrada (SIMULADA)`, "success");
    } else {
      throw new Error();
    }
  } catch {
    const demoRes = {
      ...resData,
      reservationId: `res-${Date.now()}`,
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
    };
    STATE.reservations.unshift(demoRes);
    localStorage.setItem("tp_reservations", JSON.stringify(STATE.reservations));
    showToast(`✅ Reserva registrada (modo demo)`, "success");
  }

  closeModal("newReservationModal");
  renderReservations();
  loadDashboardStats();
}

async function confirmReservation(reservationId) {
  try {
    const res = await apiFetch(`/reservations/${reservationId}`, {
      method: "PUT",
      body: JSON.stringify({ status: "CONFIRMADA" }),
    });
    if (!res.ok) throw new Error();
  } catch {
    // Demo
  }
  const r = STATE.reservations.find(r => r.reservationId === reservationId);
  if (r) {
    r.status = "CONFIRMADA";
    localStorage.setItem("tp_reservations", JSON.stringify(STATE.reservations));
  }
  renderReservations();
  showToast("Reserva confirmada", "success");
}

function deleteReservation(reservationId) {
  if (!confirm("¿Eliminar esta reserva?")) return;
  STATE.reservations = STATE.reservations.filter(r => r.reservationId !== reservationId);
  localStorage.setItem("tp_reservations", JSON.stringify(STATE.reservations));
  renderReservations();
  loadDashboardStats();
  showToast("Reserva eliminada", "info");
}

// ---------------------------------------------------------------------------
// MODALES
// ---------------------------------------------------------------------------
function openNewTripModal() {
  // Fecha por defecto
  const future = new Date(Date.now() + 30 * 86400000);
  const future2 = new Date(Date.now() + 34 * 86400000);
  const fmt = d => d.toISOString().split("T")[0];
  document.getElementById("newTripStartDate").value = fmt(future);
  document.getElementById("newTripEndDate").value = fmt(future2);
  document.getElementById("newTripModal").style.display = "flex";
}

function openNewReservationModal() {
  // Poblar selector de viajes
  const sel = document.getElementById("newResTripId");
  sel.innerHTML = '<option value="">Seleccionar viaje...</option>';
  STATE.trips.forEach(t => {
    const opt = document.createElement("option");
    opt.value = t.tripId;
    opt.textContent = `${t.destination} (${t.startDate})`;
    sel.appendChild(opt);
  });

  if (STATE.trips.length === 0) {
    // Cargar viajes si no están
    loadTrips().then(() => {
      sel.innerHTML = '<option value="">Seleccionar viaje...</option>';
      STATE.trips.forEach(t => {
        const opt = document.createElement("option");
        opt.value = t.tripId;
        opt.textContent = `${t.destination} (${t.startDate})`;
        sel.appendChild(opt);
      });
    });
  }

  document.getElementById("newReservationModal").style.display = "flex";
}

function closeModal(id) {
  document.getElementById(id).style.display = "none";
}

// ---------------------------------------------------------------------------
// DASHBOARD STATS
// ---------------------------------------------------------------------------
async function loadDashboardStats() {
  // Cargar desde API o localStorage
  let trips = STATE.trips;
  let reservations = STATE.reservations;

  if (trips.length === 0) {
    trips = JSON.parse(localStorage.getItem("tp_trips") || "[]");
    STATE.trips = trips;
  }
  if (reservations.length === 0) {
    reservations = JSON.parse(localStorage.getItem("tp_reservations") || "[]");
    STATE.reservations = reservations;
  }

  document.getElementById("statTrips").textContent = trips.length;
  document.getElementById("statReservations").textContent = reservations.length;

  // Próximos viajes
  const upcoming = trips
    .filter(t => t.status !== "CANCELLED" && t.startDate >= new Date().toISOString().split("T")[0])
    .slice(0, 3);

  const upcomingContainer = document.getElementById("upcomingTripsList");
  if (upcoming.length === 0) {
    upcomingContainer.innerHTML = `
      <div class="empty-state">
        <span>🌍</span>
        <p>No tienes viajes próximos.<br/>
        <a onclick="showModule('chat')">Pídele al asistente que te ayude</a></p>
      </div>`;
  } else {
    upcomingContainer.innerHTML = upcoming.map(t => `
      <div style="padding:8px 0; border-bottom:1px solid var(--border); display:flex; justify-content:space-between; align-items:center;">
        <div>
          <div style="font-weight:700;">${t.destination}</div>
          <div style="font-size:12px; color:var(--text-muted);">${t.startDate} · ${t.travelers || 1} viajeros</div>
        </div>
        <span class="status-badge status-${t.status || 'PLANNED'}">${t.status || 'PLANNED'}</span>
      </div>
    `).join("");
  }
}

// ---------------------------------------------------------------------------
// ESCENARIO DE DEMOSTRACIÓN
// ---------------------------------------------------------------------------
async function runDemoScenario() {
  showModule("chat");
  setTimeout(() => {
    const msg = "Quiero organizar unas vacaciones de cuatro días en Cartagena. Salgo desde Bogotá, viajo con dos personas más y tengo un presupuesto de 5 millones de pesos. Quiero conocer el clima, encontrar vuelos y organizar algunas actividades turísticas.";
    document.getElementById("chatInput").value = msg;
    sendChatMessage();
  }, 300);
}

// ---------------------------------------------------------------------------
// UTILIDADES
// ---------------------------------------------------------------------------
function formatMoney(amount, currency = "COP") {
  const num = parseFloat(amount);
  if (isNaN(num)) return `${amount} ${currency}`;
  return new Intl.NumberFormat("es-CO", {
    style: "currency",
    currency: currency === "COP" ? "COP" : (currency === "USD" ? "USD" : "EUR"),
    minimumFractionDigits: 0,
  }).format(num);
}

function showToast(message, type = "info") {
  const container = document.getElementById("toastContainer");
  const toast = document.createElement("div");
  toast.className = `toast toast-${type}`;
  toast.textContent = message;
  container.appendChild(toast);
  setTimeout(() => toast.remove(), 4000);
}
