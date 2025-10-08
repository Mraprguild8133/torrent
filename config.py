import os

# Bot Configuration
API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
API_KEY = os.environ.get("API_KEY", "")  # For new27.gdtot.dad
GDTOT_EMAIL = os.environ.get("GDTOT_EMAIL", "")  # Your GDTOT account email

# GDTOT API Configuration
GDTOT_API_URL = "https://new27.gdtot.dad/api/upload/link"
GDTOT_DOMAIN = "https://new27.gdtot.dad"

# Bot Settings
MAX_FILE_SIZE = 4 * 1024 * 1024 * 1024  # 4GB in bytes
DOWNLOAD_PATH = "./downloads"

# Validate required configuration
def validate_config():
    required_vars = {
        "API_ID": API_ID,
        "API_HASH": API_HASH,
        "BOT_TOKEN": BOT_TOKEN,
        "API_KEY": API_KEY,
        "GDTOT_EMAIL": GDTOT_EMAIL
    }
    
    missing = [var for var, value in required_vars.items() if not value]
    if missing:
        raise ValueError(f"❌ Missing required environment variables: {', '.join(missing)}")
    
    return True
