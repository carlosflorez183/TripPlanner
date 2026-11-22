# TripPlanner AI — Guía de Despliegue Completo

Instrucciones para desplegar el sistema desde cero en 4 pasos.

---

## Pre-requisitos

- Python 3.12
- AWS CLI configurado con cuenta `762233733426` en `us-east-1`
- AWS SAM CLI (`pip install aws-sam-cli`)
- AgentCore CLI instalado (`pip install agentcore`)
- Node.js (para CDK del agentcore, ya instalado)

---

## Paso 1 — Backend Serverless (Lambda + DynamoDB + API Gateway)

```bash
cd a2a-trip-planner/TripPlanner/backend/

# Construir
sam build

# Desplegar (primera vez — sigue las instrucciones interactivas)
sam deploy --guided
# Stack name: TripPlanner-Backend
# Region: us-east-1
# Stage: prod

# Obtener la URL del API Gateway
sam list stack-outputs --stack-name TripPlanner-Backend
# Copiar el valor de "ApiBaseUrl", ej:
# https://abc123def.execute-api.us-east-1.amazonaws.com/prod
```

---

## Paso 2 — Configurar agentcore.json con la URL del API

Editar `agentcore/agentcore.json`, en el runtime `OrchestratorAgent`:

```json
{ "name": "API_BASE_URL", "value": "https://abc123def.execute-api.us-east-1.amazonaws.com/prod" }
```

Reemplaza `abc123def` con el ID real de tu API Gateway.

---

## Paso 3 — Redesplegar los agentes en AgentCore

```bash
cd a2a-trip-planner/TripPlanner/

# Verificar la configuración
agentcore validate

# Desplegar (actualiza OrchestratorAgent con la nueva variable)
agentcore deploy

# Verificar estado
agentcore status

# Probar invocación
agentcore invoke --name OrchestratorAgent --prompt "Busca vuelos de BOG a CTG para el 2027-01-15"
```

---

## Paso 4 — Frontend en S3

### 4a. Configurar URLs en el frontend

Editar `frontend/app.js`:

```javascript
const CONFIG = {
  // Obtener con: agentcore fetch access --name OrchestratorAgent
  AGENT_URL: "https://bedrock-agentcore.us-east-1.amazonaws.com/runtimes/TripPlanner_OrchestratorAgent/invocations",
  
  // URL del Paso 1
  API_BASE_URL: "https://abc123def.execute-api.us-east-1.amazonaws.com/prod",
};
```

### 4b. Crear bucket S3 y subir archivos

```bash
# Crear bucket (nombre único global)
BUCKET_NAME="tripplanner-ai-frontend-$(date +%s)"
aws s3 mb s3://$BUCKET_NAME --region us-east-1

# Habilitar hosting estático
aws s3 website s3://$BUCKET_NAME \
  --index-document index.html \
  --error-document index.html

# Política pública de lectura
aws s3api put-bucket-policy --bucket $BUCKET_NAME --policy '{
  "Version": "2012-10-17",
  "Statement": [{
    "Sid": "PublicRead",
    "Effect": "Allow",
    "Principal": "*",
    "Action": "s3:GetObject",
    "Resource": "arn:aws:s3:::'"$BUCKET_NAME"'/*"
  }]
}'

# Desactivar bloqueo de acceso público
aws s3api put-public-access-block --bucket $BUCKET_NAME \
  --public-access-block-configuration "BlockPublicAcls=false,IgnorePublicAcls=false,BlockPublicPolicy=false,RestrictPublicBuckets=false"

# Subir el frontend
aws s3 sync frontend/ s3://$BUCKET_NAME/ --delete

# URL del frontend
echo "Frontend disponible en: http://$BUCKET_NAME.s3-website-us-east-1.amazonaws.com"
```

### 4c. (Opcional) CloudFront para HTTPS

```bash
aws cloudfront create-distribution \
  --origin-domain-name "$BUCKET_NAME.s3-website-us-east-1.amazonaws.com" \
  --default-root-object index.html
```

---

## Verificación del Escenario Obligatorio

Una vez todo desplegado, ejecutar en el frontend:

1. Abrir la URL del S3 (o CloudFront)
2. Ir a **Asistente** → Botón **▶ Iniciar Demostración**
3. El asistente consultará vuelos y clima
4. Ir a **Mis Viajes** → **Nuevo Viaje** (Cartagena, 15-19 ene 2027)
5. Ir a **Asistente** → pedir itinerario para el viaje
6. Ir a **Reservas** → registrar el vuelo
7. Verificar en **Mis Viajes** que los datos persisten
8. Cerrar y reabrir el browser — los datos deben seguir ahí (DynamoDB)

---

## Troubleshooting

### Error CORS en el frontend
Verificar que API Gateway tiene CORS habilitado (`AllowOrigin: '*'`).  
Las Lambdas también devuelven headers CORS en cada respuesta.

### OrchestratorAgent no conecta con backend
Verificar que `API_BASE_URL` está bien configurada en `agentcore.json` y que se ejecutó `agentcore deploy`.

### FlightsAgent falla
Verificar `SMITHERY_API_KEY` en `.env.local`. Si Smithery no responde, el agente usa el MCP local como fallback.

### SAM deploy falla
```bash
# Verificar credenciales AWS
aws sts get-caller-identity

# Eliminar stack para recrear desde cero
aws cloudformation delete-stack --stack-name TripPlanner-Backend
```
