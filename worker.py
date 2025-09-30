import os
import json
import logging
from dotenv import load_dotenv
import oracledb



logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("secretary-agent")

print("Loading environment variables from .env file...")
load_dotenv()
print("✅ Environment variables loaded.")



# def test_db_connection() -> bool:
#     print("\n--- Testing OCI Database Connection ---")
#     try:
#         db_dns = os.getenv('DB_DNS')
#         db_user = os.getenv('DB_APP_USER')
#         db_password = os.getenv('DB_APP_USER_PASSWORD')
#         wallet_password = os.getenv('DB_WALLET_PASSWORD')

#         default_wallet_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'wallet')
#         print(f"Default wallet directory: {default_wallet_dir}")
#         wallet_location = os.getenv('DB_WALLET_DIR', default_wallet_dir)
#         print(f"Wallet directory resolved to: {wallet_location}")
#         if not os.path.isdir(wallet_location):
#             raise ValueError(f"Wallet directory not found at: {wallet_location}")

#         missing = [k for k in ['DB_APP_USER', 'DB_APP_USER_PASSWORD', 'DB_WALLET_PASSWORD'] if not os.getenv(k)]
#         if missing:
#             raise ValueError(f"Missing required DB env vars: {', '.join(missing)}")

#         print("Attempting to connect to the Oracle Database...")
#         with oracledb.connect(
#             config_dir=wallet_location,
#             user=db_user,
#             password=db_password,
#             dsn=db_dns,
#             wallet_password=wallet_password
#         ) as connection:
#             print("\n" + "="*60)
#             print("✅✅✅ SUCCESSFULLY CONNECTED TO ORACLE AUTONOMOUS DATABASE! ✅✅✅")
#             print(f"     - DB Version: {connection.version}")
#             print("="*60 + "\n")
#             return True
#     except Exception as e:
#         print(f"❌ Failed to connect to Oracle Database: {e}")
#         return False
def test_db_connection() -> bool:
    print("\n--- Testing OCI Database Connection ---")
    try:
        db_user = os.getenv('DB_APP_USER')
        db_password = os.getenv('DB_APP_USER_PASSWORD')
        wallet_password = os.getenv('DB_WALLET_PASSWORD')

        default_wallet_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'wallet')
        print(f"Default wallet directory: {default_wallet_dir}")
        wallet_location = os.getenv('DB_WALLET_DIR', default_wallet_dir)
        print(f"Wallet directory resolved to: {wallet_location}")
        if not os.path.isdir(wallet_location):
            raise ValueError(f"Wallet directory not found at: {wallet_location}")

        # Ensure required env vars are present
        missing = [k for k in ['DB_APP_USER', 'DB_APP_USER_PASSWORD'] if not os.getenv(k)]
        if missing:
            raise ValueError(f"Missing required DB env vars: {', '.join(missing)}")

        # Prefer a DSN (Easy Connect or full DESCRIPTION) or a TNS alias from tnsnames.ora
        db_dns = os.getenv('DB_DNS')
        db_tns_alias = os.getenv('DB_TNS_ALIAS')
        if not db_dns and not db_tns_alias:
            raise ValueError("Provide either DB_DNS or DB_TNS_ALIAS to use wallet-based Thick connection.")

        # Make wallet config visible to Thick mode
        os.environ['TNS_ADMIN'] = wallet_location
        # Initialize Thick mode and point to the wallet config directory so sqlnet.ora and tnsnames.ora are used
        oracledb.init_oracle_client(config_dir=wallet_location)

        dsn = db_dns or db_tns_alias
        print("Attempting to connect to the Oracle Database using wallet and DSN/TNS alias...")
        with oracledb.connect(
            user=db_user,
            password=db_password,
            dsn=dsn,
            config_dir=wallet_location,
            wallet_password=wallet_password,
        ) as connection:
            print("\n" + "="*60)
            print("✅✅✅ SUCCESSFULLY CONNECTED TO ORACLE AUTONOMOUS DATABASE! ✅✅✅")
            print(f"     - DB Version: {connection.version}")
            print("="*60 + "\n")
            return True
    except Exception:
        logging.exception("❌ Failed to connect to Oracle Database")
        return False

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
    except Exception:
        logging.exception("❌ Error in GCP Configuration")
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