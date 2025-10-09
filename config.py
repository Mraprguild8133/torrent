import os
import json

class Config:
    # Telegram API Configuration
    API_ID = int(os.environ.get("API_ID", 0))
    API_HASH = os.environ.get("API_HASH", "")
    BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
    
    # Google Drive Configuration - Using Client ID/Secret directly
    GDRIVE_CLIENT_ID = os.environ.get("GDRIVE_CLIENT_ID", "")
    GDRIVE_CLIENT_SECRET = os.environ.get("GDRIVE_CLIENT_SECRET", "")
    GDRIVE_TOKEN = os.environ.get("GDRIVE_TOKEN", "")  # Optional: Store token in env var
    SCOPES = ['https://www.googleapis.com/auth/drive']
    
    # Bot Configuration
    OWNER_ID = int(os.environ.get("OWNER_ID", 0))
    DOWNLOAD_DIR = "./downloads/"
    
    # Validation
    def validate(self):
        if not all([self.API_ID, self.API_HASH, self.BOT_TOKEN]):
            raise ValueError("Missing required environment variables: API_ID, API_HASH, BOT_TOKEN")
        if not self.GDRIVE_CLIENT_ID or not self.GDRIVE_CLIENT_SECRET:
            raise ValueError("Missing Google Drive Client ID or Client Secret")
        if not self.OWNER_ID:
            print("Warning: OWNER_ID not set. Some admin features may not work.")

config = Config()
