import os

class Config:
    """Configuration class for environment variables"""
    
    # Telegram API
    API_ID = os.environ.get("API_ID")
    API_HASH = os.environ.get("API_HASH")
    BOT_TOKEN = os.environ.get("BOT_TOKEN")
    
    # Wasabi Configuration
    WASABI_ACCESS_KEY = os.environ.get("WASABI_ACCESS_KEY")
    WASABI_SECRET_KEY = os.environ.get("WASABI_SECRET_KEY")
    WASABI_BUCKET = os.environ.get("WASABI_BUCKET")
    WASABI_REGION = os.environ.get("WASABI_REGION")
    
    # Admin Configuration
    ADMIN_ID = int(os.environ.get("ADMIN_ID", 0))
    
    # Web Server Configuration
    WEB_SERVER_URL = os.environ.get("WEB_SERVER_URL", "http://localhost:8000")

# GPLinks.in Configuration
GPLINKS_API_KEY = "c1332c0b286628ba047359efde6a5bdac1509655"  # Get from https://gplinks.in/member/tools/api
AUTO_SHORTEN = True  # Set to False to disable auto-shortening

# Create config instance
config = Config()
