import os
import asyncio
from typing import Optional, Dict, Any, List
from dataclasses import dataclass
from dotenv import load_dotenv
from enum import Enum
import logging

load_dotenv()

class FileType(Enum):
    DOCUMENT = "document"
    VIDEO = "video"
    AUDIO = "audio"
    PHOTO = "photo"
    OTHER = "other"

@dataclass
class CacheConfig:
    """Redis-like memory cache configuration"""
    MAX_SIZE: int = 10000
    TTL: int = 3600
    CLEANUP_INTERVAL: int = 300
    MAX_MEMORY_MB: int = 512

@dataclass
class PerformanceConfig:
    """High-performance tuning"""
    MAX_WORKERS: int = 500
    MAX_CONCURRENT_TRANSMISSIONS: int = 50
    CHUNK_SIZE: int = 256 * 1024 * 1024  # 256MB chunks
    BUFFER_SIZE: int = 65536  # 64KB buffer
    CONNECTION_TIMEOUT: int = 30
    READ_TIMEOUT: int = 60
    STREAM_BUFFER: int = 8192
    MAX_CONNECTIONS: int = 100

@dataclass
class SecurityConfig:
    """Security settings"""
    MAX_FILE_SIZE: int = 4 * 1024 * 1024 * 1024  # 4GB
    RATE_LIMIT_PER_USER: int = 10  # requests per minute
    RATE_LIMIT_PER_FILE: int = 5   # downloads per minute
    ALLOWED_EXTENSIONS: set = None
    BLOCKED_EXTENSIONS: set = None
    
    def __post_init__(self):
        if self.ALLOWED_EXTENSIONS is None:
            self.ALLOWED_EXTENSIONS = {
                'txt', 'pdf', 'doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx',
                'jpg', 'jpeg', 'png', 'gif', 'bmp', 'webp', 'svg', 'ico',
                'mp4', 'avi', 'mov', 'mkv', 'webm', 'flv', 'm4v', '3gp',
                'mp3', 'wav', 'ogg', 'flac', 'm4a', 'aac', 'wma',
                'zip', 'rar', '7z', 'tar', 'gz', 'bz2', 'xz',
                'py', 'js', 'html', 'css', 'json', 'xml', 'csv'
            }
        if self.BLOCKED_EXTENSIONS is None:
            self.BLOCKED_EXTENSIONS = {'exe', 'bat', 'cmd', 'sh', 'bin', 'msi', 'dll'}

@dataclass
class WasabiConfig:
    """Wasabi S3 configuration"""
    ACCESS_KEY: str = os.getenv('WASABI_ACCESS_KEY', '')
    SECRET_KEY: str = os.getenv('WASABI_SECRET_KEY', '')
    BUCKET: str = os.getenv('WASABI_BUCKET', '')
    REGION: str = os.getenv('WASABI_REGION', 'us-east-1')
    ENDPOINT: str = f"https://s3.{os.getenv('WASABI_REGION', 'us-east-1')}.wasabisys.com"
    MULTIPART_THRESHOLD: int = 128 * 1024 * 1024  # 128MB
    MULTIPART_CHUNKSIZE: int = 64 * 1024 * 1024   # 64MB

class AdvancedConfig:
    """World-class configuration management"""
    
    # Core Configuration
    API_ID: int = int(os.getenv('API_ID', 0))
    API_HASH: str = os.getenv('API_HASH', '')
    BOT_TOKEN: str = os.getenv('BOT_TOKEN', '')
    
    # Service Configurations
    CACHE = CacheConfig()
    PERFORMANCE = PerformanceConfig()
    SECURITY = SecurityConfig()
    WASABI = WasabiConfig()
    
    # Path Configuration
    DOWNLOAD_PATH: str = "downloads"
    TEMP_PATH: str = "temp"
    LOG_PATH: str = "logs"
    
    # Feature Flags
    ENABLE_STREAMING: bool = True
    ENABLE_COMPRESSION: bool = True
    ENABLE_ENCRYPTION: bool = False
    ENABLE_CDN: bool = False
    ENABLE_ANALYTICS: bool = True
    
    @classmethod
    def validate(cls) -> bool:
        """Comprehensive configuration validation"""
        required = {
            'API_ID': cls.API_ID,
            'API_HASH': cls.API_HASH,
            'BOT_TOKEN': cls.BOT_TOKEN,
            'WASABI_ACCESS_KEY': cls.WASABI.ACCESS_KEY,
            'WASABI_SECRET_KEY': cls.WASABI.SECRET_KEY,
            'WASABI_BUCKET': cls.WASABI.BUCKET
        }
        
        missing = [k for k, v in required.items() if not v]
        if missing:
            logging.error(f"❌ Missing configuration: {missing}")
            return False
        
        # Create necessary directories
        for path in [cls.DOWNLOAD_PATH, cls.TEMP_PATH, cls.LOG_PATH]:
            os.makedirs(path, exist_ok=True)
        
        return True

# Global configuration instance
config = AdvancedConfig()
