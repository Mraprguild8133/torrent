import os
import time
import asyncio
import logging
import base64
import threading
import queue
import hashlib
import re
from functools import wraps
from urllib.parse import quote
from typing import Optional
from collections import defaultdict
from datetime import datetime, timedelta

import boto3
from botocore.exceptions import ClientError, NoCredentialsError
from botocore.client import Config

from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import FloodWait, RPCError

from config import config

# --- Configuration & Logging ---
logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Configuration constants
API_ID = config.API_ID
API_HASH = config.API_HASH
BOT_TOKEN = config.BOT_TOKEN
WASABI_ACCESS_KEY = config.WASABI_ACCESS_KEY
WASABI_SECRET_KEY = config.WASABI_SECRET_KEY
WASABI_BUCKET = config.WASABI_BUCKET
WASABI_REGION = config.WASABI_REGION
ADMIN_ID = config.ADMIN_ID
MAX_FILE_SIZE = config.MAX_FILE_SIZE
DOWNLOAD_DIR = config.DOWNLOAD_DIR
RENDER_URL = config.RENDER_URL

# Supported formats with enhanced support
SUPPORTED_VIDEO_FORMATS = {
    '.mp4', '.mkv', '.avi', '.mov', '.wmv', '.flv', 
    '.webm', '.m4v', '.3gp', '.mpeg', '.mpg', '.ts'
}

SUPPORTED_AUDIO_FORMATS = {
    '.mp3', '.m4a', '.flac', '.wav', '.aac', '.ogg', '.wma'
}

SUPPORTED_IMAGE_FORMATS = {
    '.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp'
}

# Media type mapping
MEDIA_EXTENSIONS = {
    'video': SUPPORTED_VIDEO_FORMATS,
    'audio': SUPPORTED_AUDIO_FORMATS,
    'image': SUPPORTED_IMAGE_FORMATS
}

# User management
ALLOWED_USERS = {ADMIN_ID}

# Rate limiting
user_requests = defaultdict(list)

# Global clients
app = None
s3_client = None

# Callback data management
callback_store = {}

# Ensure download directory exists
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# --- Helper Functions ---
def humanbytes(size: float) -> str:
    """Convert bytes to human readable format."""
    if not size or size == 0:
        return "0 B"
    
    size = int(size)
    power = 1024
    power_labels = {0: 'B', 1: 'KB', 2: 'MB', 3: 'GB', 4: 'TB'}
    
    n = 0
    while size >= power and n < len(power_labels) - 1:
        size /= power
        n += 1
        
    return f"{size:.2f} {power_labels[n]}"

def get_file_extension(filename: str) -> str:
    """Extract file extension in lowercase."""
    return os.path.splitext(filename)[1].lower()

def get_file_type(filename: str) -> str:
    """Get file type (video, audio, image, other)."""
    ext = get_file_extension(filename)
    for file_type, extensions in MEDIA_EXTENSIONS.items():
        if ext in extensions:
            return file_type
    return 'other'

def is_video_file(filename: str) -> bool:
    """Check if file is a supported video format."""
    return get_file_extension(filename) in SUPPORTED_VIDEO_FORMATS

def is_audio_file(filename: str) -> bool:
    """Check if file is a supported audio format."""
    return get_file_extension(filename) in SUPPORTED_AUDIO_FORMATS

def is_image_file(filename: str) -> bool:
    """Check if file is a supported image format."""
    return get_file_extension(filename) in SUPPORTED_IMAGE_FORMATS

def generate_player_url(filename: str, presigned_url: str) -> Optional[str]:
    """Generate player URL for supported file types."""
    if not RENDER_URL:
        return None
    
    file_type = get_file_type(filename)
    if file_type in ['video', 'audio', 'image']:
        encoded_url = base64.urlsafe_b64encode(presigned_url.encode()).decode().rstrip('=')
        return f"{RENDER_URL}/player/{file_type}/{encoded_url}"
    return None

def generate_streaming_link(file_name: str) -> str:
    """Generate public streaming link for the file."""
    encoded_file_name = quote(file_name)
    return f"https://{WASABI_BUCKET}.s3.{WASABI_REGION}.wasabisys.com/{encoded_file_name}"

def generate_callback_key(data: str) -> str:
    """Generate a short callback key from data."""
    return hashlib.md5(data.encode()).hexdigest()[:16]

def store_callback_data(key: str, data: str):
    """Store callback data with key."""
    callback_store[key] = data
    # Clean old entries (keep only last 1000)
    if len(callback_store) > 1000:
        for old_key in list(callback_store.keys())[:100]:
            del callback_store[old_key]

def get_callback_data(key: str) -> Optional[str]:
    """Get stored callback data."""
    return callback_store.get(key)

def sanitize_filename(filename: str) -> str:
    """Remove potentially dangerous characters from filenames."""
    filename = re.sub(r'[^a-zA-Z0-9 _.-]', '_', filename)
    if len(filename) > 200:
        name, ext = os.path.splitext(filename)
        filename = name[:200-len(ext)] + ext
    return filename

def get_user_folder(user_id: int) -> str:
    """Get user-specific folder name."""
    return f"user_{user_id}"

def create_progress_bar(percentage: float, length: int = 20) -> str:
    """Create a visual progress bar."""
    filled = int(length * percentage / 100)
    empty = length - filled
    return '█' * filled + '○' * empty

def format_eta(seconds: float) -> str:
    """Format seconds into human readable ETA."""
    if seconds <= 0:
        return "00:00"
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours > 0:
        return f"{int(hours):02d}:{int(minutes):02d}:{int(seconds):02d}"
    return f"{int(minutes):02d}:{int(seconds):02d}"

def format_elapsed(seconds: float) -> str:
    """Format elapsed time."""
    return f"{int(seconds // 60):02d}:{int(seconds % 60):02d}"

def is_rate_limited(user_id: int, limit: int = 5, period: int = 60) -> bool:
    """Check if user is rate limited."""
    now = datetime.now()
    user_requests[user_id] = [req_time for req_time in user_requests[user_id] if now - req_time < timedelta(seconds=period)]
    
    if len(user_requests[user_id]) >= limit:
        return True
    
    user_requests[user_id].append(now)
    return False

# --- Decorators ---
def is_admin(func):
    """Decorator to check if the user is the admin."""
    @wraps(func)
    async def wrapper(client, message):
        if message.from_user.id == ADMIN_ID:
            await func(client, message)
        else:
            await message.reply_text("⛔️ Access denied. This command is for the admin only.")
    return wrapper

def is_authorized(func):
    """Decorator to check if the user is authorized."""
    @wraps(func)
    async def wrapper(client, message):
        if message.from_user.id in ALLOWED_USERS:
            await func(client, message)
        else:
            await message.reply_text(
                "⛔️ You are not authorized to use this bot.\n\n"
                f"Your User ID: `{message.from_user.id}`\n"
                "Contact the admin for access."
            )
    return wrapper

def rate_limit(limit: int = 5, period: int = 60):
    """Decorator for rate limiting."""
    def decorator(func):
        @wraps(func)
        async def wrapper(client, message):
            if is_rate_limited(message.from_user.id, limit, period):
                await message.reply_text("⏳ Too many requests. Please try again in a minute.")
                return
            await func(client, message)
        return wrapper
    return decorator

# --- Progress Tracking ---
class ProgressTracker:
    """Track upload/download progress with rate limiting and enhanced visuals."""
    def __init__(self, message: Message, action: str):
        self.message = message
        self.action = action
        self.start_time = time.time()
        self.last_update_time = 0
        self.update_interval = 2  # Update every 2 seconds
        self.last_processed_bytes = 0
        self.last_speed_calc_time = time.time()

    async def update(self, current: int, total: int):
        """Update progress message with rate limiting and speed calculation."""
        current_time = time.time()
        if current_time - self.last_update_time < self.update_interval and current != total:
            return

        # Calculate speed with smoothing
        time_diff = current_time - self.last_speed_calc_time
        if time_diff > 0:
            speed = (current - self.last_processed_bytes) / time_diff
            self.last_processed_bytes = current
            self.last_speed_calc_time = current_time
        else:
            speed = 0

        elapsed_time = current_time - self.start_time
        percentage = (current / total) * 100 if total > 0 else 0
        
        # Enhanced progress bar
        progress_bar = create_progress_bar(percentage)
        
        # ETA calculation
        eta = (total - current) / speed if speed > 0 else 0
        
        progress_message = (
            f"**{self.action} in Progress...**\n"
            f"`[{progress_bar}] {percentage:.2f}%`\n"
            f"**Speed:** `{humanbytes(speed)}/s`\n"
            f"**Transferred:** `{humanbytes(current)} / {humanbytes(total)}`\n"
            f"**Time Elapsed:** `{format_elapsed(elapsed_time)}`\n"
            f"**ETA:** `{format_eta(eta)}`"
        )
        
        try:
            await self.message.edit_text(progress_message)
            self.last_update_time = current_time
        except FloodWait as e:
            await asyncio.sleep(e.value)
        except Exception as e:
            logger.debug(f"Progress update skipped: {e}")

# --- Wasabi Operations ---
def initialize_wasabi_client():
    """Initialize Wasabi S3 client with enhanced error handling."""
    global s3_client
    try:
        # Primary endpoint format
        s3_client = boto3.client(
            's3',
            endpoint_url=f'https://s3.{WASABI_REGION}.wasabisys.com',
            aws_access_key_id=WASABI_ACCESS_KEY,
            aws_secret_access_key=WASABI_SECRET_KEY,
            region_name=WASABI_REGION,
            config=Config(
                s3={'addressing_style': 'virtual'},
                retries={'max_attempts': 3, 'mode': 'standard'},
                signature_version='s3v4'
            )
        )
        # Test connection
        s3_client.head_bucket(Bucket=WASABI_BUCKET)
        logger.info("✅ Successfully connected to Wasabi.")
        return True
    except Exception as e:
        logger.warning(f"Primary connection failed: {e}")
        
        # Try alternative endpoint format
        try:
            wasabi_endpoint_url = f'https://{WASABI_BUCKET}.s3.{WASABI_REGION}.wasabisys.com'
            s3_client = boto3.client(
                's3',
                endpoint_url=wasabi_endpoint_url,
                aws_access_key_id=WASABI_ACCESS_KEY,
                aws_secret_access_key=WASABI_SECRET_KEY,
                region_name=WASABI_REGION
            )
            s3_client.head_bucket(Bucket=WASABI_BUCKET)
            logger.info("✅ Successfully connected to Wasabi with alternative endpoint.")
            return True
        except Exception as alt_e:
            logger.error(f"❌ All connection attempts failed: {alt_e}")
            s3_client = None
            return False

async def generate_presigned_url(file_name: str, expires_in: int = 604800) -> Optional[str]:
    """Generate presigned URL with error handling."""
    try:
        return s3_client.generate_presigned_url(
            'get_object',
            Params={'Bucket': WASABI_BUCKET, 'Key': file_name},
            ExpiresIn=expires_in  # 7 days default
        )
    except ClientError as e:
        logger.error(f"Failed to generate presigned URL: {e}")
        return None

def upload_to_wasabi_sync(file_path: str, file_name: str, progress_queue: queue.Queue):
    """Upload file to Wasabi storage (synchronous version for threading)."""
    try:
        file_size = os.path.getsize(file_path)
        uploaded = 0
        
        def upload_progress(bytes_amount):
            nonlocal uploaded
            uploaded += bytes_amount
            progress_queue.put(uploaded)
        
        s3_client.upload_file(
            file_path,
            WASABI_BUCKET,
            file_name,
            Callback=upload_progress
        )
        progress_queue.put(-1)  # Signal completion
    except Exception as e:
        progress_queue.put(("error", str(e)))

async def upload_to_wasabi(file_path: str, file_name: str, file_size: int, status_message: Message) -> bool:
    """Upload file to Wasabi storage with progress tracking."""
    if not s3_client:
        await status_message.edit_text("❌ Wasabi client not configured")
        return False

    progress_tracker = ProgressTracker(status_message, "Uploading to Wasabi")
    
    try:
        progress_queue = queue.Queue()
        
        # Start upload in a separate thread
        thread = threading.Thread(
            target=upload_to_wasabi_sync,
            args=(file_path, file_name, progress_queue)
        )
        thread.daemon = True
        thread.start()
        
        # Monitor progress
        bytes_uploaded = 0
        timeout = 300  # 5 minute timeout
        
        while thread.is_alive():
            try:
                data = progress_queue.get(timeout=1.0)
                
                if data == -1:
                    break
                elif isinstance(data, tuple) and data[0] == "error":
                    raise Exception(data[1])
                else:
                    bytes_uploaded = data
                    await progress_tracker.update(bytes_uploaded, file_size)
                    
            except queue.Empty:
                continue
                
        thread.join(timeout=5)
        
        if thread.is_alive():
            await status_message.edit_text("❌ Upload timeout")
            return False
            
        return True
        
    except Exception as e:
        await status_message.edit_text(f"❌ Upload failed: {str(e)}")
        return False

def create_download_keyboard(presigned_url: str, player_url: Optional[str] = None, filename: str = None) -> InlineKeyboardMarkup:
    """Create inline keyboard with download options."""
    keyboard = []
    
    if player_url:
        keyboard.append([InlineKeyboardButton("🎬 Web Player", url=player_url)])
    
    if presigned_url:
        keyboard.append([InlineKeyboardButton("📥 Direct Download", url=presigned_url)])
    
    # Add copy and info buttons if filename is provided
    if filename and presigned_url:
        url_key = generate_callback_key(f"url_{filename}")
        store_callback_data(url_key, presigned_url)
        
        info_key = generate_callback_key(f"info_{filename}")
        store_callback_data(info_key, filename)
        
        keyboard.append([
            InlineKeyboardButton("📋 Copy URL", callback_data=f"copy_{url_key}"),
            InlineKeyboardButton("🔍 File Info", callback_data=f"info_{info_key}")
        ])
    
    return InlineKeyboardMarkup(keyboard)

# --- Bot Command Handlers ---
def register_handlers(client):
    """Register all message handlers."""
    
    @client.on_message(filters.command("start"))
    @rate_limit()
    async def start_handler(_, message: Message):
        welcome_text = (
            f"👋 **Welcome to Wasabi Storage Bot!**\n\n"
            f"**Your User ID:** `{message.from_user.id}`\n\n"
            "Send me any file to upload to secure Wasabi storage and get shareable links.\n\n"
            "**Features:**\n"
            "• Direct download links\n"
            "• Video/Audio/Image player URLs\n"
            "• Real-time progress tracking\n"
            "• 7-day link validity\n"
            f"• Support for files up to {humanbytes(MAX_FILE_SIZE)}\n"
            "• User-specific file organization\n\n"
            "**Owner:** Mraprguild\n"
            "**Email:** mraprguild@gmail.com\n"
            "**Telegram:** @Sathishkumar33"
        )
        
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📚 Help", callback_data="help"),
             InlineKeyboardButton("🔧 Admin", callback_data="admin")],
            [InlineKeyboardButton("📊 Status", callback_data="status"),
             InlineKeyboardButton("👥 Users", callback_data="users")],
            [InlineKeyboardButton("📁 My Files", callback_data="list_files"),
             InlineKeyboardButton("👨‍💻 Developer", url="https://t.me/tprojects")]
        ])
        
        await message.reply_text(welcome_text, reply_markup=keyboard)

    @client.on_message(filters.command("help"))
    @rate_limit()
    async def help_handler(_, message: Message):
        help_text = """
🤖 **Wasabi Upload Bot Help**

**For Users:**
• Just send any file to upload
• Get direct download links
• Video/Audio/Image files get player URLs
• All links are valid for 7 days
• Your files are organized in user-specific folders

**Available Commands:**
• `/start` - Start the bot
• `/help` - Show this help
• `/download <filename>` - Get download link for existing file
• `/play <filename>` - Get player URL for existing file
• `/list` - List your uploaded files
• `/delete <filename>` - Delete a file

**For Admin:**
• `/adduser <user_id>` - Add authorized user
• `/removeuser <user_id>` - Remove user
• `/listusers` - Show authorized users
• `/stats` - Bot statistics

**Supported Formats:**
• **Videos:** MP4, MKV, AVI, MOV, WMV, FLV, WebM, M4V, 3GP, MPEG, MPG, TS
• **Audio:** MP3, M4A, FLAC, WAV, AAC, OGG, WMA
• **Images:** JPG, JPEG, PNG, GIF, BMP, WebP
• **Documents:** PDF, ZIP, RAR, and more

**Player URLs:**
Media files get special player URLs that work in browsers.
"""
        await message.reply_text(help_text)

    @client.on_message(filters.command("adduser"))
    @is_admin
    @rate_limit()
    async def add_user_handler(_, message: Message):
        try:
            user_id_to_add = int(message.text.split(" ", 1)[1])
            ALLOWED_USERS.add(user_id_to_add)
            await message.reply_text(f"✅ User `{user_id_to_add}` has been added successfully.")
            logger.info(f"Admin {message.from_user.id} added user {user_id_to_add}")
        except (IndexError, ValueError):
            await message.reply_text("⚠️ **Usage:** /adduser `<user_id>`")

    @client.on_message(filters.command("removeuser"))
    @is_admin
    @rate_limit()
    async def remove_user_handler(_, message: Message):
        try:
            user_id_to_remove = int(message.text.split(" ", 1)[1])
            if user_id_to_remove == ADMIN_ID:
                await message.reply_text("🚫 You cannot remove the admin.")
                return
            if user_id_to_remove in ALLOWED_USERS:
                ALLOWED_USERS.remove(user_id_to_remove)
                await message.reply_text(f"🗑 User `{user_id_to_remove}` has been removed.")
                logger.info(f"Admin {message.from_user.id} removed user {user_id_to_remove}")
            else:
                await message.reply_text("🤷 User not found in the authorized list.")
        except (IndexError, ValueError):
            await message.reply_text("⚠️ **Usage:** /removeuser `<user_id>`")

    @client.on_message(filters.command("listusers"))
    @is_admin
    @rate_limit()
    async def list_users_handler(_, message: Message):
        user_list = "\n".join([f"• `{user_id}`" for user_id in ALLOWED_USERS])
        await message.reply_text(f"👥 **Authorized Users ({len(ALLOWED_USERS)}):**\n{user_list}")

    @client.on_message(filters.command("stats"))
    @is_admin
    @rate_limit()
    async def stats_handler(_, message: Message):
        stats_text = (
            f"🤖 **Bot Statistics**\n\n"
            f"**Users:**\n"
            f"• Authorized users: {len(ALLOWED_USERS)}\n"
            f"• Admin ID: `{ADMIN_ID}`\n\n"
            f"**Storage:**\n"
            f"• Wasabi connected: {'✅' if s3_client else '❌'}\n"
            f"• Bucket: `{WASABI_BUCKET}`\n"
            f"• Region: `{WASABI_REGION}`\n\n"
            f"**Configuration:**\n"
            f"• Max file size: {humanbytes(MAX_FILE_SIZE)}\n"
            f"• Player URL: {RENDER_URL or 'Not set'}\n"
            f"• Download dir: `{DOWNLOAD_DIR}`"
        )
        await message.reply_text(stats_text)

    @client.on_message(filters.command("download"))
    @is_authorized
    @rate_limit()
    async def download_file_handler(_, message: Message):
        """Generate download link for existing files"""
        try:
            filename = message.text.split(" ", 1)[1].strip()
            user_file_name = f"{get_user_folder(message.from_user.id)}/{filename}"
            
            try:
                # Check if file exists
                s3_client.head_object(Bucket=WASABI_BUCKET, Key=user_file_name)
                
                # Generate URLs
                presigned_url = await generate_presigned_url(user_file_name)
                streaming_link = generate_streaming_link(user_file_name)
                player_url = generate_player_url(filename, presigned_url) if presigned_url else None
                
                if presigned_url:
                    keyboard = create_download_keyboard(presigned_url, player_url, user_file_name)
                    
                    response_text = (
                        f"📥 **Download Ready**\n\n"
                        f"**File:** `{filename}`\n"
                        f"**Links valid for:** 7 days\n\n"
                        f"*Use the buttons below to access your file*"
                    )
                    
                    await message.reply_text(
                        response_text,
                        reply_markup=keyboard
                    )
                else:
                    await message.reply_text("❌ Could not generate download URL for the file.")
                    
            except ClientError as e:
                if e.response['Error']['Code'] == '404':
                    await message.reply_text(f"❌ File `{filename}` not found in your storage.")
                else:
                    await message.reply_text(f"❌ Error accessing file: {e.response['Error']['Message']}")
                    
        except IndexError:
            await message.reply_text("⚠️ **Usage:** /download `<filename>`\n**Example:** `/download myvideo.mp4`")

    @client.on_message(filters.command("play"))
    @is_authorized
    @rate_limit()
    async def play_file_handler(_, message: Message):
        """Generate player URL for existing files"""
        try:
            filename = message.text.split(" ", 1)[1].strip()
            user_file_name = f"{get_user_folder(message.from_user.id)}/{filename}"
            
            try:
                s3_client.head_object(Bucket=WASABI_BUCKET, Key=user_file_name)
                
                presigned_url = await generate_presigned_url(user_file_name)
                if presigned_url:
                    player_url = generate_player_url(filename, presigned_url)
                    
                    if player_url:
                        keyboard = InlineKeyboardMarkup([
                            [InlineKeyboardButton("🎥 Open Player", url=player_url)],
                            [InlineKeyboardButton("🔗 Direct Link", url=presigned_url)]
                        ])
                        
                        await message.reply_text(
                            f"🎥 **Player URL for `{filename}`**\n\n"
                            f"**Player:** {player_url}\n"
                            f"**Direct:** `{presigned_url}`\n\n"
                            f"*Links valid for 7 days*",
                            disable_web_page_preview=False,
                            reply_markup=keyboard
                        )
                    else:
                        await message.reply_text(
                            f"⚠️ `{filename}` is not a supported media format for playback.\n"
                            f"**Supported:** Videos, Audio, Images"
                        )
                else:
                    await message.reply_text("❌ Could not generate presigned URL for the file.")
                    
            except ClientError as e:
                if e.response['Error']['Code'] == '404':
                    await message.reply_text(f"❌ File `{filename}` not found in your storage.")
                else:
                    await message.reply_text(f"❌ Error accessing file: {e.response['Error']['Message']}")
                    
        except IndexError:
            await message.reply_text("⚠️ **Usage:** /play `<filename>`\n**Example:** `/play myvideo.mp4`")

    @client.on_message(filters.command("list"))
    @is_authorized
    @rate_limit()
    async def list_files_handler(_, message: Message):
        """List user's uploaded files"""
        try:
            user_prefix = get_user_folder(message.from_user.id) + "/"
            response = s3_client.list_objects_v2(
                Bucket=WASABI_BUCKET, 
                Prefix=user_prefix
            )
            
            if 'Contents' not in response:
                await message.reply_text("📭 No files found in your storage.")
                return
            
            files = [obj['Key'].replace(user_prefix, "") for obj in response['Contents']]
            files_list = "\n".join([f"• {file}" for file in files[:15]])  # Show first 15 files
            
            if len(files) > 15:
                files_list += f"\n\n...and {len(files) - 15} more files"
            
            await message.reply_text(f"📁 **Your Files ({len(files)}):**\n\n{files_list}")
        
        except Exception as e:
            logger.error(f"List files error: {e}")
            await message.reply_text(f"❌ Error listing files: {str(e)}")

    @client.on_message(filters.command("delete"))
    @is_authorized
    @rate_limit()
    async def delete_file_handler(_, message: Message):
        """Delete a file from storage"""
        try:
            filename = message.text.split(" ", 1)[1].strip()
            user_file_name = f"{get_user_folder(message.from_user.id)}/{filename}"
            
            try:
                # Delete file from Wasabi
                s3_client.delete_object(
                    Bucket=WASABI_BUCKET,
                    Key=user_file_name
                )
                
                await message.reply_text(f"✅ **Deleted:** `{filename}`")
                logger.info(f"User {message.from_user.id} deleted file: {filename}")
            
            except ClientError as e:
                if e.response['Error']['Code'] == '404':
                    await message.reply_text(f"❌ File `{filename}` not found in your storage.")
                else:
                    await message.reply_text(f"❌ Error deleting file: {e.response['Error']['Message']}")
                    
        except IndexError:
            await message.reply_text("⚠️ **Usage:** /delete `<filename>`\n**Example:** `/delete myfile.mp4`")

    # --- File Handling ---
    @client.on_message(filters.document | filters.video | filters.audio | filters.photo)
    @is_authorized
    @rate_limit(limit=3, period=60)  # More restrictive for file uploads
    async def file_handler(client, message: Message):
        if not s3_client:
            await message.reply_text("❌ **Error:** Wasabi client is not initialized. File uploads are disabled.")
            return

        media = message.document or message.video or message.audio or message.photo
        if message.photo:
            # For photos, we'll use the largest size and create a filename
            file_size = message.photo.sizes[-1].file_size
            file_name = f"photo_{int(time.time())}.jpg"
        else:
            file_name = media.file_name
            file_size = media.file_size
        
        if not file_name:
            await message.reply_text("❌ **Error:** File has no name.")
            return
            
        if file_size > MAX_FILE_SIZE:
            await message.reply_text(
                f"❌ **Error:** File is larger than {humanbytes(MAX_FILE_SIZE)}.\n"
                f"**Your file:** {humanbytes(file_size)}"
            )
            return

        # Sanitize filename and create user-specific path
        safe_filename = sanitize_filename(file_name)
        user_file_name = f"{get_user_folder(message.from_user.id)}/{int(time.time())}_{safe_filename}"
        local_file_path = os.path.join(DOWNLOAD_DIR, user_file_name.replace('/', '_'))

        # Create status message
        file_type = get_file_type(file_name).title()
        status_message = await message.reply_text(
            f"🚀 **Processing File**\n\n"
            f"**Name:** `{safe_filename}`\n"
            f"**Size:** {humanbytes(file_size)}\n"
            f"**Type:** {file_type}\n\n"
            f"*Starting download...*"
        )

        try:
            # 1. Download from Telegram
            progress_tracker = ProgressTracker(status_message, "Downloading")
            
            def download_progress(current, total):
                asyncio.run_coroutine_threadsafe(
                    progress_tracker.update(current, total),
                    client.loop
                )
            
            local_file_path = await message.download(
                file_name=local_file_path,
                progress=download_progress
            )
            
            if not local_file_path or not os.path.exists(local_file_path):
                await status_message.edit_text("❌ Download failed: File not saved.")
                return

            await status_message.edit_text("✅ Download complete. Starting upload to Wasabi...")

            # 2. Upload to Wasabi
            upload_success = await upload_to_wasabi(local_file_path, user_file_name, file_size, status_message)
            if not upload_success:
                return

            await status_message.edit_text("✅ Upload complete. Generating shareable links...")
            
            # 3. Generate URLs
            presigned_url = await generate_presigned_url(user_file_name)
            streaming_link = generate_streaming_link(user_file_name)
            player_url = generate_player_url(safe_filename, presigned_url) if presigned_url else None
            
            # 4. Prepare final message with buttons
            final_message = (
                f"✅ **File Uploaded Successfully!**\n\n"
                f"**File:** `{safe_filename}`\n"
                f"**Type:** {file_type}\n"
                f"**Size:** {humanbytes(file_size)}\n"
                f"**Stored as:** `{user_file_name}`\n"
                f"**Links valid for:** 7 days\n\n"
                f"*Use the buttons below to access your file*"
            )
            
            # Create keyboard with options
            keyboard = create_download_keyboard(presigned_url, player_url, user_file_name)
            
            await status_message.edit_text(final_message, reply_markup=keyboard)
            logger.info(f"File uploaded successfully: {safe_filename} by user {message.from_user.id}")

        except Exception as e:
            logger.error(f"File processing error: {e}", exc_info=True)
            await status_message.edit_text(f"❌ **Upload failed:**\n`{str(e)}`")
        finally:
            # Cleanup downloaded file
            if 'local_file_path' in locals() and os.path.exists(local_file_path):
                try:
                    os.remove(local_file_path)
                    logger.debug(f"Cleaned up local file: {local_file_path}")
                except Exception as e:
                    logger.warning(f"Could not remove local file: {e}")

    # --- Callback Query Handlers ---
    @client.on_callback_query(filters.regex("^help$"))
    async def help_callback(_, query):
        await help_handler(_, query.message)
        await query.answer()

    @client.on_callback_query(filters.regex("^admin$"))
    @is_admin
    async def admin_callback(_, query):
        admin_text = (
            "**Admin Panel**\n\n"
            "**Available Commands:**\n"
            "• `/adduser <id>` - Add user\n"
            "• `/removeuser <id>` - Remove user\n" 
            "• `/listusers` - List users\n"
            "• `/stats` - Bot statistics\n"
            "• `/player <file>` - Generate player URL\n\n"
            f"**Authorized Users:** {len(ALLOWED_USERS)}"
        )
        await query.message.edit_text(admin_text)
        await query.answer()

    @client.on_callback_query(filters.regex("^status$"))
    async def status_callback(_, query):
        status_text = (
            f"**🤖 Bot Status**\n\n"
            f"**Storage:** {'✅ Connected' if s3_client else '❌ Disconnected'}\n"
            f"**Bucket:** `{WASABI_BUCKET}`\n"
            f"**Your ID:** `{query.from_user.id}`\n"
            f"**Access:** {'✅ Authorized' if query.from_user.id in ALLOWED_USERS else '❌ Not authorized'}\n\n"
            f"*Send a file to test upload*"
        )
        await query.message.edit_text(status_text)
        await query.answer()

    @client.on_callback_query(filters.regex("^users$"))
    @is_admin
    async def users_callback(_, query):
        user_list = "\n".join([f"• `{user_id}`" for user_id in ALLOWED_USERS])
        await query.message.edit_text(f"👥 **Authorized Users ({len(ALLOWED_USERS)}):**\n{user_list}")
        await query.answer()

    @client.on_callback_query(filters.regex("^list_files$"))
    @is_authorized
    async def list_files_callback(_, query):
        await list_files_handler(_, query.message)
        await query.answer()

    @client.on_callback_query(filters.regex("^copy_"))
    async def copy_callback(_, query):
        try:
            key = query.data.replace("copy_", "")
            presigned_url = get_callback_data(key)
            
            if presigned_url:
                # Show shortened URL in alert
                shortened_url = presigned_url[:100] + "..." if len(presigned_url) > 100 else presigned_url
                await query.answer(f"URL copied! Use: {shortened_url}", show_alert=True)
                
                # Also send the full URL in a message
                await query.message.reply_text(
                    f"📋 **Direct Download URL**\n\n"
                    f"`{presigned_url}`\n\n"
                    f"*Copy this URL to share*",
                    disable_web_page_preview=True
                )
            else:
                await query.answer("❌ URL not found", show_alert=True)
                
        except Exception as e:
            await query.answer("❌ Failed to copy URL", show_alert=True)

    @client.on_callback_query(filters.regex("^info_"))
    async def info_callback(_, query):
        try:
            key = query.data.replace("info_", "")
            filename = get_callback_data(key)
            
            if filename:
                # Extract original filename from stored path
                original_name = filename.split('/')[-1]
                file_type = get_file_type(original_name)
                
                info_text = (
                    f"📄 **File Information**\n\n"
                    f"**Name:** `{original_name}`\n"
                    f"**Type:** {file_type.title()}\n"
                    f"**Path:** `{filename}`\n"
                    f"**User:** `{filename.split('/')[1].split('_')[1]}`\n"
                    f"**Uploaded:** Timestamp `{filename.split('/')[-1].split('_')[0]}`"
                )
                
                await query.message.edit_text(info_text)
                await query.answer()
            else:
                await query.answer("❌ File info not found", show_alert=True)
                
        except Exception as e:
            await query.answer("❌ Failed to get file info", show_alert=True)

# --- Main Application ---
async def main():
    """Main application entry point."""
    global app, s3_client
    
    logger.info("🚀 Starting Wasabi Upload Bot...")
    
    # Initialize Wasabi client
    if not initialize_wasabi_client():
        logger.error("❌ Failed to initialize Wasabi client. Check your credentials.")
        return
    
    # Create Pyrogram client
    app = Client(
        "wasabi_bot",
        api_id=API_ID,
        api_hash=API_HASH,
        bot_token=BOT_TOKEN,
        workers=100,
        sleep_threshold=60
    )
    
    # Register handlers
    register_handlers(app)
    
    # Start the bot
    logger.info("✅ Bot is starting...")
    await app.start()
    
    # Get bot info
    bot_info = await app.get_me()
    logger.info(f"🤖 Bot started as @{bot_info.username}")
    logger.info(f"👤 Admin ID: {ADMIN_ID}")
    logger.info(f"👥 Allowed users: {len(ALLOWED_USERS)}")
    logger.info(f"💾 Max file size: {humanbytes(MAX_FILE_SIZE)}")
    
    # Keep running
    await asyncio.Event().wait()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("🛑 Bot stopped by user")
    except Exception as e:
        logger.error(f"❌ Bot crashed: {e}", exc_info=True)
    finally:
        if app:
            asyncio.run(app.stop())
        logger.info("👋 Bot stopped")