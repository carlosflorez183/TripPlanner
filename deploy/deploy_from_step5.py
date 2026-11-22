"""Retoma el deploy desde el paso 5 (API Gateway), usando los recursos ya creados."""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from deploy_all import (
    deploy_api_gateway, deploy_s3_frontend, update_agentcore_json,
    update_frontend_config, upload_frontend, log, ok, info,
    REGION, ACCOUNT, STAGE, PROJECT, ORCHESTRATOR_ARN, ORCHESTRATOR_RUNTIME_ID,
    session
)
import json, pathlib

BASE_DIR = pathlib.Path(__file__).parent.parent

# Valores de los pasos 1-4 ya ejecutados
POOL_ID = "us-east-1_b1vipfKMR"
CLIENT_ID = "4mut3b36os8ardr8ejolh7l3kg"

FUNCTION_NAMES = {
    "create_trip":        "TripPlanner-CreateTrip-prod",
    "get_trips":          "TripPlanner-GetTrips-prod",
    "update_trip":        "TripPlanner-UpdateTrip-prod",
    "create_itinerary":   "TripPlanner-CreateItinerary-prod",
    "update_itinerary":   "TripPlanner-UpdateItinerary-prod",
    "create_reservation": "TripPlanner-CreateReservation-prod",
    "get_reservations":   "TripPlanner-GetReservations-prod",
    "update_reservation": "TripPlanner-UpdateReservation-prod",
}

TABLE_ARNS = {
    f"{PROJECT}-Users-{STAGE}":        f"arn:aws:dynamodb:{REGION}:{ACCOUNT}:table/{PROJECT}-Users-{STAGE}",
    f"{PROJECT}-Trips-{STAGE}":        f"arn:aws:dynamodb:{REGION}:{ACCOUNT}:table/{PROJECT}-Trips-{STAGE}",
    f"{PROJECT}-Itineraries-{STAGE}":  f"arn:aws:dynamodb:{REGION}:{ACCOUNT}:table/{PROJECT}-Itineraries-{STAGE}",
    f"{PROJECT}-Reservations-{STAGE}": f"arn:aws:dynamodb:{REGION}:{ACCOUNT}:table/{PROJECT}-Reservations-{STAGE}",
}

def main():
    print("\n" + "🔁 " * 20)
    print("   TripPlanner AI — RETOMANDO DESDE STEP 5")
    print("🔁 " * 20)

    results = {
        "cognito_pool_id": POOL_ID,
        "cognito_client_id": CLIENT_ID,
        "functions": FUNCTION_NAMES,
    }

    # 5. API Gateway
    api_id, api_base_url = deploy_api_gateway(FUNCTION_NAMES, POOL_ID, CLIENT_ID)
    results["api_id"] = api_id
    results["api_base_url"] = api_base_url

    # 6. S3
    bucket_name, website_url = deploy_s3_frontend()
    results["s3_bucket"] = bucket_name
    results["website_url"] = website_url

    # 7. Actualizar agentcore.json
    update_agentcore_json(api_base_url)

    # 8. Actualizar frontend
    update_frontend_config(api_base_url, POOL_ID, CLIENT_ID, website_url)

    # 9. Subir frontend
    upload_frontend(bucket_name)

    # Guardar resultados
    results_file = BASE_DIR / "deploy" / "deploy_outputs.json"
    with open(results_file, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, default=str)

    print("\n" + "✅ " * 20)
    print("   DEPLOY COMPLETADO")
    print("✅ " * 20)
    print(f"\n   🌐 Frontend URL: {website_url}")
    print(f"   🔗 API Base URL: {api_base_url}")
    print(f"   🤖 Agente:       {ORCHESTRATOR_RUNTIME_ID}")
    print(f"   🔐 Cognito Pool: {POOL_ID}")
    print(f"   🔑 App Client:   {CLIENT_ID}")
    print(f"\n   PRÓXIMO PASO:")
    print(f"   cd a2a-trip-planner/TripPlanner && agentcore deploy")
    print("\n" + "✅ " * 20)

if __name__ == "__main__":
    main()
