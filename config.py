import os
from typing import Optional

class Config:
    """Configuration class for Wasabi Storage Bot"""
    
    # Telegram API Configuration (Required)
    API_ID: int = int(os.getenv("API_ID", 0))
    API_HASH: str = os.getenv("API_HASH", "")
    BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
    
    # Wasabi Storage Configuration
    WASABI_ACCESS_KEY: str = os.getenv("WASABI_ACCESS_KEY", "")
    WASABI_SECRET_KEY: str = os.getenv("WASABI_SECRET_KEY", "")
    WASABI_BUCKET: str = os.getenv("WASABI_BUCKET", "")
    WASABI_REGION: str = os.getenv("WASABI_REGION", "us-east-1")
    WASABI_ENDPOINT_URL: Optional[str] = os.getenv("WASABI_ENDPOINT_URL")
    
    # Bot Administration
    ADMIN_ID: int = int(os.getenv("ADMIN_ID", 0))
    
    # File Handling Configuration
    MAX_FILE_SIZE: int = int(os.getenv("MAX_FILE_SIZE", 4 * 1024 * 1024 * 1024))  # 4GB default
    
    # Download Directory
    DOWNLOAD_DIR: str = os.getenv("DOWNLOAD_DIR", "./downloads")
    
    # Optional: Database URL for persistent user storage
    DATABASE_URL: str = os.getenv("DATABASE_URL", "")
    
    # Optional: Redis URL for caching
    REDIS_URL: str = os.getenv("REDIS_URL", "")
    
    # Optional: Logging level
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
    
    # Optional: Web server configuration for player
    WEB_HOST: str = os.getenv("WEB_HOST", "0.0.0.0")
    WEB_PORT: int = int(os.getenv("WEB_PORT", 8000))
    
    # Optional: Custom player URL (for the second bot implementation)
    RENDER_URL: str = os.getenv("RENDER_URL", "")
    
    # Optional: Rate limiting
    RATE_LIMIT: int = int(os.getenv("RATE_LIMIT", 10))  # requests per minute
    
    @classmethod
    def validate_config(cls) -> bool:
        """Validate that all required configuration is present"""
        required_vars = {
            "API_ID": cls.API_ID,
            "API_HASH": cls.API_HASH,
            "BOT_TOKEN": cls.BOT_TOKEN,
            "ADMIN_ID": cls.ADMIN_ID
        }
        
        # Check required variables
        missing = [var for var, value in required_vars.items() if not value]
        if missing:
            print(f"❌ Missing required environment variables: {', '.join(missing)}")
            return False
        
        # Check Wasabi configuration
        wasabi_vars = {
            "WASABI_ACCESS_KEY": cls.WASABI_ACCESS_KEY,
            "WASABI_SECRET_KEY": cls.WASABI_SECRET_KEY,
            "WASABI_BUCKET": cls.WASABI_BUCKET
        }
        
        wasabi_missing = [var for var, value in wasabi_vars.items() if not value]
        if wasabi_missing:
            print(f"⚠️  Wasabi storage not configured: {', '.join(wasabi_missing)}")
            print("⚠️  File uploads will not work without Wasabi configuration")
        else:
            print("✅ Wasabi configuration found")
        
        print("✅ Configuration validation completed")
        return True
    
    @classmethod
    def print_config_summary(cls):
        """Print a summary of the current configuration (without sensitive data)"""
        summary = f"""
🤖 Bot Configuration Summary:

📱 Telegram:
  • API ID: {cls.API_ID}
  • API Hash: {'*' * 8 if cls.API_HASH else 'MISSING'}
  • Bot Token: {'*' * 8 if cls.BOT_TOKEN else 'MISSING'}
  • Admin ID: {cls.ADMIN_ID}

☁️  Wasabi Storage:
  • Bucket: {cls.WASABI_BUCKET or 'NOT CONFIGURED'}
  • Region: {cls.WASABI_REGION}
  • Access Key: {'✅ SET' if cls.WASABI_ACCESS_KEY else '❌ MISSING'}
  • Secret Key: {'✅ SET' if cls.WASABI_SECRET_KEY else '❌ MISSING'}

📁 File Handling:
  • Max File Size: {cls._humanbytes(cls.MAX_FILE_SIZE)}
  • Download Directory: {cls.DOWNLOAD_DIR}

🌐 Web Server:
  • Host: {cls.WEB_HOST}
  • Port: {cls.WEB_PORT}
  • Player URL: {cls.RENDER_URL or 'NOT SET'}

⚙️  Other:
  • Log Level: {cls.LOG_LEVEL}
  • Rate Limit: {cls.RATE_LIMIT} req/min
"""
        print(summary)
    
    @staticmethod
    def _humanbytes(size: float) -> str:
        """Convert bytes to human readable format (internal use)"""
        if not size:
            return "0 B"
        
        size = int(size)
        power = 1024
        power_labels = {0: 'B', 1: 'KB', 2: 'MB', 3: 'GB', 4: 'TB'}
        
        n = 0
        while size >= power and n < len(power_labels) - 1:
            size /= power
            n += 1
            
        return f"{size:.2f} {power_labels[n]}"


# Create global config instance
config = Config()

# Optional: Auto-validate on import
if __name__ == "__main__":
    config.validate_config()
    config.print_config_summary()
