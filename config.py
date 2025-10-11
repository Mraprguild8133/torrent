import os
from dataclasses import dataclass
from typing import Optional

@dataclass
class Config:
    API_ID: str
    API_HASH: str
    BOT_TOKEN: str
    WASABI_ACCESS_KEY: str
    WASABI_SECRET_KEY: str
    WASABI_BUCKET: str
    WASABI_REGION: str
    MAX_FILE_SIZE: int = 4 * 1024 * 1024 * 1024  # 4GB
    DOWNLOAD_PATH: str = "downloads"
    
    @classmethod
    def from_env(cls) -> 'Config':
        required_vars = [
            "API_ID", "API_HASH", "BOT_TOKEN", 
            "WASABI_ACCESS_KEY", "WASABI_SECRET_KEY", 
            "WASABI_BUCKET", "WASABI_REGION"
        ]
        
        missing = [var for var in required_vars if not os.environ.get(var)]
        if missing:
            raise ValueError(f"Missing environment variables: {', '.join(missing)}")
            
        return cls(**{var: os.environ[var] for var in required_vars})

# Create global config instance
config = Config.from_env()
