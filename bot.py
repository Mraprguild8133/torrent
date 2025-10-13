import os
import time
import math
import asyncio
import logging
import base64
import aiofiles
from functools import wraps
from urllib.parse import quote
from threading import Thread
from concurrent.futures import ThreadPoolExecutor
import multiprocessing

import boto3
from botocore.exceptions import ClientError
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardButton, InlineKeyboardMarkup
from flask import Flask, render_template, request, jsonify, send_file

# Import configuration
from config import config

# --- Configuration ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Use configuration from config module
API_ID = config.API_ID
API_HASH = config.API_HASH
BOT_TOKEN = config.BOT_TOKEN
WASABI_ACCESS_KEY = config.WASABI_ACCESS_KEY
WASABI_SECRET_KEY = config.WASABI_SECRET_KEY
WASABI_BUCKET = config.WASABI_BUCKET
WASABI_REGION = config.WASABI_REGION
ADMIN_ID = config.ADMIN_ID

# Player URL configuration
RENDER_URL = os.getenv("RENDER_URL", "http://localhost:8000")
SUPPORTED_VIDEO_FORMATS = {'.mp4', '.mkv', '.avi', '.mov', '.wmv', '.flv', '.webm', '.m4v', '.3gp', '.mpeg', '.mpg'}

# In-memory storage for authorized user IDs
ALLOWED_USERS = {ADMIN_ID}

# Performance optimization settings
CHUNK_SIZE = 16 * 1024 * 1024  # 16MB chunks for parallel upload
MAX_WORKERS = min(32, (os.cpu_count() or 1) + 4)  # Optimal thread count
BUFFER_SIZE = 256 * 1024  # 256KB buffer for file operations

# Thread pool for parallel operations
thread_pool = ThreadPoolExecutor(max_workers=MAX_WORKERS)

# --- Callback Data Management ---
class CallbackData:
    """Manage callback data to avoid exceeding 64-byte limit"""
    def __init__(self):
        self.file_map = {}  # Maps short IDs to full filenames
        self.next_id = 1
    
    def store_file(self, filename):
        """Store filename and return short callback ID"""
        short_id = str(self.next_id)
        self.file_map[short_id] = filename
        self.next_id += 1
        # Simple cleanup to prevent memory leaks
        if len(self.file_map) > 1000:
            self.file_map.clear()
            self.next_id = 1
        return short_id
    
    def get_file(self, short_id):
        """Get filename from short ID"""
        return self.file_map.get(short_id)
    
    def clear_file(self, short_id):
        """Remove mapping when no longer needed"""
        if short_id in self.file_map:
            del self.file_map[short_id]

# Global callback data manager
callback_data = CallbackData()

# --- Bot & Wasabi Client Initialization ---
app = Client("wasabi_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# Optimized Boto3 S3 client for Wasabi
try:
    session = boto3.Session(
        aws_access_key_id=WASABI_ACCESS_KEY,
        aws_secret_access_key=WASABI_SECRET_KEY,
        region_name=WASABI_REGION
    )
    
    s3_client = session.client(
        's3',
        endpoint_url=f'https://s3.{WASABI_REGION}.wasabisys.com',
        config=boto3.session.Config(
            max_pool_connections=MAX_WORKERS,
            retries={'max_attempts': 5, 'mode': 'adaptive'},
            s3={'addressing_style': 'virtual', 'payload_signing_enabled': False},
            read_timeout=300,
            connect_timeout=30
        )
    )
    
    # Test connection with timeout
    s3_client.head_bucket(Bucket=WASABI_BUCKET)
    logger.info(f"✅ Successfully connected to Wasabi with {MAX_WORKERS} workers")
except Exception as e:
    logger.error(f"❌ Failed to connect to Wasabi: {e}")
    s3_client = None

# --- Performance Tracking ---
class TransferStats:
    def __init__(self):
        self.start_time = None
        self.bytes_transferred = 0
        self.last_update = 0
        
    def start(self):
        self.start_time = time.time()
        self.bytes_transferred = 0
        self.last_update = self.start_time
        
    def update(self, bytes_count):
        self.bytes_transferred += bytes_count
        self.last_update = time.time()
        
    def get_speed(self):
        if not self.start_time:
            return "0 B/s"
        elapsed = time.time() - self.start_time
        if elapsed == 0:
            return "0 B/s"
        speed = self.bytes_transferred / elapsed
        return self.human_speed(speed)
    
    def human_speed(self, speed):
        """Convert speed to human readable format"""
        for unit in ['B/s', 'KB/s', 'MB/s', 'GB/s']:
            if speed < 1024.0:
                return f"{speed:.2f} {unit}"
            speed /= 1024.0
        return f"{speed:.2f} TB/s"

# Global stats tracker
transfer_stats = TransferStats()

# --- Helpers & Decorators ---
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

def humanbytes(size):
    """Converts bytes to a human-readable format."""
    if not size:
        return "0B"
    size = int(size)
    power = 1024
    n = 0
    power_labels = {0: '', 1: 'K', 2: 'M', 3: 'G', 4: 'T'}
    while size > power and n < len(power_labels) - 1:
        size /= power
        n += 1
    return f"{size:.2f} {power_labels[n]}B"

def get_file_extension(filename):
    """Extract file extension in lowercase."""
    return os.path.splitext(filename)[1].lower()

def is_video_file(filename):
    """Check if file is a supported video format."""
    return get_file_extension(filename) in SUPPORTED_VIDEO_FORMATS

def get_file_type(filename):
    """Determine file type based on extension."""
    ext = get_file_extension(filename)
    if ext in SUPPORTED_VIDEO_FORMATS:
        return 'video'
    return 'other'

def generate_player_url(filename, presigned_url):
    """Generate player URL for supported file types."""
    if not RENDER_URL:
        return None
    file_type = get_file_type(filename)
    if file_type == 'video':
        encoded_url = base64.urlsafe_b64encode(presigned_url.encode()).decode().rstrip('=')
        return f"{RENDER_URL}/player/{file_type}/{encoded_url}"
    return None

def create_link_buttons(direct_url, player_url, filename):
    """Create beautiful inline buttons for links with proper callback data"""
    buttons = []
    
    # Store filename and get short callback ID
    file_id = callback_data.store_file(filename)
    
    # Always add direct download button
    if direct_url:
        buttons.append([
            InlineKeyboardButton("📥 Direct Download", url=direct_url)
        ])
    
    # Add player button for videos
    if player_url:
        buttons.append([
            InlineKeyboardButton("🎥 Stream Video", url=player_url)
        ])
    
    # Add copy buttons with short callback data
    if direct_url:
        buttons.append([
            InlineKeyboardButton("📋 Copy Direct", callback_data=f"cd_{file_id}"),
            InlineKeyboardButton("📋 Copy Player", callback_data=f"cp_{file_id}")
        ])
    
    # Add admin buttons for admin users
    buttons.append([
        InlineKeyboardButton("🗑 Delete File", callback_data=f"del_{file_id}"),
        InlineKeyboardButton("🔄 New Links", callback_data=f"ref_{file_id}")
    ])
    
    return InlineKeyboardMarkup(buttons)

def create_simple_buttons(direct_url, player_url, filename):
    """Create simple buttons for non-admin users"""
    buttons = []
    
    # Store filename and get short callback ID
    file_id = callback_data.store_file(filename)
    
    if direct_url:
        buttons.append([InlineKeyboardButton("📥 Direct Download", url=direct_url)])
    
    if player_url:
        buttons.append([InlineKeyboardButton("🎥 Stream Video", url=player_url)])
    
    if direct_url:
        buttons.append([
            InlineKeyboardButton("📋 Copy Direct", callback_data=f"cd_{file_id}"),
            InlineKeyboardButton("📋 Copy Player", callback_data=f"cp_{file_id}")
        ])
    
    return InlineKeyboardMarkup(buttons)

# --- Ultra-Fast Progress Callback ---
last_update_time = {}
progress_cache = {}

async def progress_callback(current, total, message, status, operation_type="download"):
    """High-performance progress updates with speed tracking."""
    chat_id = message.chat.id
    message_id = message.id
    
    # Update transfer stats
    if operation_type == "download":
        transfer_stats.update(current - progress_cache.get(message_id, 0))
    
    progress_cache[message_id] = current
    
    # Throttle UI updates (every 1 second or when complete)
    now = time.time()
    if (now - last_update_time.get(message_id, 0)) < 1.0 and current != total:
        return
    
    last_update_time[message_id] = now

    percentage = current * 100 / total
    progress_bar = "[{0}{1}]".format(
        '█' * int(percentage / 5),
        '░' * (20 - int(percentage / 5))
    )
    
    speed = transfer_stats.get_speed()
    
    details = (
        f"**{status}** 🚀\n"
        f"`{progress_bar}`\n"
        f"**Progress:** {percentage:.2f}%\n"
        f"**Speed:** {speed}\n"
        f"**Done:** {humanbytes(current)} / {humanbytes(total)}"
    )
    
    try:
        await app.edit_message_text(chat_id, message_id, text=details)
    except Exception as e:
        logger.debug(f"Progress update skipped: {e}")

# --- Ultra-Fast S3 Operations ---
async def upload_to_wasabi_parallel(file_path, file_name, status_message):
    """Ultra-fast parallel multipart upload with instant speeds"""
    try:
        file_size = os.path.getsize(file_path)
        
        # Use multipart upload for files larger than 50MB
        if file_size > 50 * 1024 * 1024:
            return await upload_multipart(file_path, file_name, file_size, status_message)
        else:
            return await upload_single(file_path, file_name, file_size, status_message)
            
    except Exception as e:
        logger.error(f"Upload failed: {e}")
        raise e

async def upload_multipart(file_path, file_name, file_size, status_message):
    """Multipart upload for large files - maximum speed"""
    try:
        # Create multipart upload
        mpu = s3_client.create_multipart_upload(
            Bucket=WASABI_BUCKET,
            Key=file_name,
            ContentType='application/octet-stream'
        )
        mpu_id = mpu['UploadId']
        
        # Calculate parts
        part_size = CHUNK_SIZE
        part_count = math.ceil(file_size / part_size)
        parts = []
        
        logger.info(f"Starting multipart upload: {part_count} parts")
        
        # Upload parts in parallel
        upload_tasks = []
        
        for part_num in range(1, part_count + 1):
            start = (part_num - 1) * part_size
            end = min(start + part_size, file_size)
            
            task = upload_part(
                file_path, file_name, mpu_id, part_num, start, end, status_message
            )
            upload_tasks.append(task)
        
        # Execute all uploads in parallel
        parts = await asyncio.gather(*upload_tasks)
        
        # Complete multipart upload
        s3_client.complete_multipart_upload(
            Bucket=WASABI_BUCKET,
            Key=file_name,
            UploadId=mpu_id,
            MultipartUpload={'Parts': parts}
        )
        
        logger.info("Multipart upload completed successfully")
        return True
        
    except Exception as e:
        # Abort upload on failure
        try:
            s3_client.abort_multipart_upload(
                Bucket=WASABI_BUCKET,
                Key=file_name,
                UploadId=mpu_id
            )
        except:
            pass
        raise e

async def upload_part(file_path, file_name, mpu_id, part_num, start, end, status_message):
    """Upload a single part with progress tracking"""
    loop = asyncio.get_event_loop()
    
    def _upload_part():
        with open(file_path, 'rb') as f:
            f.seek(start)
            data = f.read(end - start)
            
            response = s3_client.upload_part(
                Bucket=WASABI_BUCKET,
                Key=file_name,
                PartNumber=part_num,
                UploadId=mpu_id,
                Body=data
            )
            
            return {'ETag': response['ETag'], 'PartNumber': part_num}
    
    return await loop.run_in_executor(thread_pool, _upload_part)

async def upload_single(file_path, file_name, file_size, status_message):
    """Single upload for smaller files"""
    loop = asyncio.get_event_loop()
    
    class ProgressTracker:
        def __init__(self):
            self.uploaded = 0
            self.file_size = file_size
        
        def __call__(self, bytes_amount):
            self.uploaded += bytes_amount
            asyncio.run_coroutine_threadsafe(
                progress_callback(
                    self.uploaded, 
                    self.file_size, 
                    status_message, 
                    "🚀 Uploading...",
                    "upload"
                ),
                loop
            )
    
    progress_tracker = ProgressTracker()
    
    await loop.run_in_executor(
        thread_pool,
        lambda: s3_client.upload_file(
            file_path,
            WASABI_BUCKET,
            file_name,
            Callback=progress_tracker
        )
    )
    return True

async def generate_presigned_url(file_name):
    """Generate presigned URL with error handling."""
    try:
        return s3_client.generate_presigned_url(
            'get_object',
            Params={'Bucket': WASABI_BUCKET, 'Key': file_name},
            ExpiresIn=604800  # 7 days
        )
    except ClientError as e:
        logger.error(f"Failed to generate presigned URL: {e}")
        return None

# --- Optimized File Download ---
async def download_file_ultrafast(client, message, file_path, status_message):
    """Ultra-fast file download from Telegram"""
    try:
        # Start transfer stats
        transfer_stats.start()
        progress_cache[status_message.id] = 0
        
        await client.download_media(
            message=message,
            file_name=file_path,
            progress=progress_callback,
            progress_args=(status_message, "⬇️ Downloading...", "download")
        )
        
        # Clear progress cache
        if status_message.id in progress_cache:
            del progress_cache[status_message.id]
            
    except Exception as e:
        logger.error(f"Download failed: {e}")
        raise e

# --- Fixed Callback Query Handler ---
@app.on_callback_query()
async def handle_callback_query(client, callback_query):
    """Handle button callbacks with proper data validation"""
    user_id = callback_query.from_user.id
    data = callback_query.data
    message = callback_query.message
    
    try:
        # Parse callback data (format: "action_id")
        if '_' not in data:
            await callback_query.answer("❌ Invalid button data", show_alert=True)
            return
            
        action, file_id = data.split('_', 1)
        filename = callback_data.get_file(file_id)
        
        if not filename:
            await callback_query.answer("❌ File data expired", show_alert=True)
            return
        
        logger.info(f"Callback: {action} for file: {filename}")
        
        if action == "cd":  # Copy Direct
            if user_id not in ALLOWED_USERS:
                await callback_query.answer("⛔️ You are not authorized!", show_alert=True)
                return
                
            presigned_url = await generate_presigned_url(filename)
            
            if presigned_url:
                await callback_query.answer("📋 Direct link copied!", show_alert=False)
                # Send link as message
                await message.reply_text(
                    f"**Direct Download Link:**\n`{presigned_url}`",
                    reply_to_message_id=message.id
                )
            else:
                await callback_query.answer("❌ Failed to generate link", show_alert=True)
                
        elif action == "cp":  # Copy Player
            if user_id not in ALLOWED_USERS:
                await callback_query.answer("⛔️ You are not authorized!", show_alert=True)
                return
                
            presigned_url = await generate_presigned_url(filename)
            player_url = generate_player_url(filename, presigned_url) if presigned_url else None
            
            if player_url:
                await callback_query.answer("📋 Player link copied!", show_alert=False)
                await message.reply_text(
                    f"**Player URL:**\n{player_url}",
                    reply_to_message_id=message.id
                )
            else:
                await callback_query.answer("❌ Not a video file", show_alert=True)
                
        elif action == "del":  # Delete
            if user_id != ADMIN_ID:
                await callback_query.answer("⛔️ Only admin can delete!", show_alert=True)
                return
                
            try:
                s3_client.delete_object(Bucket=WASABI_BUCKET, Key=filename)
                await callback_query.answer("✅ File deleted!", show_alert=True)
                await message.edit_text(
                    f"🗑 **File Deleted**\n\n`{filename}` has been removed from storage.",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🔙 Back to Bot", url=f"https://t.me/{client.me.username}")]
                    ])
                )
                # Clean up callback data
                callback_data.clear_file(file_id)
            except Exception as e:
                await callback_query.answer(f"❌ Delete failed", show_alert=True)
                
        elif action == "ref":  # Refresh
            if user_id not in ALLOWED_USERS:
                await callback_query.answer("⛔️ You are not authorized!", show_alert=True)
                return
                
            await callback_query.answer("🔄 Generating fresh links...")
            
            # Generate new presigned URLs
            presigned_url = await generate_presigned_url(filename)
            player_url = generate_player_url(filename, presigned_url) if is_video_file(filename) else None
            
            if presigned_url:
                # Create appropriate buttons based on user role
                if user_id == ADMIN_ID:
                    keyboard = create_link_buttons(presigned_url, player_url, filename)
                else:
                    keyboard = create_simple_buttons(presigned_url, player_url, filename)
                
                # Update message with new buttons
                await message.edit_reply_markup(reply_markup=keyboard)
                await callback_query.answer("✅ Links refreshed!", show_alert=False)
            else:
                await callback_query.answer("❌ Failed to refresh links", show_alert=True)
                
        else:
            await callback_query.answer("❌ Unknown action", show_alert=True)
            
    except Exception as e:
        logger.error(f"Callback error: {e}")
        await callback_query.answer("❌ An error occurred", show_alert=True)

# --- Bot Command Handlers ---
@app.on_message(filters.command("start"))
async def start_handler(client: Client, message: Message):
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📁 Upload File", callback_data="upload_help")],
        [InlineKeyboardButton("ℹ️ Help", callback_data="help_info"),
         InlineKeyboardButton("👤 My ID", callback_data="my_id")],
        [InlineKeyboardButton("🚀 Speed Test", callback_data="speed_test")]
    ])
    
    await message.reply_text(
        f"🚀 **Ultra-Fast Wasabi Upload Bot**\n\n"
        f"**Your User ID:** `{message.from_user.id}`\n\n"
        "**Features:**\n"
        "• ⚡ Instant transfer speeds\n"
        "• 🎥 Video streaming player\n"
        "• 📱 One-click download buttons\n"
        "• 🔗 7-day direct links\n\n"
        "**Just send any file to start!**",
        reply_markup=keyboard
    )

@app.on_message(filters.command("help"))
async def help_handler(client: Client, message: Message):
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📁 Upload Guide", callback_data="upload_guide")],
        [InlineKeyboardButton("🎥 Player Guide", callback_data="player_guide")],
        [InlineKeyboardButton("⚡ Speed Tips", callback_data="speed_tips")],
        [InlineKeyboardButton("🔙 Main Menu", callback_data="main_menu")]
    ])
    
    help_text = """
🤖 **Ultra-Fast Wasabi Bot Help**

**Quick Start:**
1. Send any file to the bot
2. Get instant download buttons
3. Click buttons to download or stream

**Button Features:**
• 📥 Direct Download - Instant file download
• 🎥 Stream Video - Browser video player
• 📋 Copy Links - Get link text
• 🔄 New Links - Generate fresh URLs
• 🗑 Delete File - Remove from storage (Admin)

**Commands:**
/start - Show this menu
/help - Detailed help
/stats - Bot statistics (Admin)
/speedtest - Test upload speed
"""
    await message.reply_text(help_text, reply_markup=keyboard)

@app.on_message(filters.command("adduser"))
@is_admin
async def add_user_handler(client: Client, message: Message):
    try:
        user_id_to_add = int(message.text.split(" ", 1)[1])
        ALLOWED_USERS.add(user_id_to_add)
        
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("👥 List Users", callback_data="list_users")],
            [InlineKeyboardButton("🔙 Admin Panel", callback_data="admin_panel")]
        ])
        
        await message.reply_text(
            f"✅ User `{user_id_to_add}` has been added successfully.",
            reply_markup=keyboard
        )
    except (IndexError, ValueError):
        await message.reply_text("⚠️ **Usage:** /adduser `<user_id>`")

@app.on_message(filters.command("removeuser"))
@is_admin
async def remove_user_handler(client: Client, message: Message):
    try:
        user_id_to_remove = int(message.text.split(" ", 1)[1])
        if user_id_to_remove == ADMIN_ID:
            await message.reply_text("🚫 You cannot remove the admin.")
            return
        if user_id_to_remove in ALLOWED_USERS:
            ALLOWED_USERS.remove(user_id_to_remove)
            
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("👥 List Users", callback_data="list_users")],
                [InlineKeyboardButton("🔙 Admin Panel", callback_data="admin_panel")]
            ])
            
            await message.reply_text(
                f"🗑 User `{user_id_to_remove}` has been removed.",
                reply_markup=keyboard
            )
        else:
            await message.reply_text("🤷 User not found in the authorized list.")
    except (IndexError, ValueError):
        await message.reply_text("⚠️ **Usage:** /removeuser `<user_id>`")
        
@app.on_message(filters.command("listusers"))
@is_admin
async def list_users_handler(client: Client, message: Message):
    user_list = "\n".join([f"- `{user_id}`" for user_id in ALLOWED_USERS])
    
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Add User", callback_data="add_user_dialog")],
        [InlineKeyboardButton("➖ Remove User", callback_data="remove_user_dialog")],
        [InlineKeyboardButton("🔙 Admin Panel", callback_data="admin_panel")]
    ])
    
    await message.reply_text(
        f"👥 **Authorized Users:**\n{user_list}\n\n**Total:** {len(ALLOWED_USERS)} users",
        reply_markup=keyboard
    )

@app.on_message(filters.command("stats"))
@is_admin
async def stats_handler(client: Client, message: Message):
    """Show bot statistics"""
    stats_text = (
        f"🤖 **Ultra-Fast Bot Statistics**\n"
        f"• Authorized users: {len(ALLOWED_USERS)}\n"
        f"• Wasabi connected: {'✅' if s3_client else '❌'}\n"
        f"• Thread workers: {MAX_WORKERS}\n"
        f"• Chunk size: {humanbytes(CHUNK_SIZE)}\n"
        f"• Bucket: {WASABI_BUCKET}\n"
        f"• Region: {WASABI_REGION}\n"
        f"• Player URL: {RENDER_URL}"
    )
    
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Refresh Stats", callback_data="refresh_stats")],
        [InlineKeyboardButton("🚀 Speed Test", callback_data="speed_test")],
        [InlineKeyboardButton("🔙 Admin Panel", callback_data="admin_panel")]
    ])
    
    await message.reply_text(stats_text, reply_markup=keyboard)

@app.on_message(filters.command("speedtest"))
@is_authorized
async def speed_test_handler(client: Client, message: Message):
    """Test upload speed with a small file"""
    test_message = await message.reply_text("🚀 Starting speed test...")
    
    # Create a test file
    test_size = 10 * 1024 * 1024  # 10MB
    test_filename = f"speedtest_{int(time.time())}.bin"
    test_filepath = f"./downloads/{test_filename}"
    
    try:
        # Create test file with random data
        with open(test_filepath, 'wb') as f:
            f.write(os.urandom(test_size))
        
        # Upload with timing
        start_time = time.time()
        await upload_to_wasabi_parallel(test_filepath, test_filename, test_message)
        upload_time = time.time() - start_time
        
        speed = test_size / upload_time
        speed_human = transfer_stats.human_speed(speed)
        
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Test Again", callback_data="speed_test")],
            [InlineKeyboardButton("📊 More Stats", callback_data="more_stats")],
            [InlineKeyboardButton("🔙 Main Menu", callback_data="main_menu")]
        ])
        
        await test_message.edit_text(
            f"📊 **Speed Test Results**\n\n"
            f"• File Size: {humanbytes(test_size)}\n"
            f"• Upload Time: {upload_time:.2f}s\n"
            f"• Average Speed: {speed_human}\n"
            f"• Status: ✅ Ultra-Fast Mode Active",
            reply_markup=keyboard
        )
        
        # Cleanup
        os.remove(test_filepath)
        s3_client.delete_object(Bucket=WASABI_BUCKET, Key=test_filename)
        
    except Exception as e:
        await test_message.edit_text(f"❌ Speed test failed: {str(e)}")
        if os.path.exists(test_filepath):
            os.remove(test_filepath)

# --- Fixed File Handling with Proper Callback Data ---
@app.on_message(filters.document | filters.video | filters.audio)
@is_authorized
async def file_handler(client: Client, message: Message):
    if not s3_client:
        await message.reply_text("❌ **Error:** Wasabi client is not initialized.")
        return

    media = message.document or message.video or message.audio
    file_name = media.file_name
    file_size = media.file_size
    
    # Telegram's limit for bots is 2GB for download, 4GB for upload with MTProto API
    if file_size > 4 * 1024 * 1024 * 1024:
        await message.reply_text("❌ **Error:** File is larger than 4GB, which is not supported.")
        return

    status_message = await message.reply_text("🚀 Starting ultra-fast transfer...")
    
    # Create unique file path
    timestamp = int(time.time())
    safe_filename = f"{timestamp}_{file_name}"
    file_path = f"./downloads/{safe_filename}"
    os.makedirs(os.path.dirname(file_path), exist_ok=True)

    try:
        # 1. Ultra-fast download from Telegram
        await download_file_ultrafast(client, message, file_path, status_message)
        await status_message.edit_text("✅ Download complete. Starting instant upload...")

        # 2. Ultra-fast upload to Wasabi
        await upload_to_wasabi_parallel(file_path, safe_filename, status_message)
        await status_message.edit_text("✅ Upload complete! Generating links...")
        
        # 3. Generate URLs
        presigned_url = await generate_presigned_url(safe_filename)
        player_url = generate_player_url(safe_filename, presigned_url) if is_video_file(file_name) else None
        
        # 4. Create buttons based on user role with proper callback data
        if message.from_user.id == ADMIN_ID:
            keyboard = create_link_buttons(presigned_url, player_url, safe_filename)
        else:
            keyboard = create_simple_buttons(presigned_url, player_url, safe_filename)
        
        # 5. Prepare final message
        final_message = (
            f"✅ **File Uploaded Successfully!** ⚡\n\n"
            f"**File:** `{file_name}`\n"
            f"**Size:** {humanbytes(file_size)}\n"
            f"**Stored as:** `{safe_filename}`\n\n"
            f"**Links valid for 7 days**"
        )
        
        await status_message.edit_text(final_message, reply_markup=keyboard, disable_web_page_preview=True)

    except Exception as e:
        logger.error(f"Transfer failed: {e}", exc_info=True)
        await status_message.edit_text(f"❌ **Transfer failed:**\n`{str(e)}`")
    finally:
        # Cleanup
        if os.path.exists(file_path):
            os.remove(file_path)
        if status_message.id in last_update_time:
            del last_update_time[status_message.id]
        if status_message.id in progress_cache:
            del progress_cache[status_message.id]

# --- Player URL Generation Command ---
@app.on_message(filters.command("player"))
@is_authorized
async def player_url_handler(client: Client, message: Message):
    """Generate player URL for existing files in Wasabi"""
    try:
        filename = message.text.split(" ", 1)[1].strip()
        
        try:
            s3_client.head_object(Bucket=WASABI_BUCKET, Key=filename)
            
            if is_video_file(filename):
                presigned_url = await generate_presigned_url(filename)
                if presigned_url:
                    player_url = generate_player_url(filename, presigned_url)
                    
                    keyboard = InlineKeyboardMarkup([
                        [InlineKeyboardButton("🎥 Open Player", url=player_url)],
                        [InlineKeyboardButton("📥 Direct Download", url=presigned_url)],
                        [InlineKeyboardButton("📋 Copy Links", callback_data=f"copy_both_{filename}")]
                    ])
                    
                    await message.reply_text(
                        f"🎥 **Player URL for `{filename}`**\n\n"
                        f"**Instant streaming ready!**",
                        reply_markup=keyboard,
                        disable_web_page_preview=True
                    )
                else:
                    await message.reply_text("❌ Could not generate presigned URL.")
            else:
                await message.reply_text(
                    f"⚠️ `{filename}` is not a supported video format.\n"
                    f"Supported: {', '.join(SUPPORTED_VIDEO_FORMATS)}"
                )
                
        except ClientError as e:
            if e.response['Error']['Code'] == '404':
                await message.reply_text(f"❌ File `{filename}` not found.")
            else:
                await message.reply_text(f"❌ Error: {e.response['Error']['Message']}")
                
    except IndexError:
        await message.reply_text("⚠️ **Usage:** /player `<filename>`")

# -----------------------------
# Flask app for player.html
# -----------------------------
flask_app = Flask(__name__, template_folder="templates")

@flask_app.route("/")
def index():
    return render_template("index.html")

@flask_app.route("/player/<media_type>/<encoded_url>")
def player(media_type, encoded_url):
    try:
        padding = 4 - (len(encoded_url) % 4)
        if padding != 4:
            encoded_url += '=' * padding
        media_url = base64.urlsafe_b64decode(encoded_url).decode()
        return render_template("player.html", media_type=media_type, media_url=media_url)
    except Exception as e:
        return f"Error decoding URL: {str(e)}", 400

@flask_app.route("/about")
def about():
    return render_template("about.html")

def run_flask():
    flask_app.run(host="0.0.0.0", port=8000, debug=False)

# -----------------------------
# Flask Server Startup
# -----------------------------
print("🚀 Starting Ultra-Fast Bot with Flask server...")
Thread(target=run_flask, daemon=True).start()

# --- Main Execution ---
if __name__ == "__main__":
    logger.info("⚡ Ultra-Fast Bot is starting...")
    logger.info(f"🎯 Performance Settings:")
    logger.info(f"   - Thread Workers: {MAX_WORKERS}")
    logger.info(f"   - Chunk Size: {humanbytes(CHUNK_SIZE)}")
    logger.info(f"   - Buffer Size: {humanbytes(BUFFER_SIZE)}")
    logger.info(f"   - Player URL: {RENDER_URL}")
    
    app.run()
    logger.info("Bot has stopped.")
