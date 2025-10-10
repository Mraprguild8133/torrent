import os
from typing import Optional
from dotenv import load_dotenv

load_dotenv()

class Config:
    """Configuration class for Telegram Wasabi Bot"""
    
    # Telegram API Configuration
    API_ID: int = int(os.getenv('API_ID', 0))
    API_HASH: str = os.getenv('API_HASH', '')
    BOT_TOKEN: str = os.getenv('BOT_TOKEN', '')
    
    # Wasabi Storage Configuration
    WASABI_ACCESS_KEY: str = os.getenv('WASABI_ACCESS_KEY', '')
    WASABI_SECRET_KEY: str = os.getenv('WASABI_SECRET_KEY', '')
    WASABI_BUCKET: str = os.getenv('WASABI_BUCKET', '')
    WASABI_REGION: str = os.getenv('WASABI_REGION', 'us-east-1')
    
    # Bot Configuration
    MAX_FILE_SIZE: int = 54 * 1024 * 1024 * 1024  # 54GB
    DOWNLOAD_URL_EXPIRY: int = 604800  # 7 days in seconds
    TEMP_DIR: str = "temp_downloads"
    
    @property
    def wasabi_endpoint(self) -> str:
        """Generate Wasabi endpoint URL"""
        return f'https://s3.{self.WASABI_REGION}.wasabisys.com'
    
    def validate(self) -> bool:
        """Validate that all required configuration is present"""
        required_vars = {
            'API_ID': self.API_ID,
            'API_HASH': self.API_HASH,
            'BOT_TOKEN': self.BOT_TOKEN,
            'WASABI_ACCESS_KEY': self.WASABI_ACCESS_KEY,
            'WASABI_SECRET_KEY': self.WASABI_SECRET_KEY,
            'WASABI_BUCKET': self.WASABI_BUCKET
        }
        
        missing_vars = [var for var, value in required_vars.items() if not value]
        if missing_vars:
            raise ValueError(f"Missing required environment variables: {', '.join(missing_vars)}")
        
        return True

# Global config instance
config = Config()
