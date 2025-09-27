import os
import json
from dotenv import load_dotenv
import oracledb

print("Loading environment variables from .env file...")
load_dotenv()
print("✅ Environment variables loaded.")


def test_gcp() -> bool:
    print("\n--- Testing GCP Configuration ---")
    try:
        gcp_service_account_str = None
        gcp_sa_file = os.getenv('GCP_SERVICE_ACCOUNT_JSON_FILE')
        with open(gcp_sa_file, 'r', encoding='utf-8') as f:
                    gcp_service_account_str = f.read()
        if not gcp_service_account_str:
            raise ValueError("GCP_SERVICE_ACCOUNT_JSON_FILE not found.")
        gcp_credentials_info = json.loads(gcp_service_account_str)
        project_id = gcp_credentials_info.get('project_id')
        print(f"✅ GCP Service Account JSON is valid for project: {project_id}")
        return True
    except Exception as e:
        print(f"❌ Error in GCP Configuration: {e}")
        return False


def test_db_connection() -> bool:
    print("\n--- Testing OCI Database Connection ---")
    try:
        db_user = os.getenv('DB_APP_USER')
        db_password = os.getenv('DB_APP_USER_PASSWORD')
        wallet_password = os.getenv('DB_WALLET_PASSWORD')
        db_connection_string = os.getenv('DB_CONNECTION_STRING')


        default_wallet_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'wallet')
        wallet_location = os.getenv('DB_WALLET_DIR', default_wallet_dir)
        print(f"Wallet directory resolved to: {wallet_location}")
        if not os.path.isdir(wallet_location):
            raise ValueError(f"Wallet directory not found at: {wallet_location}")

        required = ['DB_APP_USER', 'DB_APP_USER_PASSWORD', 'DB_WALLET_PASSWORD']
        if db_connection_string:
            print("Using DB_CONNECTION_STRING from environment.")
        else:
            print("DB_CONNECTION_STRING not provided; expecting DB_HOST/DB_PORT/DB_SERVICE_NAME.")
            required.extend(['DB_HOST', 'DB_PORT', 'DB_SERVICE_NAME'])
        missing = [k for k in required if not os.getenv(k)]
        if missing:
            raise ValueError(f"Missing required DB env vars: {', '.join(missing)}")

        print("Attempting to connect to the Oracle Database...")
        if not db_connection_string:
            db_host = os.getenv('DB_HOST')
            db_port = os.getenv('DB_PORT')
            db_service_name = os.getenv('DB_SERVICE_NAME')
            dsn = f"{db_host}:{db_port}/{db_service_name}"
        else:
            dsn = db_connection_string

        with oracledb.connect(
            user=db_user,
            password=db_password,
            dsn=dsn,
            config_dir=wallet_location,
            wallet_password=wallet_password
        ) as connection:
            print("\n" + "="*60)
            print("✅✅✅ SUCCESSFULLY CONNECTED TO ORACLE AUTONOMOUS DATABASE! ✅✅✅")
            print(f"     - DB Version: {connection.version}")
            print("="*60 + "\n")
            return True
    except Exception as e:
        print(f"❌ Failed to connect to Oracle Database: {e}")
        return False


def test_gemini_api() -> bool:
    print("--- Testing Gemini API Key ---")
    gemini_api_key = os.getenv('GEMINI_API_KEY')
    if gemini_api_key:
        print("✅ Gemini API Key is loaded.")
        return True
    print("❌ Gemini API Key not found.")
    return False


if __name__ == "__main__":
    test_gcp()
    test_db_connection()
    test_gemini_api()