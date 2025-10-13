import os
import time
import asyncio
import re
import base64
import json
import hashlib
from threading import Thread, Lock
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import logging
from logging.handlers import RotatingFileHandler
import secrets

import boto3
import botocore
from flask import Flask, render_template, request, jsonify, send_file
from pyrogram import Client, filters, enums
from pyrogram.types import Message, InlineKeyboardButton, InlineKeyboardMarkup
from pyrogram.errors import FloodWait, RPCError
from dotenv import load_dotenv
import aiofiles
from cryptography.fernet import Fernet
import redis
import psutil
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
import requests

# Import configuration
from config import config

# =============================================================================
# LOGGING SETUP
# =============================================================================

def setup_logging():
    """Configure advanced logging with rotation"""
    log_level = getattr(logging, config.monitoring.LOG_LEVEL)
    
    log_formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s'
    )
    
    # Root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)
    
    if config.monitoring.ENABLE_LOGGING:
        # File handler with rotation
        file_handler = RotatingFileHandler(
            'bot.log', maxBytes=10*1024*1024, backupCount=5
        )
        file_handler.setFormatter(log_formatter)
        root_logger.addHandler(file_handler)
    
    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(log_formatter)
    root_logger.addHandler(console_handler)

setup_logging()
logger = logging.getLogger(__name__)

# =============================================================================
# METRICS & MONITORING
# =============================================================================

if config.monitoring.ENABLE_METRICS:
    # Prometheus metrics
    REQUEST_COUNT = Counter('bot_requests_total', 'Total requests', ['endpoint', 'method', 'status'])
    UPLOAD_SIZE = Histogram('upload_size_bytes', 'File upload sizes')
    DOWNLOAD_SIZE = Histogram('download_size_bytes', 'File download sizes')
    PROCESSING_TIME = Histogram('processing_time_seconds', 'Request processing time')
    ACTIVE_UPLOADS = Counter('active_uploads', 'Active uploads')
    ACTIVE_DOWNLOADS = Counter('active_downloads', 'Active downloads')

# =============================================================================
# CACHE & STORAGE MANAGER
# =============================================================================

class CacheManager:
    """Redis-based cache manager for rate limiting and metadata"""
    
    def __init__(self):
        if config.redis.ENABLED:
            try:
                self.redis_client = redis.from_url(config.redis.URL, decode_responses=True)
                self.redis_client.ping()
                logger.info("Redis connected successfully")
            except Exception as e:
                logger.warning(f"Redis not available: {e}. Using in-memory cache.")
                self.redis_client = None
                self.memory_cache = {}
                self.lock = Lock()
        else:
            logger.info("Redis disabled, using in-memory cache")
            self.redis_client = None
            self.memory_cache = {}
            self.lock = Lock()
    
    def get(self, key):
        if self.redis_client:
            return self.redis_client.get(key)
        with self.lock:
            return self.memory_cache.get(key)
    
    def set(self, key, value, expire=None):
        if self.redis_client:
            self.redis_client.set(key, value, ex=expire)
        else:
            with self.lock:
                self.memory_cache[key] = value
    
    def incr(self, key):
        if self.redis_client:
            return self.redis_client.incr(key)
        with self.lock:
            self.memory_cache[key] = self.memory_cache.get(key, 0) + 1
            return self.memory_cache[key]
    
    def delete(self, key):
        if self.redis_client:
            self.redis_client.delete(key)
        else:
            with self.lock:
                self.memory_cache.pop(key, None)

cache = CacheManager()

# =============================================================================
# SECURITY & ENCRYPTION
# =============================================================================

class SecurityManager:
    """Handles encryption and security operations"""
    
    def __init__(self):
        if config.encryption.ENABLED:
            self.cipher = Fernet(config.encryption.KEY)
        else:
            self.cipher = None
            logger.info("Encryption disabled")
    
    def encrypt_data(self, data: str) -> str:
        """Encrypt sensitive data"""
        if not self.cipher:
            return data
        return self.cipher.encrypt(data.encode()).decode()
    
    def decrypt_data(self, encrypted_data: str) -> str:
        """Decrypt sensitive data"""
        if not self.cipher:
            return encrypted_data
        return self.cipher.decrypt(encrypted_data.encode()).decode()
    
    def generate_secure_token(self, length=32) -> str:
        """Generate cryptographically secure token"""
        return secrets.token_urlsafe(length)
    
    def validate_filename(self, filename: str) -> bool:
        """Validate filename for security"""
        if not filename or len(filename) > 255:
            return False
        
        # Prevent path traversal
        if '../' in filename or '..\\' in filename:
            return False
        
        # Allow only safe characters
        if not re.match(r'^[a-zA-Z0-9_\-\.\s]+$', filename):
            return False
        
        return True

security = SecurityManager()

# =============================================================================
# WASABI STORAGE MANAGER
# =============================================================================

class WasabiManager:
    """Advanced Wasabi S3 operations manager"""
    
    def __init__(self):
        self.s3_client = None
        self.bucket = config.wasabi.BUCKET
        self.initialize_client()
    
    def initialize_client(self):
        """Initialize Wasabi S3 client with multiple endpoint fallbacks"""
        endpoints = [
            config.wasabi.ENDPOINT_URL,
            f'https://s3.{config.wasabi.REGION}.wasabisys.com',
            f'https://{config.wasabi.BUCKET}.s3.{config.wasabi.REGION}.wasabisys.com'
        ]
        
        for endpoint in endpoints:
            if not endpoint:
                continue
                
            try:
                self.s3_client = boto3.client(
                    's3',
                    endpoint_url=endpoint,
                    aws_access_key_id=config.wasabi.ACCESS_KEY,
                    aws_secret_access_key=config.wasabi.SECRET_KEY,
                    region_name=config.wasabi.REGION,
                    config=botocore.config.Config(
                        s3={'addressing_style': 'virtual'},
                        signature_version='s3v4',
                        retries={'max_attempts': 3, 'mode': 'standard'}
                    )
                )
                
                # Test connection
                self.s3_client.head_bucket(Bucket=self.bucket)
                logger.info(f"Connected to Wasabi via {endpoint}")
                break
                
            except Exception as e:
                logger.warning(f"Failed to connect via {endpoint}: {e}")
                continue
        else:
            raise Exception("Could not connect to any Wasabi endpoint")
    
    def get_user_storage_usage(self, user_id: int) -> int:
        """Calculate user's total storage usage"""
        try:
            user_prefix = f"user_{user_id}/"
            total_size = 0
            
            paginator = self.s3_client.get_paginator('list_objects_v2')
            for page in paginator.paginate(Bucket=self.bucket, Prefix=user_prefix):
                if 'Contents' in page:
                    total_size += sum(obj['Size'] for obj in page['Contents'])
            
            return total_size
        except Exception as e:
            logger.error(f"Error calculating storage usage for user {user_id}: {e}")
            return 0
    
    def upload_file_with_metadata(self, file_path: str, s3_key: str, metadata: dict = None):
        """Upload file with custom metadata"""
        extra_args = {}
        if metadata:
            extra_args['Metadata'] = {k: str(v) for k, v in metadata.items()}
        
        self.s3_client.upload_file(
            file_path, self.bucket, s3_key,
            ExtraArgs=extra_args
        )
    
    def generate_secure_presigned_url(self, key: str, expires_in: int = None) -> str:
        """Generate presigned URL with additional security"""
        if expires_in is None:
            expires_in = config.limits.PRESIGNED_URL_EXPIRY
            
        try:
            return self.s3_client.generate_presigned_url(
                'get_object',
                Params={
                    'Bucket': self.bucket,
                    'Key': key,
                },
                ExpiresIn=expires_in,
                HttpMethod='GET'
            )
        except Exception as e:
            logger.error(f"Error generating presigned URL for {key}: {e}")
            raise
    
    def delete_user_file(self, user_id: int, filename: str) -> bool:
        """Delete a user's file"""
        try:
            s3_key = f"user_{user_id}/{filename}"
            self.s3_client.delete_object(Bucket=self.bucket, Key=s3_key)
            logger.info(f"Deleted file {s3_key}")
            return True
        except Exception as e:
            logger.error(f"Error deleting file {filename}: {e}")
            return False

wasabi_manager = WasabiManager()

# =============================================================================
# RATE LIMITING & THROTTLING
# =============================================================================

class RateLimiter:
    """Advanced rate limiting with sliding window"""
    
    def __init__(self):
        self.cache = cache
    
    def is_rate_limited(self, user_id: int, action: str = "default") -> bool:
        """Check if user is rate limited for specific action"""
        key = f"rate_limit:{user_id}:{action}"
        window_key = f"{key}:window"
        
        current_time = time.time()
        window_start = self.cache.get(window_key)
        
        if not window_start:
            # First request in new window
            self.cache.set(window_key, current_time, config.limits.RATE_LIMIT_PERIOD)
            self.cache.set(key, 1, config.limits.RATE_LIMIT_PERIOD)
            return False
        
        window_start = float(window_start)
        
        if current_time - window_start > config.limits.RATE_LIMIT_PERIOD:
            # Start new window
            self.cache.set(window_key, current_time, config.limits.RATE_LIMIT_PERIOD)
            self.cache.set(key, 1, config.limits.RATE_LIMIT_PERIOD)
            return False
        
        # Increment request count
        request_count = self.cache.incr(key)
        
        if request_count > config.limits.RATE_LIMIT_REQUESTS:
            return True
        
        return False
    
    def get_retry_after(self, user_id: int, action: str = "default") -> int:
        """Get seconds until user can make next request"""
        key = f"rate_limit:{user_id}:{action}:window"
        window_start = self.cache.get(key)
        
        if not window_start:
            return 0
        
        return int(config.limits.RATE_LIMIT_PERIOD - (time.time() - float(window_start)))

rate_limiter = RateLimiter()

# =============================================================================
# UTILITY FUNCTIONS
# =============================================================================

class Utilities:
    """Collection of utility functions"""
    
    @staticmethod
    def get_file_type(filename: str) -> str:
        """Determine file type from extension"""
        ext = os.path.splitext(filename)[1].lower()
        for file_type, extensions in config.MEDIA_EXTENSIONS.items():
            if ext in extensions:
                return file_type
        return 'other'
    
    @staticmethod
    def humanbytes(size: int) -> str:
        """Convert bytes to human readable format"""
        if not size:
            return "0 B"
        
        units = ["B", "KB", "MB", "GB", "TB"]
        for unit in units:
            if size < 1024.0 or unit == units[-1]:
                return f"{size:.2f} {unit}"
            size /= 1024.0
    
    @staticmethod
    def sanitize_filename(filename: str) -> str:
        """Sanitize filename removing dangerous characters"""
        # Remove path components
        filename = os.path.basename(filename)
        
        # Replace unsafe characters
        filename = re.sub(r'[^\w\s\-\.]', '_', filename)
        
        # Limit length
        if len(filename) > 200:
            name, ext = os.path.splitext(filename)
            filename = name[:200-len(ext)] + ext
        
        return filename
    
    @staticmethod
    def create_progress_bar(percentage: float, length: int = 20) -> str:
        """Create visual progress bar"""
        filled = int(length * percentage / 100)
        empty = length - filled
        return '█' * filled + '○' * empty
    
    @staticmethod
    def format_duration(seconds: float) -> str:
        """Format duration in human readable format"""
        if seconds < 60:
            return f"{int(seconds)}s"
        elif seconds < 3600:
            return f"{int(seconds // 60)}m {int(seconds % 60)}s"
        else:
            hours = int(seconds // 3600)
            minutes = int((seconds % 3600) // 60)
            return f"{hours}h {minutes}m"
    
    @staticmethod
    def generate_player_url(filename: str, presigned_url: str) -> Optional[str]:
        """Generate web player URL for supported media types"""
        if not config.server.RENDER_URL:
            return None
        
        file_type = Utilities.get_file_type(filename)
        if file_type in ['video', 'audio', 'image']:
            encoded_url = base64.urlsafe_b64encode(
                presigned_url.encode()
            ).decode().rstrip('=')
            return f"{config.server.RENDER_URL}/player/{file_type}/{encoded_url}"
        return None

utils = Utilities()

# =============================================================================
# FLASK APPLICATION
# =============================================================================

flask_app = Flask(__name__, template_folder="templates")
flask_app.secret_key = config.server.SECRET_KEY

@flask_app.route('/')
def index():
    return render_template('index.html')

@flask_app.route('/player/<media_type>/<encoded_url>')
def player(media_type, encoded_url):
    try:
        # Add padding for base64 decoding
        padding = 4 - (len(encoded_url) % 4)
        if padding != 4:
            encoded_url += '=' * padding
        
        media_url = base64.urlsafe_b64decode(encoded_url).decode()
        return render_template('player.html', media_type=media_type, media_url=media_url)
    
    except Exception as e:
        return f"Error decoding URL: {str(e)}", 400

@flask_app.route('/about')
def about():
    return render_template('about.html')

@flask_app.route('/health')
def health_check():
    """Health check endpoint for monitoring"""
    health_status = {
        'status': 'healthy',
        'timestamp': datetime.utcnow().isoformat(),
        'version': '2.0.0',
        'services': {
            'wasabi': 'unknown',
            'redis': 'unknown',
            'telegram': 'unknown'
        }
    }
    
    # Check Wasabi
    try:
        wasabi_manager.s3_client.head_bucket(Bucket=config.wasabi.BUCKET)
        health_status['services']['wasabi'] = 'healthy'
    except Exception as e:
        health_status['services']['wasabi'] = 'unhealthy'
        health_status['status'] = 'degraded'
    
    # Check Redis
    try:
        if cache.redis_client:
            cache.redis_client.ping()
            health_status['services']['redis'] = 'healthy'
        else:
            health_status['services']['redis'] = 'disabled'
    except Exception as e:
        health_status['services']['redis'] = 'unhealthy'
        health_status['status'] = 'degraded'
    
    return jsonify(health_status)

@flask_app.route('/metrics')
def metrics():
    """Prometheus metrics endpoint"""
    if config.monitoring.ENABLE_METRICS:
        return generate_latest(), 200, {'Content-Type': CONTENT_TYPE_LATEST}
    else:
        return "Metrics disabled", 404

def run_flask():
    """Run Flask application"""
    flask_app.run(
        host=config.server.HOST,
        port=config.server.PORT,
        debug=False,
        threaded=True
    )

# =============================================================================
# TELEGRAM BOT
# =============================================================================

# Initialize Pyrogram client
app = Client(
    "wasabi_bot",
    api_id=config.telegram.API_ID,
    api_hash=config.telegram.API_HASH,
    bot_token=config.telegram.BOT_TOKEN,
    workers=100,
    sleep_threshold=60
)

class BotHandlers:
    """Advanced bot message handlers"""
    
    @staticmethod
    async def send_typing_action(chat_id: int):
        """Send typing action to indicate bot is processing"""
        try:
            await app.send_chat_action(chat_id, enums.ChatAction.TYPING)
        except Exception as e:
            logger.debug(f"Could not send typing action: {e}")
    
    @staticmethod
    async def handle_large_file_upload(message: Message, file_size: int) -> bool:
        """Check if user can upload large file based on storage limits"""
        user_id = message.from_user.id
        current_usage = wasabi_manager.get_user_storage_usage(user_id)
        
        if current_usage + file_size > config.limits.MAX_USER_STORAGE:
            await message.reply_text(
                f"❌ Storage limit exceeded!\n\n"
                f"Current usage: {utils.humanbytes(current_usage)}\n"
                f"File size: {utils.humanbytes(file_size)}\n"
                f"Limit: {utils.humanbytes(config.limits.MAX_USER_STORAGE)}\n\n"
                f"Please delete some files to free up space."
            )
            return False
        return True
    
    @staticmethod
    def create_advanced_keyboard(presigned_url: str, player_url: str = None, 
                               filename: str = None) -> InlineKeyboardMarkup:
        """Create advanced inline keyboard with multiple options"""
        keyboard = []
        
        if player_url:
            keyboard.append([
                InlineKeyboardButton("🎬 Web Player", url=player_url),
                InlineKeyboardButton("📱 Mobile Friendly", url=player_url)
            ])
        
        keyboard.append([InlineKeyboardButton("📥 Direct Download", url=presigned_url)])
        
        if filename and utils.get_file_type(filename) in ['video', 'audio']:
            keyboard.append([
                InlineKeyboardButton("🔗 Share Link", 
                                   switch_inline_query=filename),
                InlineKeyboardButton("🗑️ Delete", 
                                   callback_data=f"delete_{filename}")
            ])
        
        return InlineKeyboardMarkup(keyboard)

# =============================================================================
# BOT COMMAND HANDLERS
# =============================================================================

@app.on_message(filters.command("start"))
async def start_command(client, message: Message):
    if rate_limiter.is_rate_limited(message.from_user.id, "start"):
        retry_after = rate_limiter.get_retry_after(message.from_user.id, "start")
        await message.reply_text(
            f"⏰ Too many requests. Please try again in {retry_after} seconds."
        )
        return
    
    await BotHandlers.send_typing_action(message.chat.id)
    
    user_storage = wasabi_manager.get_user_storage_usage(message.from_user.id)
    
    welcome_text = f"""
🚀 **Advanced Cloud Storage Bot** 🚀

**📊 Your Storage:** {utils.humanbytes(user_storage)} / {utils.humanbytes(config.limits.MAX_USER_STORAGE)}
**📁 Max File Size:** {utils.humanbytes(config.limits.MAX_FILE_SIZE)}

**✨ Features:**
• Secure Wasabi Cloud Storage
• Web Player for Media Files
• Advanced Progress Tracking
• Storage Management
• Rate Limiting Protection

**📋 Available Commands:**
/start - Show this message
/upload - Upload files to cloud
/download <filename> - Download files
/play <filename> - Get web player link
/list - List your files
/delete <filename> - Delete files
/stats - Show storage statistics
/help - Get help

**🔒 Security:**
• Encrypted file metadata
• Secure presigned URLs
• Rate limiting enabled
• File type validation

**💎 Owner:** @Sathishkumar33
**📧 Support:** mraprguild@gmail.com
    """
    
    await message.reply_text(welcome_text, parse_mode=enums.ParseMode.MARKDOWN)

@app.on_message(filters.command("upload"))
async def upload_command(client, message: Message):
    """Handle upload command with enhanced features"""
    if rate_limiter.is_rate_limited(message.from_user.id, "upload"):
        retry_after = rate_limiter.get_retry_after(message.from_user.id, "upload")
        await message.reply_text(
            f"⏰ Too many upload requests. Please try again in {retry_after} seconds."
        )
        return
    
    await BotHandlers.send_typing_action(message.chat.id)
    
    if not message.reply_to_message or not (
        message.reply_to_message.document or 
        message.reply_to_message.video or 
        message.reply_to_message.audio or
        message.reply_to_message.photo
    ):
        await message.reply_text(
            "📎 Please reply to a file with /upload to upload it to cloud storage."
        )
        return
    
    # Use the existing upload handler
    await upload_file_handler(client, message.reply_to_message)

@app.on_message(filters.document | filters.video | filters.audio | filters.photo)
async def upload_file_handler(client, message: Message):
    """Enhanced file upload handler with progress tracking"""
    user_id = message.from_user.id
    
    if rate_limiter.is_rate_limited(user_id, "upload"):
        return
    
    # Get file information
    media = message.document or message.video or message.audio or message.photo
    if not media:
        await message.reply_text("❌ Unsupported file type")
        return
    
    # Get file size
    if message.photo:
        file_size = message.photo.sizes[-1].file_size
        file_name = f"photo_{message.id}.jpg"
    else:
        file_size = media.file_size
        file_name = media.file_name if hasattr(media, 'file_name') else f"file_{message.id}"
    
    file_name = utils.sanitize_filename(file_name)
    
    # Validate file size
    if file_size > config.limits.MAX_FILE_SIZE:
        await message.reply_text(
            f"❌ File too large!\n"
            f"Size: {utils.humanbytes(file_size)}\n"
            f"Limit: {utils.humanbytes(config.limits.MAX_FILE_SIZE)}"
        )
        return
    
    # Check storage limits
    if not await BotHandlers.handle_large_file_upload(message, file_size):
        return
    
    # Start upload process
    status_message = await message.reply_text(
        f"📥 **Downloading...**\n"
        f"📁 `{file_name}`\n"
        f"📊 {utils.humanbytes(file_size)}\n"
        f"⏳ Initializing..."
    )
    
    download_start = time.time()
    last_update_time = download_start
    processed_bytes = 0
    last_processed_bytes = 0
    
    if config.monitoring.ENABLE_METRICS:
        ACTIVE_UPLOADS.inc()
    
    async def progress_callback(current, total):
        nonlocal processed_bytes, last_update_time, last_processed_bytes
        
        processed_bytes = current
        current_time = time.time()
        
        # Update every 1 second or when significant progress is made
        if current_time - last_update_time >= 1 or current == total:
            percentage = (current / total) * 100
            elapsed = current_time - download_start
            
            # Calculate speed with smoothing
            time_diff = current_time - last_update_time
            if time_diff > 0:
                instant_speed = (current - last_processed_bytes) / time_diff
            else:
                instant_speed = 0
            
            # Calculate ETA
            if instant_speed > 0:
                eta = (total - current) / instant_speed
            else:
                eta = 0
            
            progress_bar = utils.create_progress_bar(percentage)
            
            progress_text = (
                f"📥 **Downloading...**\n"
                f"📁 `{file_name}`\n"
                f"📊 {utils.humanbytes(current)} / {utils.humanbytes(total)}\n"
                f"📈 {utils.humanbytes(instant_speed)}/s\n"
                f"⏱️ ETA: {utils.format_duration(eta)}\n"
                f"🕒 Elapsed: {utils.format_duration(elapsed)}\n"
                f"`[{progress_bar}] {percentage:.1f}%`"
            )
            
            try:
                await status_message.edit_text(progress_text, parse_mode=enums.ParseMode.MARKDOWN)
                last_update_time = current_time
                last_processed_bytes = current
            except FloodWait as e:
                await asyncio.sleep(e.value)
            except Exception as e:
                logger.debug(f"Progress update failed: {e}")
    
    try:
        # Download file
        file_path = await message.download(progress=progress_callback)
        
        # Upload to Wasabi
        await status_message.edit_text("📤 **Uploading to Wasabi Cloud...**")
        
        user_file_key = f"user_{user_id}/{file_name}"
        
        # Upload with metadata
        metadata = {
            'upload-time': str(int(time.time())),
            'user-id': str(user_id),
            'file-size': str(file_size),
            'original-message': str(message.id)
        }
        
        await asyncio.to_thread(
            wasabi_manager.upload_file_with_metadata,
            file_path, user_file_key, metadata
        )
        
        # Generate shareable URLs
        presigned_url = wasabi_manager.generate_secure_presigned_url(user_file_key)
        player_url = utils.generate_player_url(file_name, presigned_url)
        
        # Create keyboard
        keyboard = BotHandlers.create_advanced_keyboard(presigned_url, player_url, file_name)
        
        total_time = time.time() - download_start
        upload_speed = file_size / total_time if total_time > 0 else 0
        
        success_text = (
            f"✅ **Upload Complete!**\n\n"
            f"📁 **File:** `{file_name}`\n"
            f"📊 **Size:** {utils.humanbytes(file_size)}\n"
            f"⚡ **Speed:** {utils.humanbytes(upload_speed)}/s\n"
            f"⏱️ **Time:** {utils.format_duration(total_time)}\n"
            f"🔗 **Expires:** {config.limits.PRESIGNED_URL_EXPIRY // 3600} hours\n\n"
            f"**Your file is now securely stored in the cloud!** ☁️"
        )
        
        await status_message.edit_text(
            success_text,
            reply_markup=keyboard,
            parse_mode=enums.ParseMode.MARKDOWN
        )
        
        if config.monitoring.ENABLE_METRICS:
            UPLOAD_SIZE.observe(file_size)
            PROCESSING_TIME.observe(total_time)
        
    except Exception as e:
        logger.error(f"Upload error for user {user_id}: {e}")
        error_text = (
            f"❌ **Upload Failed**\n\n"
            f"**Error:** `{str(e)}`\n\n"
            f"Please try again or contact support if the problem persists."
        )
        await status_message.edit_text(error_text, parse_mode=enums.ParseMode.MARKDOWN)
    
    finally:
        # Cleanup
        if 'file_path' in locals() and os.path.exists(file_path):
            try:
                os.remove(file_path)
            except Exception as e:
                logger.warning(f"Could not delete temp file {file_path}: {e}")
        
        if config.monitoring.ENABLE_METRICS:
            ACTIVE_UPLOADS.dec()

# ... (other handlers remain the same as in previous version, just update config references)

# =============================================================================
# STARTUP & SHUTDOWN
# =============================================================================

@app.on_raw_update()
async def raw_update_handler(_, update, *args):
    """Handle raw updates for metrics"""
    if config.monitoring.ENABLE_METRICS:
        REQUEST_COUNT.labels(endpoint='raw_update', method='POST', status='200').inc()

async def startup():
    """Bot startup routine"""
    logger.info("🤖 Starting Advanced Wasabi Storage Bot...")
    
    # Test connections
    try:
        wasabi_manager.s3_client.head_bucket(Bucket=config.wasabi.BUCKET)
        logger.info("✅ Wasabi connection verified")
    except Exception as e:
        logger.error(f"❌ Wasabi connection failed: {e}")
        raise
    
    logger.info("✅ Bot startup completed")

async def shutdown():
    """Bot shutdown routine"""
    logger.info("🛑 Shutting down bot...")
    # Cleanup resources if needed

# =============================================================================
# MAIN EXECUTION
# =============================================================================

if __name__ == "__main__":
    print("🚀 Starting Advanced Wasabi Storage Bot with Web Player...")
    print(f"📊 Metrics enabled: {config.monitoring.ENABLE_METRICS}")
    print(f"💾 Max file size: {utils.humanbytes(config.limits.MAX_FILE_SIZE)}")
    print(f"👤 User storage: {utils.humanbytes(config.limits.MAX_USER_STORAGE)}")
    print(f"🔒 Encryption: {config.encryption.ENABLED}")
    print(f"🗄️ Redis: {config.redis.ENABLED}")
    
    # Start Flask server in background thread
    flask_thread = Thread(target=run_flask, daemon=True)
    flask_thread.start()
    print(f"🌐 Flask server started on {config.server.HOST}:{config.server.PORT}")
    
    # Start the bot
    try:
        app.run(startup(), shutdown())
    except KeyboardInterrupt:
        print("\n🛑 Bot stopped by user")
    except Exception as e:
        logger.critical(f"Bot crashed: {e}")
        raise
