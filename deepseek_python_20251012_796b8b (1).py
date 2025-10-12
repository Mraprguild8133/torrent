import os
import time
import asyncio
import logging
import base64
import threading
import queue
import hashlib
from functools import wraps
from urllib.parse import quote, urlencode
from typing import Optional

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
WEB_PORT = config.WEB_PORT

# Supported formats
SUPPORTED_VIDEO_FORMATS = {
    '.mp4', '.mkv', '.avi', '.mov', '.wmv', '.flv', 
    '.webm', '.m4v', '.3gp', '.mpeg', '.mpg', '.ts'
}

SUPPORTED_AUDIO_FORMATS = {
    '.mp3', '.m4a', '.flac', '.wav', '.aac', '.ogg', '.wma'
}

SUPPORTED_IMAGE_FORMATS = {
    '.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', '.svg'
}

# User management
ALLOWED_USERS = {ADMIN_ID}

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
    """Get file type for player URL generation."""
    ext = get_file_extension(filename)
    if ext in SUPPORTED_VIDEO_FORMATS:
        return 'video'
    elif ext in SUPPORTED_AUDIO_FORMATS:
        return 'audio'
    elif ext in SUPPORTED_IMAGE_FORMATS:
        return 'image'
    else:
        return 'document'

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
    
    try:
        file_type = get_file_type(filename)
        if file_type in ['video', 'audio', 'image']:
            # Encode the presigned URL for security
            encoded_url = base64.urlsafe_b64encode(presigned_url.encode()).decode().rstrip('=')
            return f"{RENDER_URL.rstrip('/')}/player/{file_type}/{encoded_url}"
        return None
    except Exception as e:
        logger.error(f"Error generating player URL: {e}")
        return None

def generate_direct_link(file_name: str) -> str:
    """Generate direct public link for the file."""
    try:
        encoded_file_name = quote(file_name, safe='')
        direct_link = f"https://{WASABI_BUCKET}.s3.{WASABI_REGION}.wasabisys.com/{encoded_file_name}"
        logger.info(f"Generated direct link: {direct_link}")
        return direct_link
    except Exception as e:
        logger.error(f"Error generating direct link: {e}")
        return f"https://s3.{WASABI_REGION}.wasabisys.com/{WASABI_BUCKET}/{quote(file_name, safe='')}"

def generate_presigned_url(file_name: str, expires_in: int = 604800) -> Optional[str]:
    """Generate presigned URL with proper configuration for Wasabi."""
    try:
        logger.info(f"Generating presigned URL for: {file_name}")
        
        presigned_url = s3_client.generate_presigned_url(
            'get_object',
            Params={
                'Bucket': WASABI_BUCKET, 
                'Key': file_name,
                'ResponseContentDisposition': f'attachment; filename="{quote(file_name)}"'
            },
            ExpiresIn=expires_in,
            HttpMethod='GET'
        )
        
        logger.info(f"Successfully generated presigned URL: {presigned_url[:100]}...")
        return presigned_url
        
    except ClientError as e:
        logger.error(f"Failed to generate presigned URL: {e}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error generating presigned URL: {e}")
        return None

def create_download_keyboard(presigned_url: str, player_url: str = None, filename: str = None, safe_filename: str = None) -> InlineKeyboardMarkup:
    """Create optimized inline keyboard with download options."""
    keyboard = []
    
    file_type = get_file_type(filename) if filename else 'document'
    
    # Player button for supported media types
    if player_url and file_type in ['video', 'audio', 'image']:
        icons = {'video': '🎬', 'audio': '🎵', 'image': '🖼️'}
        icon = icons.get(file_type, '🎬')
        keyboard.append([InlineKeyboardButton(f"{icon} Web Player", url=player_url)])
    
    # Download buttons
    download_buttons = []
    
    # Direct download button (presigned URL)
    if presigned_url:
        download_buttons.append(InlineKeyboardButton("📥 Direct Download", url=presigned_url))
    
    # Public link button
    if safe_filename:
        direct_link = generate_direct_link(safe_filename)
        download_buttons.append(InlineKeyboardButton("🌐 Public Link", url=direct_link))
    
    if download_buttons:
        keyboard.append(download_buttons)
    
    # Additional actions
    action_buttons = []
    
    # Copy URL button
    if presigned_url and safe_filename:
        url_key = generate_callback_key(f"url_{safe_filename}")
        store_callback_data(url_key, presigned_url)
        action_buttons.append(InlineKeyboardButton("📋 Copy URL", callback_data=f"copy_{url_key}"))
    
    # File info button
    if safe_filename:
        info_key = generate_callback_key(f"info_{safe_filename}")
        store_callback_data(info_key, safe_filename)
        action_buttons.append(InlineKeyboardButton("🔍 File Info", callback_data=f"info_{info_key}"))
    
    if action_buttons:
        keyboard.append(action_buttons)
    
    # Web player link
    if RENDER_URL:
        keyboard.append([InlineKeyboardButton("🌐 Open Web Player", url=RENDER_URL)])
    
    return InlineKeyboardMarkup(keyboard)

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

# --- Progress Tracking ---
class ProgressTracker:
    """Track upload/download progress with rate limiting."""
    def __init__(self, message: Message, action: str):
        self.message = message
        self.action = action
        self.start_time = time.time()
        self.last_update_time = 0
        self.update_interval = 2  # Update every 2 seconds

    async def update(self, current: int, total: int):
        """Update progress message with rate limiting."""
        current_time = time.time()
        if current_time - self.last_update_time < self.update_interval and current != total:
            return

        elapsed_time = current_time - self.start_time
        speed = current / elapsed_time if elapsed_time > 0 else 0
        percentage = (current / total) * 100 if total > 0 else 0
        
        # Progress bar
        progress_bar = "▰" * int(percentage / 5) + "▱" * (20 - int(percentage / 5))
        
        # ETA calculation
        eta = (total - current) / speed if speed > 0 else 0
        
        progress_message = (
            f"**{self.action} in Progress...**\n"
            f"`[{progress_bar}] {percentage:.2f}%`\n"
            f"**Speed:** `{humanbytes(speed)}/s`\n"
            f"**Transferred:** `{humanbytes(current)} / {humanbytes(total)}`\n"
            f"**Time Elapsed:** `{int(elapsed_time)}s`\n"
            f"**ETA:** `{int(eta)}s`"
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
    """Initialize Wasabi S3 client."""
    global s3_client
    try:
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
        logger.error(f"❌ Failed to connect to Wasabi: {e}")
        s3_client = None
        return False

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

# --- Bot Command Handlers ---
def register_handlers(client):
    """Register all message handlers."""
    
    @client.on_message(filters.command("start"))
    async def start_handler(_, message: Message):
        welcome_text = (
            f"👋 **Welcome to Wasabi Storage Bot!** 🚀\n\n"
            f"**Your User ID:** `{message.from_user.id}`\n\n"
            "Send me any file to upload to secure Wasabi storage and get shareable links.\n\n"
            "**Features:**\n"
            "• Direct download links\n"
            "• Video/Audio/Image player URLs\n"
            "• Real-time progress tracking\n"
            "• 7-day link validity\n"
            f"• Support for files up to {humanbytes(MAX_FILE_SIZE)}\n"
            f"• Web player available at: {RENDER_URL}"
        )
        
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📚 Help", callback_data="help"),
             InlineKeyboardButton("🔧 Admin", callback_data="admin")],
            [InlineKeyboardButton("📊 Status", callback_data="status"),
             InlineKeyboardButton("👥 Users", callback_data="users")],
            [InlineKeyboardButton("🌐 Web Player", url=RENDER_URL)],
            [InlineKeyboardButton("👨‍💻 Developer", url="https://t.me/tprojects")]
        ])
        
        await message.reply_text(welcome_text, reply_markup=keyboard)

    @client.on_message(filters.command("help"))
    async def help_handler(_, message: Message):
        help_text = f"""
🤖 **Wasabi Upload Bot Help**

**For Users:**
• Just send any file to upload
• Get direct download links
• Video/Audio/Image files get player URLs
• All links are valid for 7 days
• Web player: {RENDER_URL}

**For Admin:**
• `/adduser <user_id>` - Add authorized user
• `/removeuser <user_id>` - Remove user
• `/listusers` - Show authorized users
• `/stats` - Bot statistics
• `/player <filename>` - Generate player URL for existing file

**Supported Formats:**
• **Videos:** {', '.join(sorted(SUPPORTED_VIDEO_FORMATS))}
• **Audio:** {', '.join(sorted(SUPPORTED_AUDIO_FORMATS))}
• **Images:** {', '.join(sorted(SUPPORTED_IMAGE_FORMATS))}
• **Documents:** PDF, ZIP, RAR, and more

**Player URLs:**
Video, audio, and image files get special player URLs that work in browsers.
"""
        await message.reply_text(help_text)

    @client.on_message(filters.command("adduser"))
    @is_admin
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
    async def list_users_handler(_, message: Message):
        user_list = "\n".join([f"• `{user_id}`" for user_id in ALLOWED_USERS])
        await message.reply_text(f"👥 **Authorized Users ({len(ALLOWED_USERS)}):**\n{user_list}")

    @client.on_message(filters.command("stats"))
    @is_admin
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
            f"• Player URL: {RENDER_URL}\n"
            f"• Download dir: `{DOWNLOAD_DIR}`\n"
            f"• Web port: `{WEB_PORT}`"
        )
        await message.reply_text(stats_text)

    @client.on_message(filters.command("player"))
    @is_authorized
    async def player_url_handler(_, message: Message):
        """Generate player URL for existing files in Wasabi"""
        try:
            filename = message.text.split(" ", 1)[1].strip()
            
            try:
                # Check if file exists
                s3_client.head_object(Bucket=WASABI_BUCKET, Key=filename)
                
                # Generate URLs
                presigned_url = await generate_presigned_url(filename)
                
                if not presigned_url:
                    await message.reply_text("❌ Failed to generate presigned URL for the file.")
                    return
                
                player_url = generate_player_url(filename, presigned_url)
                file_type = get_file_type(filename)
                
                # Create keyboard
                reply_markup = create_download_keyboard(
                    presigned_url=presigned_url,
                    player_url=player_url,
                    filename=filename,
                    safe_filename=filename
                )
                
                response_text = (
                    f"🔗 **URLs for `{filename}`**\n\n"
                    f"**Type:** {file_type.title()}\n"
                    f"**Player:** {'Available' if player_url else 'Not available'}\n"
                    f"**Links valid for:** 7 days\n\n"
                    f"*Use the buttons below to access your file*"
                )
                
                await message.reply_text(
                    response_text,
                    disable_web_page_preview=True,
                    reply_markup=reply_markup
                )
                    
            except ClientError as e:
                if e.response['Error']['Code'] == '404':
                    await message.reply_text(f"❌ File `{filename}` not found in Wasabi storage.")
                else:
                    await message.reply_text(f"❌ Error accessing file: {e.response['Error']['Message']}")
                    
        except IndexError:
            await message.reply_text("⚠️ **Usage:** /player `<filename>`\n**Example:** `/player 1234567890_myvideo.mp4`")

    # --- File Handling ---
    @client.on_message(filters.document | filters.video | filters.audio | filters.photo)
    @is_authorized
    async def file_handler(client, message: Message):
        if not s3_client:
            await message.reply_text("❌ **Error:** Wasabi client is not initialized. File uploads are disabled.")
            return

        # Initialize variables
        file_name = None
        file_size = 0
        media = None

        # Get media object based on message type
        if message.document:
            media = message.document
            file_name = media.file_name
            file_size = media.file_size
        elif message.video:
            media = message.video
            file_name = media.file_name
            file_size = media.file_size
        elif message.audio:
            media = message.audio
            file_name = media.file_name
            file_size = media.file_size
        elif message.photo:
            # For photos, we need to get the largest size
            file_name = f"photo_{message.id}.jpg"
            file_size = message.photo.file_size
            # We'll handle photo download separately
        else:
            await message.reply_text("❌ **Error:** Unsupported file type.")
            return

        if not file_name:
            await message.reply_text("❌ **Error:** File has no name.")
            return
            
        if file_size > MAX_FILE_SIZE:
            await message.reply_text(
                f"❌ **Error:** File is larger than {humanbytes(MAX_FILE_SIZE)}.\n"
                f"**Your file:** {humanbytes(file_size)}"
            )
            return

        # Create status message
        status_message = await message.reply_text(
            f"🚀 **Processing File**\n\n"
            f"**Name:** `{file_name}`\n"
            f"**Size:** {humanbytes(file_size)}\n"
            f"**Type:** {get_file_type(file_name).title()}\n\n"
            f"*Starting download...*"
        )
        
        # Create unique file name
        timestamp = int(time.time())
        safe_filename = f"{timestamp}_{file_name}"
        file_path = os.path.join(DOWNLOAD_DIR, safe_filename)

        try:
            # 1. Download from Telegram
            progress_tracker = ProgressTracker(status_message, "Downloading")
            
            # Create a proper async progress callback
            async def download_progress(current, total):
                await progress_tracker.update(current, total)
            
            # Download the file
            if message.photo:
                # Download photo (no progress for photos as they're usually small)
                file_path = await client.download_media(
                    message,
                    file_name=file_path
                )
                # Update progress manually for photos
                await progress_tracker.update(file_size, file_size)
            else:
                # Download other media types with progress
                file_path = await message.download(
                    file_name=file_path,
                    progress=download_progress
                )
            
            if not file_path or not os.path.exists(file_path):
                await status_message.edit_text("❌ Download failed: File not saved.")
                return

            # Verify downloaded file size
            actual_size = os.path.getsize(file_path)
            if actual_size == 0:
                await status_message.edit_text("❌ Download failed: File is empty.")
                return

            await status_message.edit_text("✅ Download complete. Starting upload to Wasabi...")

            # 2. Upload to Wasabi
            upload_success = await upload_to_wasabi(file_path, safe_filename, file_size, status_message)
            if not upload_success:
                return

            await status_message.edit_text("✅ Upload complete. Generating shareable links...")
            
            # 3. Generate URLs
            presigned_url = await generate_presigned_url(safe_filename)
            
            if not presigned_url:
                await status_message.edit_text("❌ Failed to generate shareable URLs. File uploaded but URLs not created.")
                return
            
            player_url = generate_player_url(file_name, presigned_url)
            
            # 4. Prepare final message
            file_type = get_file_type(file_name)
            
            final_message = (
                f"✅ **File Uploaded Successfully!** 🎉\n\n"
                f"**File:** `{file_name}`\n"
                f"**Type:** {file_type.title()}\n"
                f"**Size:** {humanbytes(file_size)}\n"
                f"**Stored as:** `{safe_filename}`\n"
                f"**Player:** {'Available' if player_url else 'Not available'}\n"
                f"**Links valid for:** 7 days\n\n"
                f"*Use the buttons below to access your file*"
            )
            
            # Create optimized keyboard
            reply_markup = create_download_keyboard(
                presigned_url=presigned_url,
                player_url=player_url,
                filename=file_name,
                safe_filename=safe_filename
            )
            
            await status_message.edit_text(final_message, reply_markup=reply_markup)
            logger.info(f"File uploaded successfully: {file_name} by user {message.from_user.id}")

        except Exception as e:
            logger.error(f"File processing error: {e}", exc_info=True)
            await status_message.edit_text(f"❌ **Upload failed:**\n`{str(e)}`")
        finally:
            # Cleanup downloaded file
            if file_path and os.path.exists(file_path):
                try:
                    os.remove(file_path)
                    logger.debug(f"Cleaned up local file: {file_path}")
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
            f"**Authorized Users:** {len(ALLOWED_USERS)}\n"
            f"**Web Player:** {RENDER_URL}"
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
            f"**Access:** {'✅ Authorized' if query.from_user.id in ALLOWED_USERS else '❌ Not authorized'}\n"
            f"**Web Player:** {RENDER_URL}\n\n"
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

    @client.on_callback_query(filters.regex("^copy_"))
    async def copy_callback(_, query):
        try:
            key = query.data.replace("copy_", "")
            presigned_url = get_callback_data(key)
            
            if presigned_url:
                # Show shortened URL in alert
                shortened_url = presigned_url[:100] + "..." if len(presigned_url) > 100 else presigned_url
                await query.answer(f"URL copied! Use: {shortened_url}", show_alert=True)
            else:
                await query.answer("❌ URL expired or not found", show_alert=True)
        except Exception as e:
            await query.answer("❌ Error retrieving URL", show_alert=True)

    @client.on_callback_query(filters.regex("^info_"))
    async def info_callback(_, query):
        try:
            key = query.data.replace("info_", "")
            filename = get_callback_data(key)
            
            if filename and s3_client:
                response = s3_client.head_object(Bucket=WASABI_BUCKET, Key=filename)
                file_info = (
                    f"**📁 File Information**\n\n"
                    f"**Name:** `{filename}`\n"
                    f"**Size:** {humanbytes(response['ContentLength'])}\n"
                    f"**Type:** {response.get('ContentType', 'Unknown')}\n"
                    f"**Last Modified:** {response['LastModified'].strftime('%Y-%m-%d %H:%M:%S')}\n"
                    f"**Storage Class:** {response.get('StorageClass', 'Standard')}"
                )
                await query.answer(file_info, show_alert=True)
            else:
                await query.answer("❌ File information not available", show_alert=True)
                
        except ClientError as e:
            await query.answer(f"❌ Error: {e.response['Error']['Message']}", show_alert=True)
        except Exception as e:
            await query.answer("❌ Error getting file info", show_alert=True)

    logger.info("All handlers registered successfully")

# --- Web Server Integration ---
async def start_web_server():
    """Start the Flask web server in a separate thread."""
    try:
        import web_server
        import threading
        
        def run_web_server():
            web_server.app.run(host='0.0.0.0', port=WEB_PORT, debug=False)
        
        web_thread = threading.Thread(target=run_web_server, daemon=True)
        web_thread.start()
        logger.info(f"🌐 Web server started on port {WEB_PORT}")
        return True
    except Exception as e:
        logger.error(f"❌ Failed to start web server: {e}")
        return False

# --- Main Application ---
async def main():
    global app
    
    # Validate configuration
    if not config.validate_config():
        logger.error("❌ Configuration validation failed. Please check your environment variables.")
        return
    
    config.print_config_summary()
    
    # Initialize Wasabi
    wasabi_ready = initialize_wasabi_client()
    
    if not wasabi_ready:
        logger.warning("⚠️  Wasabi storage not available - file uploads will not work")
    
    # Start web server
    web_server_ready = await start_web_server()
    
    if not web_server_ready:
        logger.warning("⚠️  Web server not available - player URLs will not work")
    
    # Create Pyrogram client
    app = Client(
        "wasabi_storage_bot",
        api_id=API_ID,
        api_hash=API_HASH,
        bot_token=BOT_TOKEN,
        workdir=DOWNLOAD_DIR
    )
    
    # Register handlers
    register_handlers(app)
    
    try:
        logger.info("🤖 Bot is starting...")
        await app.start()
        
        me = await app.get_me()
        logger.info(f"✅ Bot started successfully as @{me.username}")
        logger.info(f"✅ Authorized users: {len(ALLOWED_USERS)}")
        logger.info(f"✅ Wasabi storage: {'Connected' if s3_client else 'Not available'}")
        logger.info(f"🌐 Web server: {'Running on port ' + str(WEB_PORT) if web_server_ready else 'Not available'}")
        logger.info(f"🌐 Web player available at: {RENDER_URL}")
        
        # Keep running
        await asyncio.Event().wait()
        
    except KeyboardInterrupt:
        logger.info("⏹️ Bot stopped by user")
    except Exception as e:
        logger.error(f"❌ Bot crashed: {e}")
    finally:
        if app:
            await app.stop()
        logger.info("✅ Bot stopped gracefully")

if __name__ == "__main__":
    asyncio.run(main())