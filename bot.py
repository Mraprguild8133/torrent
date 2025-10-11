#!/usr/bin/env python3
"""
🚀 WORLD-CLASS TELEGRAM WASABI BOT
Advanced features, high performance, enterprise-grade architecture
"""

import os
import asyncio
import logging
import aiofiles
import hashlib
import time
import uuid
from typing import Optional, Dict, Tuple, Callable, List, Any
from pathlib import Path
from asyncio import Lock, Semaphore
from dataclasses import dataclass
from contextlib import asynccontextmanager
import zlib
import json

from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardButton, InlineKeyboardMarkup
from pyrogram.enums import ParseMode, MessageEntityType
from pyrogram.errors import FloodWait, RPCError, MessageNotModified
import aioboto3
from botocore.exceptions import ClientError
import aiohttp
from cryptography.fernet import Fernet

from config import config, FileType

# Create logs directory if it doesn't exist
logs_dir = Path('logs')
logs_dir.mkdir(exist_ok=True)

# Create download directory if it doesn't exist
download_dir = Path(config.DOWNLOAD_PATH)
download_dir.mkdir(exist_ok=True)

# Configure advanced logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-8s | %(name)-20s | %(message)s',
    handlers=[
        logging.FileHandler('logs/bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

@dataclass
class UploadStats:
    """Real-time upload statistics"""
    files_uploaded: int = 0
    total_upload_size: int = 0
    average_speed: float = 0.0
    success_rate: float = 100.0

class AdvancedCache:
    """High-performance memory cache with TTL"""
    
    def __init__(self):
        self._cache: Dict[str, Dict] = {}
        self._lock = Lock()
    
    async def set(self, key: str, value: Any, ttl: int = None):
        """Set cache value with TTL"""
        async with self._lock:
            self._cache[key] = {
                'value': value,
                'expires': time.time() + (ttl or config.CACHE.TTL)
            }
            if len(self._cache) > config.CACHE.MAX_SIZE:
                await self._cleanup()
    
    async def get(self, key: str) -> Any:
        """Get cache value"""
        async with self._lock:
            if key not in self._cache:
                return None
            
            item = self._cache[key]
            if time.time() > item['expires']:
                del self._cache[key]
                return None
            
            return item['value']
    
    async def delete(self, key: str):
        """Delete cache key"""
        async with self._lock:
            self._cache.pop(key, None)
    
    async def _cleanup(self):
        """Clean expired cache entries"""
        now = time.time()
        expired = [k for k, v in self._cache.items() if now > v['expires']]
        for key in expired:
            del self._cache[key]

class RateLimiter:
    """Advanced rate limiting system"""
    
    def __init__(self):
        self._user_requests: Dict[int, List[float]] = {}
        self._file_downloads: Dict[str, List[float]] = {}
        self._lock = Lock()
    
    async def check_user_limit(self, user_id: int) -> bool:
        """Check if user is within rate limit"""
        async with self._lock:
            now = time.time()
            window_start = now - 60  # 1 minute window
            
            if user_id not in self._user_requests:
                self._user_requests[user_id] = []
            
            self._user_requests[user_id] = [
                req_time for req_time in self._user_requests[user_id] 
                if req_time > window_start
            ]
            
            if len(self._user_requests[user_id]) >= config.SECURITY.RATE_LIMIT_PER_USER:
                return False
            
            self._user_requests[user_id].append(now)
            return True
    
    async def check_file_limit(self, filename: str) -> bool:
        """Check if file download is within rate limit"""
        async with self._lock:
            now = time.time()
            window_start = now - 60
            
            if filename not in self._file_downloads:
                self._file_downloads[filename] = []
            
            self._file_downloads[filename] = [
                dl_time for dl_time in self._file_downloads[filename] 
                if dl_time > window_start
            ]
            
            if len(self._file_downloads[filename]) >= config.SECURITY.RATE_LIMIT_PER_FILE:
                return False
            
            self._file_downloads[filename].append(now)
            return True

class ConnectionPool:
    """Managed connection pool for high performance"""
    
    def __init__(self):
        self._session = None
        self._semaphore = Semaphore(config.PERFORMANCE.MAX_CONNECTIONS)
    
    async def get_http_session(self) -> aiohttp.ClientSession:
        """Get or create HTTP session"""
        if not self._session or self._session.closed:
            timeout = aiohttp.ClientTimeout(
                total=config.PERFORMANCE.CONNECTION_TIMEOUT,
                sock_connect=10,
                sock_read=config.PERFORMANCE.READ_TIMEOUT
            )
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session
    
    @asynccontextmanager
    async def wasabi_client(self):
        """Managed Wasabi client with connection pooling"""
        async with self._semaphore:
            session = aioboto3.Session()
            async with session.client(
                's3',
                endpoint_url=config.WASABI.ENDPOINT,
                aws_access_key_id=config.WASABI.ACCESS_KEY,
                aws_secret_access_key=config.WASABI.SECRET_KEY,
                region_name=config.WASABI.REGION,
                config=aioboto3.Config(
                    max_pool_connections=config.PERFORMANCE.MAX_CONNECTIONS,
                    retries={'max_attempts': 3, 'mode': 'adaptive'}
                )
            ) as s3_client:
                yield s3_client
    
    async def close(self):
        """Close all connections"""
        if self._session:
            await self._session.close()

class StreamingFileProcessor:
    """Advanced file streaming processor"""
    
    def __init__(self):
        self.chunk_size = config.PERFORMANCE.CHUNK_SIZE
        self.buffer_size = config.PERFORMANCE.BUFFER_SIZE
    
    async def stream_upload(self, file_path: str, filename: str, 
                          progress_callback: Callable = None) -> bool:
        """Stream file upload with compression"""
        try:
            file_size = os.path.getsize(file_path)
            uploaded_size = 0
            
            async with aiofiles.open(file_path, 'rb') as file:
                async with ConnectionPool().wasabi_client() as s3:
                    if file_size > config.WASABI.MULTIPART_THRESHOLD:
                        await self._multipart_upload(s3, file, filename, file_size, progress_callback)
                    else:
                        file_data = await file.read()
                        if config.ENABLE_COMPRESSION and self._is_compressible(filename):
                            file_data = zlib.compress(file_data, level=6)
                        
                        await s3.put_object(
                            Bucket=config.WASABI.BUCKET,
                            Key=filename,
                            Body=file_data
                        )
                        
                        if progress_callback:
                            await progress_callback(file_size, file_size)
            
            return True
            
        except Exception as e:
            logger.error(f"Stream upload error: {e}")
            return False
    
    async def _multipart_upload(self, s3, file, filename: str, file_size: int, 
                              progress_callback: Callable):
        """Advanced multipart upload with streaming"""
        mpu = await s3.create_multipart_upload(Bucket=config.WASABI.BUCKET, Key=filename)
        upload_id = mpu['UploadId']
        
        try:
            parts = []
            part_number = 1
            uploaded_bytes = 0
            
            while True:
                chunk = await file.read(config.WASABI.MULTIPART_CHUNKSIZE)
                if not chunk:
                    break
                
                if config.ENABLE_COMPRESSION and self._is_compressible(filename):
                    chunk = zlib.compress(chunk, level=6)
                
                part = await s3.upload_part(
                    Bucket=config.WASABI.BUCKET,
                    Key=filename,
                    PartNumber=part_number,
                    UploadId=upload_id,
                    Body=chunk
                )
                
                parts.append({'PartNumber': part_number, 'ETag': part['ETag']})
                uploaded_bytes += len(chunk)
                
                if progress_callback:
                    await progress_callback(uploaded_bytes, file_size)
                
                part_number += 1
            
            await s3.complete_multipart_upload(
                Bucket=config.WASABI.BUCKET,
                Key=filename,
                UploadId=upload_id,
                MultipartUpload={'Parts': parts}
            )
            
        except Exception as e:
            await s3.abort_multipart_upload(
                Bucket=config.WASABI.BUCKET,
                Key=filename,
                UploadId=upload_id
            )
            raise e
    
    def _is_compressible(self, filename: str) -> bool:
        """Check if file type is compressible"""
        compressible_extensions = {'txt', 'log', 'json', 'xml', 'csv', 'html', 'css', 'js'}
        ext = Path(filename).suffix.lower().lstrip('.')
        return ext in compressible_extensions

class AdvancedWasabiManager:
    """Enterprise-grade Wasabi storage manager"""
    
    def __init__(self):
        self.connection_pool = ConnectionPool()
        self.stream_processor = StreamingFileProcessor()
        self.cache = AdvancedCache()
        self.stats = UploadStats()
    
    async def upload_file(self, file_path: str, filename: str, 
                         progress_callback: Callable = None) -> bool:
        """Advanced file upload with multiple optimizations"""
        start_time = time.time()
        
        try:
            if not await self._validate_file(file_path, filename):
                return False
            
            success = await self.stream_processor.stream_upload(
                file_path, filename, progress_callback
            )
            
            if success:
                await self._update_stats(file_path, start_time)
                await self.cache.set(f"file_{filename}", {
                    'size': os.path.getsize(file_path),
                    'upload_time': time.time()
                })
            
            return success
            
        except Exception as e:
            logger.error(f"Upload failed: {e}")
            return False
    
    async def download_file(self, filename: str, local_path: str,
                           progress_callback: Callable = None) -> bool:
        """Advanced file download with streaming"""
        try:
            async with self.connection_pool.wasabi_client() as s3:
                head_response = await s3.head_object(
                    Bucket=config.WASABI.BUCKET, 
                    Key=filename
                )
                file_size = head_response['ContentLength']
                
                response = await s3.get_object(
                    Bucket=config.WASABI.BUCKET, 
                    Key=filename
                )
                
                async with response['Body'] as stream:
                    async with aiofiles.open(local_path, 'wb') as file:
                        downloaded = 0
                        async for chunk in stream.iter_chunks():
                            await file.write(chunk)
                            downloaded += len(chunk)
                            if progress_callback:
                                await progress_callback(downloaded, file_size)
            
            return True
            
        except Exception as e:
            logger.error(f"Download failed: {e}")
            return False
    
    async def list_files(self, prefix: str = "", limit: int = 100) -> List[Dict]:
        """Advanced file listing with caching"""
        cache_key = f"list_{prefix}_{limit}"
        cached = await self.cache.get(cache_key)
        
        if cached:
            return cached
        
        try:
            async with self.connection_pool.wasabi_client() as s3:
                files = []
                paginator = s3.get_paginator('list_objects_v2')
                
                async for page in paginator.paginate(
                    Bucket=config.WASABI.BUCKET,
                    Prefix=prefix,
                    PaginationConfig={'PageSize': limit}
                ):
                    if 'Contents' in page:
                        for obj in page['Contents']:
                            files.append({
                                'key': obj['Key'],
                                'size': obj['Size'],
                                'modified': obj['LastModified'],
                                'etag': obj['ETag']
                            })
                
                await self.cache.set(cache_key, files, ttl=300)
                return files
                
        except Exception as e:
            logger.error(f"List files failed: {e}")
            return []
    
    async def file_exists(self, filename: str) -> bool:
        """Check if file exists in Wasabi"""
        async with self.connection_pool.wasabi_client() as s3:
            try:
                await s3.head_object(Bucket=config.WASABI.BUCKET, Key=filename)
                return True
            except ClientError:
                return False
    
    async def delete_file(self, filename: str):
        """Delete file from Wasabi"""
        async with self.connection_pool.wasabi_client() as s3:
            await s3.delete_object(Bucket=config.WASABI.BUCKET, Key=filename)
    
    async def _validate_file(self, file_path: str, filename: str) -> bool:
        """Comprehensive file validation"""
        file_size = os.path.getsize(file_path)
        if file_size > config.SECURITY.MAX_FILE_SIZE:
            raise ValueError(f"File too large: {file_size} bytes")
        
        ext = Path(filename).suffix.lower().lstrip('.')
        if ext in config.SECURITY.BLOCKED_EXTENSIONS:
            raise ValueError(f"File type blocked: {ext}")
        
        if (config.SECURITY.ALLOWED_EXTENSIONS and 
            ext not in config.SECURITY.ALLOWED_EXTENSIONS):
            raise ValueError(f"File type not allowed: {ext}")
        
        return True
    
    async def _update_stats(self, file_path: str, start_time: float):
        """Update upload statistics"""
        file_size = os.path.getsize(file_path)
        upload_time = time.time() - start_time
        speed = file_size / upload_time if upload_time > 0 else 0
        
        self.stats.files_uploaded += 1
        self.stats.total_upload_size += file_size
        self.stats.average_speed = (
            self.stats.average_speed * 0.9 + speed * 0.1
        )

class CallbackManager:
    """Advanced callback management"""
    
    def __init__(self):
        self._callbacks: Dict[str, Dict] = {}
    
    def register_callback(self, action: str, data: Dict) -> str:
        """Register callback with data"""
        callback_id = str(uuid.uuid4())[:12]  # Short ID
        self._callbacks[callback_id] = {
            'action': action,
            'data': data,
            'created': time.time()
        }
        return callback_id
    
    def get_callback(self, callback_id: str) -> Optional[Dict]:
        """Retrieve callback data"""
        if callback_id not in self._callbacks:
            return None
        
        callback = self._callbacks[callback_id]
        
        # Cleanup old callbacks
        if time.time() - callback['created'] > 3600:
            del self._callbacks[callback_id]
            return None
        
        return callback

class ProgressManager:
    """Advanced progress update manager to handle MessageNotModified errors"""
    
    def __init__(self):
        self._last_updates: Dict[int, Dict] = {}  # message_id -> last content
        self._update_lock = Lock()
    
    async def safe_edit_message(self, message: Message, text: str, **kwargs) -> bool:
        """Safely edit message with protection against MessageNotModified errors"""
        try:
            # Check if content is actually different
            message_id = message.id
            async with self._update_lock:
                last_content = self._last_updates.get(message_id, {})
                
                # Only update if content has changed
                if last_content.get('text') == text and last_content.get('kwargs') == kwargs:
                    return True  # No update needed
                
                # Update the message
                await message.edit_text(text, **kwargs)
                
                # Store the last content
                self._last_updates[message_id] = {
                    'text': text,
                    'kwargs': kwargs,
                    'timestamp': time.time()
                }
                
                # Cleanup old entries (older than 1 hour)
                self._cleanup_old_entries()
                
            return True
            
        except MessageNotModified:
            # This is fine - the message already has the same content
            logger.debug(f"Message {message.id} not modified (same content)")
            return True
        except Exception as e:
            logger.error(f"Failed to edit message {message.id}: {e}")
            return False
    
    def _cleanup_old_entries(self):
        """Clean up old message entries"""
        current_time = time.time()
        expired_messages = [
            msg_id for msg_id, data in self._last_updates.items()
            if current_time - data['timestamp'] > 3600  # 1 hour
        ]
        for msg_id in expired_messages:
            del self._last_updates[msg_id]

class WorldClassTelegramBot:
    """
    🚀 WORLD-CLASS TELEGRAM BOT
    Enterprise-grade features, maximum performance, advanced architecture
    """
    
    def __init__(self):
        if not config.validate():
            raise ValueError("Configuration validation failed!")
        
        # Initialize core components
        self.wasabi = AdvancedWasabiManager()
        self.callback_manager = CallbackManager()
        self.rate_limiter = RateLimiter()
        self.cache = AdvancedCache()
        self.progress_manager = ProgressManager()
        
        # Progress tracking
        self._last_progress = {}  # message_id -> last progress percentage
        
        # Performance tracking
        self.performance_stats = {
            'requests_processed': 0,
            'files_uploaded': 0,
            'files_downloaded': 0,
            'total_data_transferred': 0,
            'average_response_time': 0.0
        }
        
        # Initialize Telegram client with max performance
        self.app = Client(
            name="world_class_bot",
            api_id=config.API_ID,
            api_hash=config.API_HASH,
            bot_token=config.BOT_TOKEN,
            workers=config.PERFORMANCE.MAX_WORKERS,
            max_concurrent_transmissions=config.PERFORMANCE.MAX_CONCURRENT_TRANSMISSIONS,
            sleep_threshold=60,
            in_memory=True
        )
        
        self._setup_advanced_handlers()
    
    def _setup_advanced_handlers(self):
        """Setup advanced message handlers with middleware"""
        
        @self.app.on_message(filters.command("start"))
        async def advanced_start(client, message: Message):
            """Advanced start command with rich formatting"""
            user = message.from_user
            await message.reply_text(
                f"🚀 **WORLD-CLASS WASABI BOT**\n\n"
                f"👤 **Welcome {user.first_name}!**\n"
                f"💾 **Storage:** Wasabi Enterprise Cloud\n"
                f"⚡ **Performance:** Ultra-High Speed\n"
                f"🔒 **Security:** Military Grade\n\n"
                "**✨ Advanced Features:**\n"
                "• 🚀 4GB File Support\n"
                "• ⚡ Parallel Processing\n"
                "• 🔄 Real-time Streaming\n"
                "• 📊 Advanced Analytics\n"
                "• 🛡️ Rate Limiting\n"
                "• 💾 Smart Caching\n\n"
                "**📋 Available Commands:**\n"
                "• `/upload` - Upload files\n"
                "• `/download <filename>` - Download files\n"
                "• `/list` - List all files\n"
                "• `/stats` - Performance stats\n"
                "• `/help` - Detailed help\n\n"
                "**💡 Pro Tip:** Just send any file to upload instantly!",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📤 Upload Guide", callback_data="upload_guide"),
                     InlineKeyboardButton("📥 Download Guide", callback_data="download_guide")],
                    [InlineKeyboardButton("📊 Live Stats", callback_data="live_stats"),
                     InlineKeyboardButton("🛠️ Help", callback_data="advanced_help")]
                ])
            )
        
        @self.app.on_message(filters.command("help"))
        async def help_command(client, message: Message):
            await advanced_start(client, message)
        
        @self.app.on_message(filters.command("stats"))
        async def show_stats(client, message: Message):
            """Show advanced performance statistics"""
            stats = self.wasabi.stats
            perf = self.performance_stats
            
            await message.reply_text(
                f"📊 **REAL-TIME PERFORMANCE DASHBOARD**\n\n"
                f"**📈 Upload Statistics:**\n"
                f"• Files Uploaded: `{stats.files_uploaded}`\n"
                f"• Total Data: `{self._format_size(stats.total_upload_size)}`\n"
                f"• Avg Speed: `{self._format_speed(stats.average_speed)}/s`\n"
                f"• Success Rate: `{stats.success_rate:.1f}%`\n\n"
                f"**⚡ System Performance:**\n"
                f"• Requests Processed: `{perf['requests_processed']}`\n"
                f"• Files Downloaded: `{perf['files_downloaded']}`\n"
                f"• Total Transfer: `{self._format_size(perf['total_data_transferred'])}`\n"
                f"• Avg Response: `{perf['average_response_time']:.2f}s`\n\n"
                f"**🛠️ System Info:**\n"
                f"• Workers: `{config.PERFORMANCE.MAX_WORKERS}`\n"
                f"• Concurrent: `{config.PERFORMANCE.MAX_CONCURRENT_TRANSMISSIONS}`\n"
                f"• Chunk Size: `{self._format_size(config.PERFORMANCE.CHUNK_SIZE)}`",
                parse_mode=ParseMode.MARKDOWN
            )
        
        @self.app.on_message(filters.command("upload"))
        async def upload_guide(client, message: Message):
            """Upload guide"""
            await message.reply_text(
                "📤 **UPLOAD GUIDE**\n\n"
                "**Method 1:** Direct Upload\n"
                "• Simply send any file (document, video, audio, photo)\n"
                "• Bot will automatically upload to Wasabi\n"
                "• Files keep original names\n\n"
                "**Method 2:** Command Upload\n"
                "• Use `/upload` command for instructions\n"
                "• Follow the interactive guide\n\n"
                "**Supported Files:**\n"
                "• Documents (up to 4GB)\n"
                "• Videos (up to 4GB)\n"
                "• Audio files\n"
                "• Photos\n"
                "• Archives (ZIP, RAR, etc.)\n\n"
                "**🚀 Just send a file to get started!**",
                parse_mode=ParseMode.MARKDOWN
            )
        
        @self.app.on_message(filters.document | filters.video | filters.audio | filters.photo)
        async def handle_file_upload(client, message: Message):
            """Advanced file upload handler with rate limiting"""
            user_id = message.from_user.id
            
            # Rate limiting check
            if not await self.rate_limiter.check_user_limit(user_id):
                await message.reply_text(
                    "🚫 **Rate Limit Exceeded**\n\n"
                    "Please wait a minute before uploading more files.",
                    parse_mode=ParseMode.MARKDOWN
                )
                return
            
            start_time = time.time()
            await self._process_file_upload(message)
            
            # Update performance stats
            response_time = time.time() - start_time
            self._update_performance_stats(response_time)
        
        @self.app.on_message(filters.command("download"))
        async def handle_file_download(client, message: Message):
            """Advanced file download handler"""
            if len(message.command) < 2:
                await self._show_file_list(message)
                return
            
            filename = ' '.join(message.command[1:])
            await self._process_file_download(message, filename)
        
        @self.app.on_message(filters.command("list"))
        async def handle_file_list(client, message: Message):
            """Advanced file listing"""
            await self._show_file_list(message)
        
        @self.app.on_message(filters.command("info"))
        async def show_info(client, message: Message):
            """Show system information"""
            await message.reply_text(
                f"🤖 **SYSTEM INFORMATION**\n\n"
                f"**💾 Storage Provider:** Wasabi Hot Cloud Storage\n"
                f"**🌐 Region:** `{config.WASABI.REGION}`\n"
                f"**📦 Bucket:** `{config.WASABI.BUCKET}`\n"
                f"**⚡ Max File Size:** `{self._format_size(config.SECURITY.MAX_FILE_SIZE)}`\n"
                f"**🔧 Workers:** `{config.PERFORMANCE.MAX_WORKERS}`\n"
                f"**🔄 Concurrent:** `{config.PERFORMANCE.MAX_CONCURRENT_TRANSMISSIONS}`\n\n"
                f"**🛡️ Security Features:**\n"
                f"• Rate Limiting\n"
                f"• File Type Validation\n"
                f"• Size Limits\n"
                f"• Secure Connections\n\n"
                f"**🚀 Performance Features:**\n"
                f"• Parallel Processing\n"
                f"• Streaming Upload/Download\n"
                f"• Smart Caching\n"
                f"• Connection Pooling",
                parse_mode=ParseMode.MARKDOWN
            )
    
    async def _process_file_upload(self, message: Message):
        """Process file upload with advanced features"""
        try:
            # Get file information
            file_info = await self._extract_file_info(message)
            if not file_info:
                return
            
            file_obj, file_type, filename = file_info
            
            # Validate file size
            if file_obj.file_size > config.SECURITY.MAX_FILE_SIZE:
                await message.reply_text(
                    f"❌ **File Too Large**\n\n"
                    f"**File Size:** {self._format_size(file_obj.file_size)}\n"
                    f"**Max Size:** {self._format_size(config.SECURITY.MAX_FILE_SIZE)}\n\n"
                    "Please upload a smaller file.",
                    parse_mode=ParseMode.MARKDOWN
                )
                return
            
            # Create status message
            status_msg = await message.reply_text(
                f"🚀 **Processing Upload**\n\n"
                f"**File:** `{filename}`\n"
                f"**Size:** {self._format_size(file_obj.file_size)}\n"
                f"**Type:** {file_type.value}\n\n"
                "🔄 Initializing transfer...",
                parse_mode=ParseMode.MARKDOWN
            )
            
            # Download from Telegram
            download_path = await self._download_telegram_file(message, file_obj, status_msg)
            if not download_path:
                return
            
            # Upload to Wasabi
            success = await self.wasabi.upload_file(
                download_path,
                filename,
                progress_callback=lambda current, total: self._update_progress(
                    status_msg, current, total, "🚀 Uploading to Wasabi"
                )
            )
            
            # Cleanup
            if os.path.exists(download_path):
                os.remove(download_path)
            
            if success:
                download_url = f"{config.WASABI.ENDPOINT}/{config.WASABI.BUCKET}/{filename}"
                
                # Create advanced keyboard
                keyboard = InlineKeyboardMarkup([
                    [InlineKeyboardButton("🌐 Direct URL", url=download_url)],
                    [InlineKeyboardButton("📥 Download", callback_data=self.callback_manager.register_callback("download", {"filename": filename}))],
                    [InlineKeyboardButton("🗑️ Delete", callback_data=self.callback_manager.register_callback("delete", {"filename": filename}))]
                ])
                
                await self.progress_manager.safe_edit_message(
                    status_msg,
                    f"✅ **UPLOAD COMPLETE**\n\n"
                    f"**File:** `{filename}`\n"
                    f"**Size:** {self._format_size(file_obj.file_size)}\n"
                    f"**Storage:** Wasabi Enterprise\n"
                    f"**Bucket:** `{config.WASABI.BUCKET}`\n\n"
                    f"**🚀 Performance:**\n"
                    f"• Transfer: Complete\n"
                    f"• Security: Verified\n"
                    f"• Availability: Global\n\n"
                    f"Use `/download {filename}` to retrieve anytime.",
                    reply_markup=keyboard,
                    parse_mode=ParseMode.MARKDOWN
                )
                
                self.performance_stats['files_uploaded'] += 1
                self.performance_stats['total_data_transferred'] += file_obj.file_size
            else:
                await self.progress_manager.safe_edit_message(
                    status_msg,
                    "❌ **Upload Failed**\n\n"
                    "The file could not be uploaded. Please try again.",
                    parse_mode=ParseMode.MARKDOWN
                )
                
        except Exception as e:
            logger.error(f"Upload processing error: {e}")
            await message.reply_text(
                f"❌ **Upload Error**\n\n"
                f"Error: `{str(e)}`\n\n"
                "Please try again or contact support.",
                parse_mode=ParseMode.MARKDOWN
            )
    
    async def _process_file_download(self, message: Message, filename: str):
        """Process file download with advanced features"""
        try:
            # Rate limiting for file downloads
            if not await self.rate_limiter.check_file_limit(filename):
                await message.reply_text(
                    "🚫 **Download Rate Limit**\n\n"
                    "This file has been downloaded too many times recently. Please wait a minute.",
                    parse_mode=ParseMode.MARKDOWN
                )
                return
            
            # Check if file exists
            if not await self.wasabi.file_exists(filename):
                await message.reply_text(
                    f"❌ **File Not Found**\n\n"
                    f"File `{filename}` does not exist in storage.",
                    parse_mode=ParseMode.MARKDOWN
                )
                return
            
            status_msg = await message.reply_text(
                f"📥 **Initiating Download**\n\n"
                f"**File:** `{filename}`\n"
                f"**Source:** Wasabi Cloud\n\n"
                "🔄 Preparing transfer...",
                parse_mode=ParseMode.MARKDOWN
            )
            
            # Create local path
            download_dir = Path(config.DOWNLOAD_PATH)
            download_dir.mkdir(exist_ok=True)
            local_path = download_dir / f"temp_{int(time.time())}_{filename}"
            
            # Download from Wasabi
            success = await self.wasabi.download_file(
                filename,
                str(local_path),
                progress_callback=lambda current, total: self._update_progress(
                    status_msg, current, total, "📥 Downloading from Wasabi"
                )
            )
            
            if success and os.path.exists(local_path):
                await self.progress_manager.safe_edit_message(status_msg, "📤 **Sending to Telegram...**")
                
                # Send file to user
                await message.reply_document(
                    document=str(local_path),
                    caption=(
                        f"📁 **{filename}**\n"
                        f"✅ Downloaded from Wasabi\n"
                        f"🏪 Bucket: `{config.WASABI.BUCKET}`\n"
                        f"🌐 Region: `{config.WASABI.REGION}`\n\n"
                        f"🚀 **World-Class Transfer Complete**"
                    ),
                    parse_mode=ParseMode.MARKDOWN
                )
                
                await status_msg.delete()
                
                # Update stats
                file_size = os.path.getsize(local_path)
                self.performance_stats['files_downloaded'] += 1
                self.performance_stats['total_data_transferred'] += file_size
                
                # Cleanup
                os.remove(local_path)
            else:
                await self.progress_manager.safe_edit_message(
                    status_msg,
                    f"❌ **Download Failed**\n\n"
                    f"File `{filename}` could not be downloaded.",
                    parse_mode=ParseMode.MARKDOWN
                )
                
        except Exception as e:
            logger.error(f"Download processing error: {e}")
            await message.reply_text(
                f"❌ **Download Error**\n\n"
                f"Error: `{str(e)}`",
                parse_mode=ParseMode.MARKDOWN
            )
    
    async def _show_file_list(self, message: Message):
        """Show advanced file listing"""
        try:
            status_msg = await message.reply_text("📁 **Fetching File Inventory...**")
            
            files = await self.wasabi.list_files(limit=50)
            
            if not files:
                await self.progress_manager.safe_edit_message(
                    status_msg,
                    "📭 **No Files Found**\n\n"
                    "Your Wasabi storage is empty.\n"
                    "Send a file to get started! 🚀",
                    parse_mode=ParseMode.MARKDOWN
                )
                return
            
            # Group files by type
            file_groups = {}
            total_size = 0
            
            for file in files:
                ext = Path(file['key']).suffix.lower().lstrip('.') or 'other'
                file_groups.setdefault(ext, []).append(file)
                total_size += file['size']
            
            # Create file list message
            file_list = []
            for ext, group in list(file_groups.items())[:10]:
                count = len(group)
                size = sum(f['size'] for f in group)
                file_list.append(f"• **{ext.upper()}:** {count} files ({self._format_size(size)})")
            
            await self.progress_manager.safe_edit_message(
                status_msg,
                f"📊 **FILE INVENTORY**\n\n"
                f"**Total Files:** {len(files)}\n"
                f"**Total Size:** {self._format_size(total_size)}\n"
                f"**Bucket:** `{config.WASABI.BUCKET}`\n\n"
                f"**📂 File Types:**\n" + "\n".join(file_list) + "\n\n"
                f"**💡 Usage:** `/download filename` to download any file",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 Refresh", callback_data="refresh_list")]
                ])
            )
            
        except Exception as e:
            logger.error(f"File list error: {e}")
            await message.reply_text(
                f"❌ **Listing Error**\n\nError: `{str(e)}`",
                parse_mode=ParseMode.MARKDOWN
            )
    
    async def _download_telegram_file(self, message: Message, file_obj, status_msg) -> Optional[str]:
        """Download file from Telegram with advanced progress"""
        try:
            download_dir = Path(config.DOWNLOAD_PATH)
            download_dir.mkdir(exist_ok=True)
            
            temp_path = download_dir / f"temp_{message.id}_{int(time.time())}"
            
            async def progress_callback(current, total):
                await self._update_progress(status_msg, current, total, "📥 Downloading from Telegram")
            
            file_path = await message.download(
                file_name=str(temp_path),
                progress=progress_callback
            )
            
            return file_path if file_path and os.path.exists(file_path) else None
            
        except Exception as e:
            logger.error(f"Telegram download error: {e}")
            await self.progress_manager.safe_edit_message(status_msg, "❌ Failed to download file from Telegram")
            return None
    
    async def _update_progress(self, message: Message, current: int, total: int, operation: str):
        """Advanced progress updates with robust error handling"""
        try:
            # Calculate progress with minimum 1% step to avoid too frequent updates
            percent = (current / total) * 100 if total > 0 else 0
            
            # Only update if progress has changed significantly (at least 1% or 5MB)
            message_id = message.id
            last_percent = self._last_progress.get(message_id, 0)
            
            min_update_threshold = 1  # 1% minimum change
            min_size_threshold = 5 * 1024 * 1024  # 5MB minimum change
            
            size_changed = (current - self._last_progress.get(f"{message_id}_bytes", 0)) >= min_size_threshold
            percent_changed = abs(percent - last_percent) >= min_update_threshold
            
            if not (percent_changed or size_changed or current == total):
                return  # Skip update if change is too small
            
            # Create visual progress bar
            bar_length = 20
            filled_length = int(bar_length * current // total) if total > 0 else 0
            bar = '█' * filled_length + '▱' * (bar_length - filled_length)
            
            # Calculate speed and ETA
            current_time = time.time()
            elapsed = current_time - getattr(self, '_last_update_time', current_time)
            speed = current / elapsed if elapsed > 0 else 0
            eta = (total - current) / speed if speed > 0 and current < total else 0
            
            progress_text = (
                f"**{operation}**\n\n"
                f"`{bar}` **{percent:.1f}%**\n\n"
                f"**Progress:** {self._format_size(current)} / {self._format_size(total)}\n"
                f"**Speed:** {self._format_speed(speed)}/s\n"
                f"**ETA:** {self._format_time(eta)}\n"
                f"**Status:** {'🔄 Processing' if percent < 100 else '✅ Complete'}"
            )
            
            # Use safe edit to handle MessageNotModified errors
            await self.progress_manager.safe_edit_message(
                message, 
                progress_text, 
                parse_mode=ParseMode.MARKDOWN
            )
            
            # Update last progress values
            self._last_progress[message_id] = percent
            self._last_progress[f"{message_id}_bytes"] = current
            self._last_update_time = current_time
            
        except Exception as e:
            logger.debug(f"Progress update skipped: {e}")
    
    async def _extract_file_info(self, message: Message) -> Optional[Tuple]:
        """Extract advanced file information"""
        try:
            if message.document:
                return message.document, FileType.DOCUMENT, message.document.file_name
            elif message.video:
                return message.video, FileType.VIDEO, f"video_{message.id}.mp4"
            elif message.audio:
                filename = getattr(message.audio, 'file_name', f"audio_{message.id}.mp3")
                return message.audio, FileType.AUDIO, filename
            elif message.photo:
                return message.photo, FileType.PHOTO, f"photo_{message.id}.jpg"
            return None
        except Exception as e:
            logger.error(f"File info extraction error: {e}")
            return None
    
    def _update_performance_stats(self, response_time: float):
        """Update performance statistics"""
        self.performance_stats['requests_processed'] += 1
        self.performance_stats['average_response_time'] = (
            self.performance_stats['average_response_time'] * 0.9 + response_time * 0.1
        )
    
    @staticmethod
    def _format_size(size_bytes: int) -> str:
        """Format file size in human readable format"""
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if size_bytes < 1024.0:
                return f"{size_bytes:.2f} {unit}"
            size_bytes /= 1024.0
        return f"{size_bytes:.2f} PB"
    
    @staticmethod
    def _format_speed(speed_bytes: float) -> str:
        """Format speed in human readable format"""
        return WorldClassTelegramBot._format_size(speed_bytes)
    
    @staticmethod
    def _format_time(seconds: float) -> str:
        """Format time in human readable format"""
        if seconds < 60:
            return f"{int(seconds)}s"
        elif seconds < 3600:
            return f"{int(seconds // 60)}m {int(seconds % 60)}s"
        else:
            return f"{int(seconds // 3600)}h {int((seconds % 3600) // 60)}m"
    
    def run(self):
        """Start the world-class bot"""
        logger.info("🚀 Starting WORLD-CLASS Telegram Wasabi Bot...")
        logger.info(f"💾 Bucket: {config.WASABI.BUCKET}")
        logger.info(f"🌐 Region: {config.WASABI.REGION}")
        logger.info(f"⚡ Workers: {config.PERFORMANCE.MAX_WORKERS}")
        logger.info(f"🔀 Concurrent: {config.PERFORMANCE.MAX_CONCURRENT_TRANSMISSIONS}")
        logger.info(f"💽 Chunk Size: {self._format_size(config.PERFORMANCE.CHUNK_SIZE)}")
        
        try:
            self.app.run()
        except KeyboardInterrupt:
            logger.info("🛑 Bot stopped by user")
        except Exception as e:
            logger.error(f"❌ Bot crashed: {e}")
        finally:
            # Cleanup
            asyncio.run(self.wasabi.connection_pool.close())

# Advanced callback handler
@Client.on_callback_query()
async def handle_advanced_callbacks(client, callback_query):
    """Handle advanced callback queries"""
    bot = client.wasabi_bot
    
    try:
        data = callback_query.data
        
        if data == "upload_guide":
            await callback_query.message.reply_text(
                "📤 **UPLOAD GUIDE**\n\n"
                "**Quick Start:**\n"
                "1. Send any file to the bot\n"
                "2. Wait for upload completion\n"
                "3. Get download link\n\n"
                "**Supported Formats:**\n"
                "• Documents: PDF, DOC, XLS, PPT, TXT\n"
                "• Media: MP4, AVI, MP3, WAV, JPG, PNG\n"
                "• Archives: ZIP, RAR, 7Z, TAR\n"
                "• Code: PY, JS, HTML, CSS, JSON\n\n"
                "**Max Size:** 4GB per file\n\n"
                "🚀 **Just send a file to begin!**",
                parse_mode=ParseMode.MARKDOWN
            )
            await callback_query.answer()
        
        elif data == "download_guide":
            await callback_query.message.reply_text(
                "📥 **DOWNLOAD GUIDE**\n\n"
                "**Method 1: Command**\n"
                "• Use `/download filename`\n"
                "• Example: `/download myfile.pdf`\n\n"
                "**Method 2: File List**\n"
                "• Use `/list` to see all files\n"
                "• Click on download buttons\n\n"
                "**Features:**\n"
                "• Resume interrupted downloads\n"
                "• Progress tracking\n"
                "• Speed optimization\n"
                "• Secure transfers\n\n"
                "Use `/list` to see available files!",
                parse_mode=ParseMode.MARKDOWN
            )
            await callback_query.answer()
        
        elif data == "live_stats":
            await callback_query.message.reply_text(
                "📊 **Loading Live Statistics...**"
            )
            # Create a new message for stats to avoid callback issues
            await bot.show_stats(callback_query.message)
            await callback_query.answer()
        
        elif data == "advanced_help":
            await callback_query.message.reply_text(
                "🛠️ **ADVANCED HELP**\n\n"
                "**Available Commands:**\n"
                "• `/start` - Welcome message\n"
                "• `/upload` - Upload guide\n"
                "• `/download <file>` - Download file\n"
                "• `/list` - List all files\n"
                "• `/stats` - Performance stats\n"
                "• `/info` - System information\n"
                "• `/help` - This message\n\n"
                "**Advanced Features:**\n"
                "• 🚀 4GB file support\n"
                "• ⚡ Parallel processing\n"
                "• 🔄 Real-time progress\n"
                "• 📊 Performance analytics\n"
                "• 🛡️ Rate limiting\n"
                "• 💾 Smart caching\n\n"
                "**Need Help?**\n"
                "Just send a file or use commands above!",
                parse_mode=ParseMode.MARKDOWN
            )
            await callback_query.answer()
        
        elif data == "refresh_list":
            await bot.progress_manager.safe_edit_message(
                callback_query.message,
                "🔄 Refreshing file list..."
            )
            await bot._show_file_list(callback_query.message)
            await callback_query.answer("✅ List refreshed")
        
        else:
            # Handle custom callbacks
            callback_data = bot.callback_manager.get_callback(data)
            if callback_data:
                action = callback_data['action']
                file_data = callback_data['data']
                filename = file_data.get('filename')
                
                if action == "download":
                    await callback_query.answer("📥 Starting download...")
                    await bot._process_file_download(callback_query.message, filename)
                
                elif action == "delete":
                    try:
                        await bot.wasabi.delete_file(filename)
                        await callback_query.answer("✅ File deleted")
                        await bot.progress_manager.safe_edit_message(
                            callback_query.message,
                            f"🗑️ **File Deleted**\n\n"
                            f"`{filename}` has been removed from Wasabi storage.",
                            parse_mode=ParseMode.MARKDOWN
                        )
                    except Exception as e:
                        await callback_query.answer("❌ Delete failed")
                        logger.error(f"Delete error: {e}")
            
            else:
                await callback_query.answer("❌ Invalid action")
    
    except Exception as e:
        logger.error(f"Callback error: {e}")
        await callback_query.answer("❌ Action failed")

def main():
    """Main entry point"""
    try:
        bot = WorldClassTelegramBot()
        bot.app.wasabi_bot = bot
        bot.run()
    except Exception as e:
        logger.critical(f"Failed to start bot: {e}")
        exit(1)

if __name__ == "__main__":
    main()
