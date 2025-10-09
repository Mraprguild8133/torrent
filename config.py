import os

class Config:
    # Telegram API Configuration
    API_ID = int(os.environ.get("API_ID", 0))
    API_HASH = os.environ.get("API_HASH", "")
    BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
    
    # Google Drive Configuration
    GDRIVE_CREDENTIALS_JSON = os.environ.get("GDRIVE_CREDENTIALS_JSON", "credentials.json")
    GDRIVE_TOKEN_JSON = "token.json"
    SCOPES = ['https://www.googleapis.com/auth/drive']
    
    # Bot Configuration
    OWNER_ID = int(os.environ.get("OWNER_ID", 0))
    DOWNLOAD_DIR = "./downloads/"
    
    # Validation
    def validate(self):
        if not all([self.API_ID, self.API_HASH, self.BOT_TOKEN]):
            raise ValueError("Missing required environment variables: API_ID, API_HASH, BOT_TOKEN")
        if not self.OWNER_ID:
            print("Warning: OWNER_ID not set. Some admin features may not work.")

config = Config()
