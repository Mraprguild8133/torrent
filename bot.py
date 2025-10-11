import os
import time
import math
import asyncio
import logging
import base64
import threading
import queue
from functools import wraps
from urllib.parse import quote
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
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Configuration
API_ID = config.API_ID
API_HASH = config.API_HASH
BOT_TOKEN = config.BOT_TOKEN
WASABI_ACCESS_KEY = config.WASABI_ACCESS_KEY
WASABI_SECRET_KEY = config.WASABI_SECRET_KEY
WASABI_BUCKET = config.WASABI_BUCKET
WASABI_REGION = config.WASABI_REGION
ADMIN_ID = config.ADMIN_ID
MAX_FILE_SIZE = getattr(config, 'MAX_FILE_SIZE', 4 * 1024 * 1024 * 1024)  # 4GB default

# Player URL configuration
RENDER_URL = os.getenv("RENDER_URL", "http://localhost:8000")
SUPPORTED_VIDEO_FORMATS = {'.mp4', '.mkv', '.avi', '.mov', '.wmv', '.flv', '.webm', '.m4v', '.3gp', '.mpeg', '.mpg'}

# User management
ALLOWED_USERS = {ADMIN_ID}

# Global clients
app = None
s3_client = None

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

def is_video_file(filename: str) -> bool:
    """Check if file is a supported video format."""
    return get_file_extension(filename) in SUPPORTED_VIDEO_FORMATS

def generate_player_url(filename: str, presigned_url: str) -> Optional[str]:
    """Generate player URL for supported file types."""
    if not RENDER_URL:
        return None
    
    if is_video_file(filename):
        encoded_url = base64.urlsafe_b64encode(presigned_url.encode()).decode().rstrip('=')
        return f"{RENDER_URL}/player/video/{encoded_url}"
    return None

def generate_streaming_link(file_name: str) -> str:
    """Generate public streaming link for the file."""
    encoded_file_name = quote(file_name)
    return f"https://{WASABI_BUCKET}.s3.{WASABI_REGION}.wasabisys.com/{encoded_file_name}"

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
            await message.reply_text("⛔️ You are not authorized to use this bot. Contact the admin.")
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
        
        progress_bar = "▰" * int(percentage / 5) + "▱" * (20 - int(percentage / 5))
        
        progress_message = (
            f"**{self.action} in Progress...**\n"
            f"`[{progress_bar}] {percentage:.2f}%`\n"
            f"**Speed:** `{humanbytes(speed)}/s`\n"
            f"**Transferred:** `{humanbytes(current)} / {humanbytes(total)}`\n"
            f"**Time Elapsed:** `{int(elapsed_time)}s`"
        )
        
        try:
            await self.message.edit_text(progress_message)
            self.last_update_time = current_time
        except FloodWait as e:
            await asyncio.sleep(e.value)
        except Exception as e:
            logger.warning(f"Progress update failed: {e}")

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
        s3_client.upload_file(
            file_path,
            WASABI_BUCKET,
            file_name,
            Callback=lambda bytes_transferred: progress_queue.put(bytes_transferred)
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
            f"👋 **Welcome to Wasabi Storage Bot!**\n\n"
            f"**Your User ID:** `{message.from_user.id}`\n\n"
            "Send me any file to upload to secure Wasabi storage.\n\n"
            "**Features:**\n"
            "• Direct download links\n"
            "• Video player URLs for streaming\n"
            "• Progress tracking\n"
            "• 7-day link validity\n"
            "• Support for files up to 4GB"
        )
        
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📚 Help", callback_data="help"),
             InlineKeyboardButton("🔧 Admin", callback_data="admin")],
            [InlineKeyboardButton("👨‍💻 Developer", url="https://t.me/tprojects")]
        ])
        
        await message.reply_text(welcome_text, reply_markup=keyboard)

    @client.on_message(filters.command("help"))
    async def help_handler(_, message: Message):
        help_text = """
🤖 **Wasabi Upload Bot Help**

**For Users:**
• Just send any file to upload
• Get direct download links
• Video files get player URLs for streaming

**For Admin:**
• `/adduser <user_id>` - Add authorized user
• `/removeuser <user_id>` - Remove user
• `/listusers` - Show authorized users
• `/stats` - Bot statistics

**Supported Video Formats:**
MP4, MKV, AVI, MOV, WMV, FLV, WebM, M4V, 3GP, MPEG, MPG

**Player URLs:**
Video files get special player URLs for streaming.
"""
        await message.reply_text(help_text)

    @client.on_message(filters.command("adduser"))
    @is_admin
    async def add_user_handler(_, message: Message):
        try:
            user_id_to_add = int(message.text.split(" ", 1)[1])
            ALLOWED_USERS.add(user_id_to_add)
            await message.reply_text(f"✅ User `{user_id_to_add}` has been added successfully.")
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
            else:
                await message.reply_text("🤷 User not found in the authorized list.")
        except (IndexError, ValueError):
            await message.reply_text("⚠️ **Usage:** /removeuser `<user_id>`")

    @client.on_message(filters.command("listusers"))
    @is_admin
    async def list_users_handler(_, message: Message):
        user_list = "\n".join([f"- `{user_id}`" for user_id in ALLOWED_USERS])
        await message.reply_text(f"👥 **Authorized Users:**\n{user_list}")

    @client.on_message(filters.command("stats"))
    @is_admin
    async def stats_handler(_, message: Message):
        stats_text = (
            f"🤖 **Bot Statistics**\n"
            f"• Authorized users: {len(ALLOWED_USERS)}\n"
            f"• Wasabi connected: {'✅' if s3_client else '❌'}\n"
            f"• Bucket: {WASABI_BUCKET}\n"
            f"• Region: {WASABI_REGION}\n"
            f"• Player URL: {RENDER_URL}"
        )
        await message.reply_text(stats_text)

    @client.on_message(filters.command("player"))
    @is_authorized
    async def player_url_handler(_, message: Message):
        """Generate player URL for existing files in Wasabi"""
        try:
            filename = message.text.split(" ", 1)[1].strip()
            
            try:
                s3_client.head_object(Bucket=WASABI_BUCKET, Key=filename)
                
                if is_video_file(filename):
                    presigned_url = await generate_presigned_url(filename)
                    if presigned_url:
                        player_url = generate_player_url(filename, presigned_url)
                        await message.reply_text(
                            f"🎥 **Player URL for `{filename}`**\n\n"
                            f"{player_url}\n\n"
                            f"*This URL allows direct video streaming in browsers*",
                            disable_web_page_preview=False
                        )
                    else:
                        await message.reply_text("❌ Could not generate presigned URL for the file.")
                else:
                    await message.reply_text(
                        f"⚠️ `{filename}` is not a supported video format.\n"
                        f"Supported formats: {', '.join(SUPPORTED_VIDEO_FORMATS)}"
                    )
                    
            except ClientError as e:
                if e.response['Error']['Code'] == '404':
                    await message.reply_text(f"❌ File `{filename}` not found in Wasabi storage.")
                else:
                    await message.reply_text(f"❌ Error accessing file: {e.response['Error']['Message']}")
                    
        except IndexError:
            await message.reply_text("⚠️ **Usage:** /player `<filename>`\nExample: `/player 1234567890_myvideo.mp4`")

    # --- File Handling ---
    @client.on_message(filters.document | filters.video | filters.audio)
    @is_authorized
    async def file_handler(client, message: Message):
        if not s3_client:
            await message.reply_text("❌ **Error:** Wasabi client is not initialized.")
            return

        media = message.document or message.video or message.audio
        file_name = media.file_name
        file_size = media.file_size
        
        if file_size > MAX_FILE_SIZE:
            await message.reply_text(f"❌ **Error:** File is larger than {humanbytes(MAX_FILE_SIZE)}.")
            return

        status_message = await message.reply_text("🚀 Preparing to process your file...")
        
        # Create unique file name
        timestamp = int(time.time())
        safe_filename = f"{timestamp}_{file_name}"
        file_path = f"./downloads/{safe_filename}"
        os.makedirs(os.path.dirname(file_path), exist_ok=True)

        try:
            # 1. Download from Telegram
            progress_tracker = ProgressTracker(status_message, "Downloading")
            
            def download_progress(current, total):
                asyncio.run_coroutine_threadsafe(
                    progress_tracker.update(current, total),
                    client.loop
                )
            
            file_path = await message.download(
                file_name=file_path,
                progress=download_progress
            )
            
            if not file_path:
                await status_message.edit_text("❌ Download failed.")
                return

            await status_message.edit_text("✅ Download complete. Starting upload to Wasabi...")

            # 2. Upload to Wasabi
            upload_success = await upload_to_wasabi(file_path, safe_filename, file_size, status_message)
            if not upload_success:
                return

            await status_message.edit_text("✅ Upload complete. Generating shareable links...")
            
            # 3. Generate URLs
            presigned_url = await generate_presigned_url(safe_filename)
            streaming_link = generate_streaming_link(safe_filename)
            player_url = generate_player_url(safe_filename, presigned_url) if presigned_url and is_video_file(file_name) else None
            
            # 4. Prepare final message with buttons
            final_message = (
                f"✅ **File Uploaded Successfully!**\n\n"
                f"**File:** `{file_name}`\n"
                f"**Size:** {humanbytes(file_size)}\n"
                f"**Stored as:** `{safe_filename}`\n"
            )
            
            # Create buttons
            keyboard_buttons = []
            if streaming_link:
                keyboard_buttons.append([InlineKeyboardButton("🌐 Direct Link", url=streaming_link)])
            if player_url:
                keyboard_buttons.append([InlineKeyboardButton("🎥 Player URL", url=player_url)])
            if presigned_url:
                keyboard_buttons.append([InlineKeyboardButton("📋 Presigned URL", callback_data=f"url_{safe_filename}")])
            
            reply_markup = InlineKeyboardMarkup(keyboard_buttons) if keyboard_buttons else None
            
            await status_message.edit_text(final_message, reply_markup=reply_markup)

        except Exception as e:
            logger.error(f"File processing error: {e}", exc_info=True)
            await status_message.edit_text(f"❌ **Upload failed:**\n`{str(e)}`")
        finally:
            # Cleanup
            if os.path.exists(file_path):
                os.remove(file_path)

    # Callback handlers
    @client.on_callback_query(filters.regex("^help$"))
    async def help_callback(_, query):
        await help_handler(_, query.message)
        await query.answer()

    @client.on_callback_query(filters.regex("^admin$"))
    @is_admin
    async def admin_callback(_, query):
        admin_text = "**Admin Panel**\n\nUse commands:\n• /adduser - Add user\n• /removeuser - Remove user\n• /listusers - List users\n• /stats - Bot stats"
        await query.message.edit_text(admin_text)
        await query.answer()

    @client.on_callback_query(filters.regex("^url_"))
    async def url_callback(_, query):
        filename = query.data.replace("url_", "")
        presigned_url = await generate_presigned_url(filename)
        if presigned_url:
            await query.answer(presigned_url, show_alert=True)
        else:
            await query.answer("❌ Could not generate URL", show_alert=True)

# --- Main Application ---
async def main():
    global app
    
    # Validate configuration
    if not all([API_ID, API_HASH, BOT_TOKEN]):
        logger.error("❌ Missing Telegram API configuration")
        return
    
    # Initialize Wasabi
    wasabi_ready = initialize_wasabi_client()
    
    # Create Pyrogram client
    app = Client("wasabi_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
    
    # Register handlers
    register_handlers(app)
    
    try:
        logger.info("🤖 Bot is starting...")
        await app.start()
        
        me = await app.get_me()
        logger.info(f"✅ Bot started successfully as @{me.username}")
        
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
