"""
TripPlanner AI — Deploy Script
Despliega todo el stack de backend y frontend usando boto3 y AWS CLI.

Orden:
  1. Cognito User Pool + App Client
  2. DynamoDB tables
  3. IAM Role para Lambdas
  4. Lambda functions (empaquetar + subir)
  5. API Gateway HTTP API
  6. S3 bucket frontend
  7. Actualizar agentcore.json con API_BASE_URL
  8. Actualizar frontend/app.js con URLs reales
  9. Subir frontend a S3

Uso:
    python deploy/deploy_all.py
"""

import boto3
import json
import os
import sys
import time
import zipfile
import io
import pathlib

REGION = "us-east-1"
ACCOUNT = "762233733426"
STAGE = "prod"
PROJECT = "TripPlanner"

# ARNs conocidos del stack AgentCore
ORCHESTRATOR_ARN = "arn:aws:bedrock-agentcore:us-east-1:762233733426:runtime/TripPlanner_OrchestratorAgent-wlgjt0E83A"
ORCHESTRATOR_RUNTIME_ID = "TripPlanner_OrchestratorAgent-wlgjt0E83A"

BASE_DIR = pathlib.Path(__file__).parent.parent  # TripPlanner/

session = boto3.Session(region_name=REGION)

def log(msg):
    print(f"\n{'='*60}")
    print(f"  {msg}")
    print('='*60)

def ok(msg):
    print(f"  ✅ {msg}")

def info(msg):
    print(f"  ℹ️  {msg}")

def err(msg):
    print(f"  ❌ {msg}")

# ---------------------------------------------------------------------------
# 1. COGNITO USER POOL
# ---------------------------------------------------------------------------
def deploy_cognito():
    log("STEP 1: Cognito User Pool")
    cognito = session.client("cognito-idp")

    pool_name = f"{PROJECT}-UserPool-{STAGE}"

    # Verificar si ya existe
    existing = cognito.list_user_pools(MaxResults=50)
    for pool in existing["UserPools"]:
        if pool["Name"] == pool_name:
            pool_id = pool["Id"]
            ok(f"User Pool ya existe: {pool_id}")
            # Obtener el App Client
            clients = cognito.list_user_pool_clients(UserPoolId=pool_id, MaxResults=10)
            client_id = clients["UserPoolClients"][0]["ClientId"] if clients["UserPoolClients"] else None
            if not client_id:
                client_id = _create_app_client(cognito, pool_id)
            return pool_id, client_id

    # Crear User Pool
    response = cognito.create_user_pool(
        PoolName=pool_name,
        Policies={
            "PasswordPolicy": {
                "MinimumLength": 8,
                "RequireUppercase": False,
                "RequireLowercase": True,
                "RequireNumbers": True,
                "RequireSymbols": False,
            }
        },
        AutoVerifiedAttributes=["email"],
        UsernameAttributes=["email"],
        Schema=[
            {"Name": "email", "AttributeDataType": "String", "Required": True, "Mutable": True},
            {"Name": "name", "AttributeDataType": "String", "Required": False, "Mutable": True},
        ],
        UserPoolTags={"Project": PROJECT, "Stage": STAGE},
    )
    pool_id = response["UserPool"]["Id"]
    ok(f"User Pool creado: {pool_id}")

    client_id = _create_app_client(cognito, pool_id)
    return pool_id, client_id


def _create_app_client(cognito, pool_id):
    response = cognito.create_user_pool_client(
        UserPoolId=pool_id,
        ClientName=f"{PROJECT}-WebClient",
        GenerateSecret=False,  # SPA no necesita secret
        ExplicitAuthFlows=[
            "ALLOW_USER_PASSWORD_AUTH",
            "ALLOW_USER_SRP_AUTH",
            "ALLOW_REFRESH_TOKEN_AUTH",
        ],
        SupportedIdentityProviders=["COGNITO"],
        CallbackURLs=["http://localhost:3000"],
        LogoutURLs=["http://localhost:3000"],
        AllowedOAuthFlows=["implicit"],
        AllowedOAuthScopes=["email", "openid", "profile"],
        AllowedOAuthFlowsUserPoolClient=True,
    )
    client_id = response["UserPoolClient"]["ClientId"]
    ok(f"App Client creado: {client_id}")
    return client_id


# ---------------------------------------------------------------------------
# 2. DYNAMODB TABLES
# ---------------------------------------------------------------------------
def deploy_dynamodb():
    log("STEP 2: DynamoDB Tables")
    ddb = session.client("dynamodb")

    tables = {
        f"{PROJECT}-Users-{STAGE}": {
            "AttributeDefinitions": [{"AttributeName": "userId", "AttributeType": "S"}],
            "KeySchema": [{"AttributeName": "userId", "KeyType": "HASH"}],
        },
        f"{PROJECT}-Trips-{STAGE}": {
            "AttributeDefinitions": [
                {"AttributeName": "userId", "AttributeType": "S"},
                {"AttributeName": "tripId", "AttributeType": "S"},
            ],
            "KeySchema": [
                {"AttributeName": "userId", "KeyType": "HASH"},
                {"AttributeName": "tripId", "KeyType": "RANGE"},
            ],
        },
        f"{PROJECT}-Itineraries-{STAGE}": {
            "AttributeDefinitions": [
                {"AttributeName": "tripId", "AttributeType": "S"},
                {"AttributeName": "itineraryId", "AttributeType": "S"},
            ],
            "KeySchema": [
                {"AttributeName": "tripId", "KeyType": "HASH"},
                {"AttributeName": "itineraryId", "KeyType": "RANGE"},
            ],
        },
        f"{PROJECT}-Reservations-{STAGE}": {
            "AttributeDefinitions": [
                {"AttributeName": "userId", "AttributeType": "S"},
                {"AttributeName": "reservationId", "AttributeType": "S"},
            ],
            "KeySchema": [
                {"AttributeName": "userId", "KeyType": "HASH"},
                {"AttributeName": "reservationId", "KeyType": "RANGE"},
            ],
        },
    }

    arns = {}
    for table_name, schema in tables.items():
        try:
            resp = ddb.describe_table(TableName=table_name)
            arn = resp["Table"]["TableArn"]
            ok(f"Tabla ya existe: {table_name}")
            arns[table_name] = arn
            continue
        except ddb.exceptions.ResourceNotFoundException:
            pass

        resp = ddb.create_table(
            TableName=table_name,
            BillingMode="PAY_PER_REQUEST",
            Tags=[{"Key": "Project", "Value": PROJECT}],
            **schema,
        )
        arn = resp["TableDescription"]["TableArn"]
        ok(f"Tabla creada: {table_name}")
        arns[table_name] = arn

    # Esperar que todas estén ACTIVE
    info("Esperando que las tablas estén activas...")
    ddb_res = session.resource("dynamodb")
    for table_name in tables:
        t = ddb_res.Table(table_name)
        t.meta.client.get_waiter("table_exists").wait(TableName=table_name)
    ok("Todas las tablas están activas")
    return arns


# ---------------------------------------------------------------------------
# 3. IAM ROLE PARA LAMBDAS
# ---------------------------------------------------------------------------
def deploy_iam_role(table_arns):
    log("STEP 3: IAM Role para Lambdas")
    iam = session.client("iam")
    role_name = f"{PROJECT}-Lambda-Role-{STAGE}"

    trust_policy = {
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Principal": {"Service": "lambda.amazonaws.com"},
            "Action": "sts:AssumeRole",
        }],
    }

    # Crear o obtener el role
    try:
        resp = iam.get_role(RoleName=role_name)
        role_arn = resp["Role"]["Arn"]
        ok(f"IAM Role ya existe: {role_arn}")
    except iam.exceptions.NoSuchEntityException:
        resp = iam.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps(trust_policy),
            Description="Execution role for TripPlanner Lambda functions",
            Tags=[{"Key": "Project", "Value": PROJECT}],
        )
        role_arn = resp["Role"]["Arn"]
        ok(f"IAM Role creado: {role_arn}")
        # Esperar propagación
        time.sleep(10)

    # Adjuntar política de logs básica
    try:
        iam.attach_role_policy(
            RoleName=role_name,
            PolicyArn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
        )
    except Exception:
        pass  # Ya adjunta

    # Política inline de DynamoDB
    ddb_policy = {
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Action": [
                "dynamodb:PutItem", "dynamodb:GetItem", "dynamodb:UpdateItem",
                "dynamodb:DeleteItem", "dynamodb:Query", "dynamodb:Scan",
            ],
            "Resource": list(table_arns.values()),
        }],
    }
    iam.put_role_policy(
        RoleName=role_name,
        PolicyName=f"{PROJECT}-DynamoDB-Access",
        PolicyDocument=json.dumps(ddb_policy),
    )
    ok("Política DynamoDB aplicada")
    return role_arn


# ---------------------------------------------------------------------------
# 4. LAMBDA FUNCTIONS
# ---------------------------------------------------------------------------
def _zip_lambda(lambda_dir: pathlib.Path) -> bytes:
    """Empaqueta el directorio de la lambda en un ZIP en memoria."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in lambda_dir.rglob("*"):
            if f.is_file():
                zf.write(f, f.relative_to(lambda_dir))
    buf.seek(0)
    return buf.read()


def deploy_lambdas(role_arn, table_arns):
    log("STEP 4: Lambda Functions")
    lam = session.client("lambda")

    lambdas_dir = BASE_DIR / "backend" / "lambdas"

    env_vars = {
        "TRIPS_TABLE": f"{PROJECT}-Trips-{STAGE}",
        "ITINERARIES_TABLE": f"{PROJECT}-Itineraries-{STAGE}",
        "RESERVATIONS_TABLE": f"{PROJECT}-Reservations-{STAGE}",
        "USERS_TABLE": f"{PROJECT}-Users-{STAGE}",
        "CORS_ORIGIN": "*",
    }

    function_names = {}

    lambda_dirs = [d for d in lambdas_dir.iterdir() if d.is_dir()]

    for lambda_dir in sorted(lambda_dirs):
        func_key = lambda_dir.name  # e.g. "create_trip"
        func_name = f"{PROJECT}-{func_key.replace('_', '-').title().replace('-', '')}-{STAGE}"
        # Normalizar nombre: TripPlanner-CreateTrip-prod
        parts = func_key.split("_")
        normalized = "".join(p.capitalize() for p in parts)
        func_name = f"{PROJECT}-{normalized}-{STAGE}"

        zip_bytes = _zip_lambda(lambda_dir)

        try:
            # Verificar si existe
            lam.get_function(FunctionName=func_name)
            # Actualizar código
            lam.update_function_code(FunctionName=func_name, ZipFile=zip_bytes)
            lam.update_function_configuration(
                FunctionName=func_name,
                Environment={"Variables": env_vars},
            )
            ok(f"Lambda actualizada: {func_name}")
        except lam.exceptions.ResourceNotFoundException:
            # Crear nueva
            resp = lam.create_function(
                FunctionName=func_name,
                Runtime="python3.12",
                Role=role_arn,
                Handler="app.handler",
                Code={"ZipFile": zip_bytes},
                Description=f"TripPlanner {normalized} handler",
                Timeout=30,
                MemorySize=256,
                Environment={"Variables": env_vars},
                Tags={"Project": PROJECT},
            )
            ok(f"Lambda creada: {func_name}")

        # Esperar que esté activa
        waiter = lam.get_waiter("function_active_v2")
        waiter.wait(FunctionName=func_name)

        function_names[func_key] = func_name

    return function_names


# ---------------------------------------------------------------------------
# 5. API GATEWAY HTTP API
# ---------------------------------------------------------------------------
def deploy_api_gateway(function_names, cognito_pool_id, cognito_client_id):
    log("STEP 5: API Gateway HTTP API")
    apigw = session.client("apigatewayv2")
    lam = session.client("lambda")

    api_name = f"{PROJECT}-API-{STAGE}"

    # Verificar si ya existe
    apis = apigw.get_apis()
    existing_api = next((a for a in apis["Items"] if a["Name"] == api_name), None)

    if existing_api:
        api_id = existing_api["ApiId"]
        api_endpoint = existing_api["ApiEndpoint"]
        ok(f"API Gateway ya existe: {api_id}")
    else:
        resp = apigw.create_api(
            Name=api_name,
            ProtocolType="HTTP",
            CorsConfiguration={
                "AllowOrigins": ["*"],
                "AllowMethods": ["GET", "POST", "PUT", "DELETE", "OPTIONS"],
                "AllowHeaders": ["Content-Type", "Authorization", "X-User-Id"],
                "MaxAge": 300,
            },
            Tags={"Project": PROJECT},
        )
        api_id = resp["ApiId"]
        api_endpoint = resp["ApiEndpoint"]
        ok(f"API Gateway creado: {api_id}")

    # Authorizer Cognito JWT
    auth_name = "CognitoJWTAuth"
    authorizers = apigw.get_authorizers(ApiId=api_id)
    existing_auth = next((a for a in authorizers["Items"] if a["Name"] == auth_name), None)

    if existing_auth:
        authorizer_id = existing_auth["AuthorizerId"]
        ok(f"Authorizer ya existe: {authorizer_id}")
    else:
        auth_resp = apigw.create_authorizer(
            ApiId=api_id,
            AuthorizerType="JWT",
            Name=auth_name,
            IdentitySource=["$request.header.Authorization"],
            JwtConfiguration={
                "Audience": [cognito_client_id],
                "Issuer": f"https://cognito-idp.{REGION}.amazonaws.com/{cognito_pool_id}",
            },
        )
        authorizer_id = auth_resp["AuthorizerId"]
        ok(f"Authorizer JWT Cognito creado: {authorizer_id}")

    # Rutas y sus Lambdas
    routes = [
        ("POST",   "/trips",                                        "create_trip"),
        ("GET",    "/trips",                                        "get_trips"),
        ("PUT",    "/trips/{tripId}",                               "update_trip"),
        ("DELETE", "/trips/{tripId}",                               "delete_trip"),
        ("POST",   "/trips/{tripId}/itinerary",                     "create_itinerary"),
        ("PUT",    "/trips/{tripId}/itinerary/{itineraryId}",       "update_itinerary"),
        ("POST",   "/reservations",                                 "create_reservation"),
        ("GET",    "/reservations",                                 "get_reservations"),
        ("PUT",    "/reservations/{reservationId}",                 "update_reservation"),
    ]

    # Stage de despliegue
    try:
        apigw.create_stage(
            ApiId=api_id,
            StageName=STAGE,
            AutoDeploy=True,
        )
        ok(f"Stage '{STAGE}' creado")
    except apigw.exceptions.ConflictException:
        ok(f"Stage '{STAGE}' ya existe")

    for method, path, func_key in routes:
        func_name = function_names.get(func_key)
        if not func_name:
            info(f"Lambda no encontrada para {func_key}, saltando ruta {method} {path}")
            continue

        func_arn = f"arn:aws:lambda:{REGION}:{ACCOUNT}:function:{func_name}"
        integration_uri = f"arn:aws:apigateway:{REGION}:lambda:path/2015-03-31/functions/{func_arn}/invocations"

        # Crear/actualizar integración
        integrations = apigw.get_integrations(ApiId=api_id)
        existing_int = next(
            (i for i in integrations["Items"] if func_name in i.get("IntegrationUri", "")),
            None
        )

        if existing_int:
            integration_id = existing_int["IntegrationId"]
        else:
            int_resp = apigw.create_integration(
                ApiId=api_id,
                IntegrationType="AWS_PROXY",
                IntegrationUri=integration_uri,
                PayloadFormatVersion="2.0",
                TimeoutInMillis=29000,
            )
            integration_id = int_resp["IntegrationId"]

        # Crear ruta
        route_key = f"{method} {path}"
        existing_routes = apigw.get_routes(ApiId=api_id)
        existing_route = next(
            (r for r in existing_routes["Items"] if r["RouteKey"] == route_key),
            None
        )

        if not existing_route:
            apigw.create_route(
                ApiId=api_id,
                RouteKey=route_key,
                Target=f"integrations/{integration_id}",
                AuthorizationType="JWT",
                AuthorizerId=authorizer_id,
            )
            ok(f"Ruta creada: {route_key} (JWT)")
        else:
            # Asegura que las rutas existentes también exijan el token de Cognito
            apigw.update_route(
                ApiId=api_id,
                RouteId=existing_route["RouteId"],
                AuthorizationType="JWT",
                AuthorizerId=authorizer_id,
            )
            ok(f"Ruta ya existe: {route_key} (JWT)")

        # Permiso Lambda para ser invocada por API Gateway
        try:
            lam.add_permission(
                FunctionName=func_name,
                StatementId=f"apigateway-{integration_id[:8]}",
                Action="lambda:InvokeFunction",
                Principal="apigateway.amazonaws.com",
                SourceArn=f"arn:aws:execute-api:{REGION}:{ACCOUNT}:{api_id}/*",
            )
        except lam.exceptions.ResourceConflictException:
            pass  # Permiso ya existe

    api_base_url = f"{api_endpoint}/{STAGE}"
    ok(f"API Base URL: {api_base_url}")
    return api_id, api_base_url


# ---------------------------------------------------------------------------
# 6. S3 FRONTEND BUCKET
# ---------------------------------------------------------------------------
def deploy_s3_frontend():
    log("STEP 6: S3 Bucket para Frontend")
    s3 = session.client("s3")

    bucket_name = f"tripplanner-ai-frontend-{ACCOUNT}"

    try:
        s3.head_bucket(Bucket=bucket_name)
        ok(f"Bucket ya existe: {bucket_name}")
    except Exception:
        s3.create_bucket(Bucket=bucket_name)
        ok(f"Bucket creado: {bucket_name}")

    # Deshabilitar bloqueo público
    s3.delete_public_access_block(Bucket=bucket_name)

    # Política pública de lectura
    public_policy = {
        "Version": "2012-10-17",
        "Statement": [{
            "Sid": "PublicRead",
            "Effect": "Allow",
            "Principal": "*",
            "Action": "s3:GetObject",
            "Resource": f"arn:aws:s3:::{bucket_name}/*",
        }],
    }
    s3.put_bucket_policy(Bucket=bucket_name, Policy=json.dumps(public_policy))

    # Habilitar static website hosting
    s3.put_bucket_website(
        Bucket=bucket_name,
        WebsiteConfiguration={
            "IndexDocument": {"Suffix": "index.html"},
            "ErrorDocument": {"Key": "index.html"},
        },
    )

    website_url = f"http://{bucket_name}.s3-website-{REGION}.amazonaws.com"
    ok(f"Website URL: {website_url}")
    return bucket_name, website_url


# ---------------------------------------------------------------------------
# 7. ACTUALIZAR AGENTCORE.JSON CON API_BASE_URL
# ---------------------------------------------------------------------------
def update_agentcore_json(api_base_url):
    log("STEP 7: Actualizar agentcore.json")
    agentcore_json_path = BASE_DIR / "agentcore" / "agentcore.json"

    with open(agentcore_json_path, encoding="utf-8") as f:
        config = json.load(f)

    for runtime in config.get("runtimes", []):
        if runtime.get("name") == "OrchestratorAgent":
            env_vars = runtime.setdefault("envVars", [])
            # Actualizar o agregar API_BASE_URL
            for ev in env_vars:
                if ev["name"] == "API_BASE_URL":
                    ev["value"] = api_base_url
                    ok(f"API_BASE_URL actualizada en agentcore.json: {api_base_url}")
                    break
            else:
                env_vars.append({"name": "API_BASE_URL", "value": api_base_url})
                ok(f"API_BASE_URL agregada en agentcore.json: {api_base_url}")

    with open(agentcore_json_path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# 8. ACTUALIZAR FRONTEND app.js CON URLS REALES
# ---------------------------------------------------------------------------
def update_frontend_config(api_base_url, cognito_pool_id, cognito_client_id, website_url):
    log("STEP 8: Actualizar frontend/app.js con URLs reales")
    app_js_path = BASE_DIR / "frontend" / "app.js"

    with open(app_js_path, encoding="utf-8") as f:
        content = f.read()

    # AgentCore endpoint
    agent_url = f"https://bedrock-agentcore.{REGION}.amazonaws.com/runtimes/{ORCHESTRATOR_RUNTIME_ID}/invocations"

    # Reemplazar CONFIG
    old_config = '''const CONFIG = {
  // URL del OrchestratorAgent en AgentCore (HTTP streaming)
  // Obtenerla con: agentcore fetch access --name OrchestratorAgent
  AGENT_URL: "https://bedrock-agentcore.us-east-1.amazonaws.com/runtimes/TripPlanner_OrchestratorAgent/invocations",

  // URL base del API Gateway (backend Lambda + DynamoDB)
  // Obtenerla de los outputs del SAM deploy
  API_BASE_URL: "https://YOUR_API_ID.execute-api.us-east-1.amazonaws.com/prod",
};'''

    new_config = f'''const CONFIG = {{
  AGENT_URL: "{agent_url}",
  API_BASE_URL: "{api_base_url}",
  COGNITO_REGION: "{REGION}",
  COGNITO_USER_POOL_ID: "{cognito_pool_id}",
  COGNITO_CLIENT_ID: "{cognito_client_id}",
}};'''

    content = content.replace(old_config, new_config)

    with open(app_js_path, "w", encoding="utf-8") as f:
        f.write(content)

    ok(f"app.js actualizado con URL del agente y API")
    ok(f"Cognito Pool: {cognito_pool_id} / Client: {cognito_client_id}")


# ---------------------------------------------------------------------------
# 9. SUBIR FRONTEND A S3
# ---------------------------------------------------------------------------
def upload_frontend(bucket_name):
    log("STEP 9: Subir Frontend a S3")
    s3 = session.client("s3")
    frontend_dir = BASE_DIR / "frontend"

    content_types = {
        ".html": "text/html; charset=utf-8",
        ".css": "text/css; charset=utf-8",
        ".js": "application/javascript; charset=utf-8",
        ".json": "application/json",
        ".png": "image/png",
        ".ico": "image/x-icon",
        ".svg": "image/svg+xml",
    }

    for f in frontend_dir.rglob("*"):
        if not f.is_file():
            continue
        key = str(f.relative_to(frontend_dir)).replace("\\", "/")
        ct = content_types.get(f.suffix, "application/octet-stream")
        s3.upload_file(
            str(f), bucket_name, key,
            ExtraArgs={"ContentType": ct, "CacheControl": "no-cache"},
        )
        ok(f"Subido: {key}")


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------
def main():
    print("\n" + "🚀 " * 20)
    print("   TripPlanner AI — DEPLOY COMPLETO")
    print("🚀 " * 20)
    print(f"   Cuenta: {ACCOUNT} | Región: {REGION} | Stage: {STAGE}")

    results = {}

    # 1. Cognito
    pool_id, client_id = deploy_cognito()
    results["cognito_pool_id"] = pool_id
    results["cognito_client_id"] = client_id

    # 2. DynamoDB
    table_arns = deploy_dynamodb()
    results["table_arns"] = table_arns

    # 3. IAM
    role_arn = deploy_iam_role(table_arns)
    results["lambda_role_arn"] = role_arn

    # 4. Lambdas
    function_names = deploy_lambdas(role_arn, table_arns)
    results["functions"] = function_names

    # 5. API Gateway
    api_id, api_base_url = deploy_api_gateway(function_names, pool_id, client_id)
    results["api_id"] = api_id
    results["api_base_url"] = api_base_url

    # 6. S3
    bucket_name, website_url = deploy_s3_frontend()
    results["s3_bucket"] = bucket_name
    results["website_url"] = website_url

    # 7. Actualizar agentcore.json
    update_agentcore_json(api_base_url)

    # 8. Actualizar frontend
    update_frontend_config(api_base_url, pool_id, client_id, website_url)

    # 9. Subir frontend
    upload_frontend(bucket_name)

    # Guardar resultados
    results_file = BASE_DIR / "deploy" / "deploy_outputs.json"
    with open(results_file, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, default=str)

    # Resumen final
    print("\n" + "✅ " * 20)
    print("   DEPLOY COMPLETADO")
    print("✅ " * 20)
    print(f"\n   🌐 Frontend URL: {website_url}")
    print(f"   🔗 API Base URL: {api_base_url}")
    print(f"   🤖 Agente ARN:   {ORCHESTRATOR_ARN}")
    print(f"   🔐 Cognito Pool: {pool_id}")
    print(f"   🔑 App Client:   {client_id}")
    print(f"\n   Resultados guardados en: deploy/deploy_outputs.json")
    print(f"\n   PRÓXIMO PASO:")
    print(f"   Ejecutar 'agentcore deploy' para actualizar el OrchestratorAgent")
    print(f"   con la nueva variable API_BASE_URL={api_base_url}")
    print("\n" + "✅ " * 20)

    return results


if __name__ == "__main__":
    main()
