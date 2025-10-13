import os

# --- Configuration ---
# Load environment variables
API_ID = os.environ.get("API_ID")
API_HASH = os.environ.get("API_HASH")
BOT_TOKEN = os.environ.get("BOT_TOKEN")
ADMIN_ID = int(os.environ.get("ADMIN_ID", 0))

# Wasabi Configuration
WASABI_ACCESS_KEY = os.environ.get("WASABI_ACCESS_KEY")
WASABI_SECRET_KEY = os.environ.get("WASABI_SECRET_KEY")
WASABI_BUCKET = os.environ.get("WASABI_BUCKET")
WASABI_REGION = os.environ.get("WASABI_REGION")
WASABI_ENDPOINT_URL = f"https://s3.{WASABI_REGION}.wasabisys.com"

# Render configuration
BASE_URL = "RENDER_EXTERNAL_URL"  # Your Render app URL
# Validate required variables
required_vars = {
    "API_ID": API_ID,
    "API_HASH": API_HASH,
    "BOT_TOKEN": BOT_TOKEN,
    "ADMIN_ID": ADMIN_ID,
    "WASABI_ACCESS_KEY": WASABI_ACCESS_KEY,
    "WASABI_SECRET_KEY": WASABI_SECRET_KEY,
    "WASABI_BUCKET": WASABI_BUCKET,
    "WASABI_REGION": WASABI_REGION,
    "BASE_URL": RENDER_EXTERNAL_URL
}

missing_vars = [var for var, value in required_vars.items() if not value]
if missing_vars:
    raise ValueError(f"Missing required environment variables: {', '.join(missing_vars)}")
