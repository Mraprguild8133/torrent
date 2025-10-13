import os
import secrets
from dataclasses import dataclass
from typing import Optional
from dotenv import load_dotenv

load_dotenv()

@dataclass
class TelegramConfig:
    API_ID: str = os.getenv("API_ID")
    API_HASH: str = os.getenv("API_HASH")
    BOT_TOKEN: str = os.getenv("BOT_TOKEN")

@dataclass
class WasabiConfig:
    ACCESS_KEY: str = os.getenv("WASABI_ACCESS_KEY")
    SECRET_KEY: str = os.getenv("WASABI_SECRET_KEY")
    BUCKET: str = os.getenv("WASABI_BUCKET")
    REGION: str = os.getenv("WASABI_REGION", "us-east-1")
    ENDPOINT_URL: Optional[str] = os.getenv("WASABI_ENDPOINT_URL")

@dataclass
class ServerConfig:
    RENDER_URL: str = os.getenv("RENDER_URL", "http://localhost:8000")
    SECRET_KEY: str = os.getenv("SECRET_KEY", secrets.token_hex(32))
    HOST: str = os.getenv("HOST", "0.0.0.0")
    PORT: int = int(os.getenv("PORT", "8000"))

@dataclass
class LimitsConfig:
    MAX_FILE_SIZE: int = 2000 * 1024 * 1024  # 2GB
    MAX_USER_STORAGE: int = 10 * 1024 * 1024 * 1024  # 10GB per user
    RATE_LIMIT_REQUESTS: int = 10  # requests per minute
    RATE_LIMIT_PERIOD: int = 60   # seconds
    PRESIGNED_URL_EXPIRY: int = 86400  # 24 hours

@dataclass
class RedisConfig:
    URL: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    ENABLED: bool = os.getenv("REDIS_ENABLED", "true").lower() == "true"

@dataclass
class MonitoringConfig:
    ENABLE_METRICS: bool = os.getenv("ENABLE_METRICS", "true").lower() == "true"
    ENABLE_LOGGING: bool = os.getenv("ENABLE_LOGGING", "true").lower() == "true"
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

@dataclass
class EncryptionConfig:
    KEY: str = os.getenv("ENCRYPTION_KEY", Fernet.generate_key())
    ENABLED: bool = os.getenv("ENCRYPTION_ENABLED", "true").lower() == "true"

class Config:
    """Main configuration class"""
    
    telegram = TelegramConfig()
    wasabi = WasabiConfig()
    server = ServerConfig()
    limits = LimitsConfig()
    redis = RedisConfig()
    monitoring = MonitoringConfig()
    encryption = EncryptionConfig()
    
    # Media extensions
    MEDIA_EXTENSIONS = {
        'video': ['.mp4', '.mov', '.avi', '.mkv', '.webm', '.m4v', '.flv', '.3gp'],
        'audio': ['.mp3', '.m4a', '.ogg', '.wav', '.flac', '.aac', '.wma'],
        'image': ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', '.tiff', '.svg'],
        'document': ['.pdf', '.txt', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx']
    }
    
    @classmethod
    def validate(cls):
        """Validate required configuration"""
        missing_vars = []
        
        # Check Telegram config
        if not cls.telegram.API_ID:
            missing_vars.append("API_ID")
        if not cls.telegram.API_HASH:
            missing_vars.append("API_HASH")
        if not cls.telegram.BOT_TOKEN:
            missing_vars.append("BOT_TOKEN")
        
        # Check Wasabi config
        if not cls.wasabi.ACCESS_KEY:
            missing_vars.append("WASABI_ACCESS_KEY")
        if not cls.wasabi.SECRET_KEY:
            missing_vars.append("WASABI_SECRET_KEY")
        if not cls.wasabi.BUCKET:
            missing_vars.append("WASABI_BUCKET")
        
        if missing_vars:
            raise ValueError(f"Missing required environment variables: {', '.join(missing_vars)}")
        
        # Set Wasabi endpoint URL if not provided
        if not cls.wasabi.ENDPOINT_URL:
            cls.wasabi.ENDPOINT_URL = f'https://s3.{cls.wasabi.REGION}.wasabisys.com'
        
        return True

# Validate configuration on import
Config.validate()

# Export config instance
config = Config()
