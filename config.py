import os
from dataclasses import dataclass

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
    def from_env(cls):
        required_vars = [
            "API_ID", "API_HASH", "BOT_TOKEN", 
            "WASABI_ACCESS_KEY", "WASABI_SECRET_KEY", 
            "WASABI_BUCKET", "WASABI_REGION"
        ]
        
        missing = [var for var in required_vars if not os.environ.get(var)]
        if missing:
            raise ValueError(f"Missing environment variables: {', '.join(missing)}")
            
        return cls(
            API_ID=os.environ["API_ID"],
            API_HASH=os.environ["API_HASH"],
            BOT_TOKEN=os.environ["BOT_TOKEN"],
            WASABI_ACCESS_KEY=os.environ["WASABI_ACCESS_KEY"],
            WASABI_SECRET_KEY=os.environ["WASABI_SECRET_KEY"],
            WASABI_BUCKET=os.environ["WASABI_BUCKET"],
            WASABI_REGION=os.environ["WASABI_REGION"]
        )

# Create global config instance
config = Config.from_env()
