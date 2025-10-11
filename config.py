import os

class Config:
    # Telegram API credentials
    API_ID = int(os.environ.get("API_ID", 0))
    API_HASH = os.environ.get("API_HASH", "")
    BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
    
    # Wasabi credentials
    WASABI_ACCESS_KEY = os.environ.get("WASABI_ACCESS_KEY", "")
    WASABI_SECRET_KEY = os.environ.get("WASABI_SECRET_KEY", "")
    WASABI_BUCKET = os.environ.get("WASABI_BUCKET", "")
    WASABI_REGION = os.environ.get("WASABI_REGION", "us-east-1")
    
    @property
    def WASABI_ENDPOINT_URL(self):
        return f'https://s3.{self.WASABI_REGION}.wasabisys.com'
    
    # Bot settings
    MAX_FILE_SIZE = 5 * 1024 * 1024 * 1024  # 5 GB

config = Config()
