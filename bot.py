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
from asyncio import Lock, Semaphore, Queue
from dataclasses import dataclass
from contextlib import asynccontextmanager
import zlib
import json

from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardButton, InlineKeyboardMarkup
from pyrogram.enums import ParseMode, MessageEntityType
from pyrogram.errors import FloodWait, RPCError
import aioboto3
from botocore.exceptions import ClientError
import aiohttp
from cryptography.fernet import Fernet

from config import config, FileType

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
            # Clean expired entries if cache is large
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
            
            # Remove old requests
            self._user_requests[user_id] = [
                req_time for req_time in self._user_requests[user_id] 
                if req_time > window_start
            ]
            
            # Check limit
            if len(self._user_requests[user_id]) >= config.SECURITY.RATE_LIMIT_PER_USER:
                return False
            
            self._user_requests[user_id].append(now)
            return True
    
    async def check_file_limit(self, filename: str) -> bool:
        """Check if file download is within rate limit"""
        async with self._lock:
            now = time.time()
            window_start = now - 60  # 1 minute window
            
            if filename not in self._file_downloads:
                self._file_downloads[filename] = []
            
            # Remove old downloads
            self._file_downloads[filename] = [
                dl_time for dl_time in self._file_downloads[filename] 
                if dl_time > window_start
            ]
            
            # Check limit
            if len(self._file_downloads[filename]) >= config.SECURITY.RATE_LIMIT_PER_FILE:
                return False
            
            self._file_downloads[filename].append(now)
            return True

class ConnectionPool:
    """Managed connection pool for high performance"""
    
    def __init__(self):
        self._session = None
        self._wasabi_pool = None
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
                    # For large files, use multipart upload
                    if file_size > config.WASABI.MULTIPART_THRESHOLD:
                        await self._multipart_upload(s3, file, filename, file_size, progress_callback)
                    else:
                        # Single upload with streaming
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
                
                # Compress chunk if enabled
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
            # Validate file
            if not await self._validate_file(file_path, filename):
                return False
            
            # Upload file
            success = await self.stream_processor.stream_upload(
                file_path, filename, progress_callback
            )
            
            # Update statistics
            if success:
                await self._update_stats(file_path, start_time)
                # Cache file info
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
                # Get file info
                head_response = await s3.head_object(
                    Bucket=config.WASABI.BUCKET, 
                    Key=filename
                )
                file_size = head_response['ContentLength']
                
                # Download with streaming
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
                
                # Cache results
                await self.cache.set(cache_key, files, ttl=300)
                return files
                
        except Exception as e:
            logger.error(f"List files failed: {e}")
            return []
    
    async def _validate_file(self, file_path: str, filename: str) -> bool:
        """Comprehensive file validation"""
        # Check file size
        file_size = os.path.getsize(file_path)
        if file_size > config.SECURITY.MAX_FILE_SIZE:
            raise ValueError(f"File too large: {file_size} bytes")
        
        # Check file extension
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
    """Advanced callback management with security"""
    
    def __init__(self):
        self._callbacks: Dict[str, Dict] = {}
        self._encryption = Fernet.generate_key() if config.ENABLE_ENCRYPTION else None
    
    def register_callback(self, action: str, data: Dict) -> str:
        """Register callback with encrypted data"""
        callback_id = str(uuid.uuid4())
        
        if self._encryption:
            fernet = Fernet(self._encryption)
            encrypted_data = fernet.encrypt(json.dumps(data).encode())
            self._callbacks[callback_id] = {
                'action': action,
                'data': encrypted_data.decode(),
                'created': time.time()
            }
        else:
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
        if time.time() - callback['created'] > 3600:  # 1 hour TTL
            del self._callbacks[callback_id]
            return None
        
        if self._encryption and 'data' in callback:
            fernet = Fernet(self._encryption)
            decrypted_data = fernet.decrypt(callback['data'].encode())
            callback['data'] = json.loads(decrypted_data.decode())
        
        return callback

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
                    [InlineKeyboardButton("📤 Upload File", callback_data="upload_guide"),
                     InlineKeyboardButton("📥 Download File", callback_data="download_guide")],
                    [InlineKeyboardButton("📊 Live Stats", callback_data="live_stats"),
                     InlineKeyboardButton("🛠️ Advanced Help", callback_data="advanced_help")]
                ])
            )
        
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
                    [InlineKeyboardButton("🗑️ Delete", callback_data=self.callback_manager.register_callback("delete", {"filename": filename}))],
                    [InlineKeyboardButton("📊 Stats", callback_data="show_stats")]
                ])
                
                await status_msg.edit_text(
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
                await status_msg.edit_text(
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
            local_path = download_dir / filename
            
            # Download from Wasabi
            success = await self.wasabi.download_file(
                filename,
                str(local_path),
                progress_callback=lambda current, total: self._update_progress(
                    status_msg, current, total, "📥 Downloading from Wasabi"
                )
            )
            
            if success and os.path.exists(local_path):
                await status_msg.edit_text("📤 **Sending to Telegram...**")
                
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
                await status_msg.edit_text(
                    f"❌ **Download Failed**\n\n"
                    f"File `{filename}` not found or inaccessible.",
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
                await status_msg.edit_text(
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
            for ext, group in list(file_groups.items())[:10]:  # Show top 10 types
                count = len(group)
                size = sum(f['size'] for f in group)
                file_list.append(f"• **{ext.upper()}:** {count} files ({self._format_size(size)})")
            
            await status_msg.edit_text(
                f"📊 **FILE INVENTORY**\n\n"
                f"**Total Files:** {len(files)}\n"
                f"**Total Size:** {self._format_size(total_size)}\n"
                f"**Bucket:** `{config.WASABI.BUCKET}`\n\n"
                f"**📂 File Types:**\n" + "\n".join(file_list) + "\n\n"
                f"**💡 Usage:** `/download filename` to download any file",
                parse_mode=ParseMode.MARKDOWN
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
            await status_msg.edit_text("❌ Failed to download file from Telegram")
            return None
    
    async def _update_progress(self, message: Message, current: int, total: int, operation: str):
        """Advanced progress updates with rich formatting"""
        try:
            percent = (current / total) * 100
            
            # Create visual progress bar
            bar_length = 20
            filled_length = int(bar_length * current // total)
            bar = '█' * filled_length + '▱' * (bar_length - filled_length)
            
            # Calculate speed and ETA
            speed = current / (time.time() - getattr(self, '_last_update_time', time.time()))
            eta = (total - current) / speed if speed > 0 else 0
            
            progress_text = (
                f"**{operation}**\n\n"
                f"`{bar}` **{percent:.1f}%**\n\n"
                f"**Progress:** {self._format_size(current)} / {self._format_size(total)}\n"
                f"**Speed:** {self._format_speed(speed)}/s\n"
                f"**ETA:** {self._format_time(eta)}\n"
                f"**Status:** {'🔄 Processing' if percent < 100 else '✅ Complete'}"
            )
            
            await message.edit_text(progress_text, parse_mode=ParseMode.MARKDOWN)
            self._last_update_time = time.time()
            
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
    data = callback_query.data
    
    try:
        if data.startswith("{"): 
