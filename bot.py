import os
import time
import math
import asyncio
import logging
import base64  # Missing import
from functools import wraps
from urllib.parse import quote

import boto3
from botocore.exceptions import ClientError
from pyrogram import Client, filters
from pyrogram.types import Message

# Import configuration
from config import config

# --- Configuration ---
# Set up basic logging
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

# Player URL configuration - Using Render URL
RENDER_URL = os.getenv("RENDER_URL", "http://localhost:8000")
SUPPORTED_VIDEO_FORMATS = {'.mp4', '.mkv', '.avi', '.mov', '.wmv', '.flv', '.webm', '.m4v', '.3gp', '.mpeg', '.mpg'}

# In-memory storage for authorized user IDs. Starts with the admin.
# For persistence, consider using a database or a file.
ALLOWED_USERS = {ADMIN_ID}

# --- Bot & Wasabi Client Initialization ---
app = Client("wasabi_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# Boto3 S3 client for Wasabi
try:
    s3_client = boto3.client(
        's3',
        endpoint_url=f'https://s3.{WASABI_REGION}.wasabisys.com',
        aws_access_key_id=WASABI_ACCESS_KEY,
        aws_secret_access_key=WASABI_SECRET_KEY,
        region_name=WASABI_REGION,
        config=boto3.session.Config(
            s3={'addressing_style': 'virtual'},
            retries={'max_attempts': 3, 'mode': 'standard'}
        )
    )
    # Test connection
    s3_client.head_bucket(Bucket=WASABI_BUCKET)
    logger.info("Successfully connected to Wasabi.")
except Exception as e:
    logger.error(f"Failed to connect to Wasabi: {e}")
    s3_client = None

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
    while size > power and n < len(power_labels) -1 :
        size /= power
        n += 1
    return f"{size:.2f} {power_labels[n]}B"

def get_file_extension(filename):
    """Extract file extension in lowercase."""
    return os.path.splitext(filename)[1].lower()  # Fixed: Removed incorrect SUPPORTED_VIDEO_FORMATS

def is_video_file(filename):
    """Check if file is a supported video format."""
    return get_file_extension(filename) in SUPPORTED_VIDEO_FORMATS

def get_file_type(filename):
    """Determine file type based on extension."""
    ext = get_file_extension(filename)
    if ext in SUPPORTED_VIDEO_FORMATS:
        return 'video'
    # Add more file type mappings as needed
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

# --- Progress Callback Management ---
last_update_time = {}

async def progress_callback(current, total, message, status):
    """Updates the progress message in Telegram."""
    chat_id = message.chat.id
    message_id = message.id
    
    # Throttle updates to avoid hitting Telegram API limits
    now = time.time()
    if (now - last_update_time.get(message_id, 0)) < 2 and current != total:
        return
    last_update_time[message_id] = now

    percentage = current * 100 / total
    progress_bar = "[{0}{1}]".format(
        '█' * int(percentage / 5),
        ' ' * (20 - int(percentage / 5))
    )
    
    details = (
        f"**{status}**\n"
        f"`{progress_bar}`\n"
        f"**Progress:** {percentage:.2f}%\n"
        f"**Done:** {humanbytes(current)}\n"
        f"**Total:** {humanbytes(total)}"
    )
    
    try:
        await app.edit_message_text(chat_id, message_id, text=details)
    except Exception as e:
        logger.warning(f"Failed to edit message: {e}")

# --- Enhanced S3 Operations ---
async def upload_to_wasabi(file_path, file_name, status_message):
    """Upload file to Wasabi with retry logic and progress tracking."""
    max_retries = 3
    base_delay = 2
    
    for attempt in range(max_retries):
        try:
            loop = asyncio.get_event_loop()
            
            class ProgressTracker:
                def __init__(self):
                    self.uploaded = 0
                    self.file_size = os.path.getsize(file_path)
                
                def __call__(self, bytes_amount):
                    self.uploaded += bytes_amount
                    # Use thread-safe coroutine execution
                    asyncio.run_coroutine_threadsafe(
                        progress_callback(
                            self.uploaded, 
                            self.file_size, 
                            status_message, 
                            f"Uploading... (Attempt {attempt + 1}/{max_retries})"
                        ),
                        loop
                    )
            
            progress_tracker = ProgressTracker()
            
            # Upload file using threads
            await loop.run_in_executor(
                None,
                lambda: s3_client.upload_file(
                    file_path,
                    WASABI_BUCKET,
                    file_name,
                    Callback=progress_tracker
                )
            )
            return True
            
        except ClientError as e:
            error_code = e.response['Error']['Code']
            logger.warning(f"Upload attempt {attempt + 1} failed: {error_code}")
            
            if attempt == max_retries - 1:  # Last attempt
                raise e
                
            # Exponential backoff
            delay = base_delay * (2 ** attempt)
            await status_message.edit_text(
                f"⚠️ Upload failed (attempt {attempt + 1}/{max_retries}). "
                f"Retrying in {delay} seconds..."
            )
            await asyncio.sleep(delay)
    
    return False

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

# --- Bot Command Handlers ---
@app.on_message(filters.command("start"))
async def start_handler(client: Client, message: Message):
    await message.reply_text(
        f"👋 Welcome!\n\nThis bot can upload files to Wasabi storage.\n"
        f"Your User ID is: `{message.from_user.id}`\n\n"
        "Send me any file if you are an authorized user.\n\n"
        "**Features:**\n"
        "• Direct download links\n"
        "• Video player URLs for streaming\n"
        "• Progress tracking\n"
        "• 7-day link validity"
    )

@app.on_message(filters.command("help"))
async def help_handler(client: Client, message: Message):
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
Video files get special player URLs that work with our Render video player.
"""
    await message.reply_text(help_text)

@app.on_message(filters.command("adduser"))
@is_admin
async def add_user_handler(client: Client, message: Message):
    try:
        user_id_to_add = int(message.text.split(" ", 1)[1])
        ALLOWED_USERS.add(user_id_to_add)
        await message.reply_text(f"✅ User `{user_id_to_add}` has been added successfully.")
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
            await message.reply_text(f"🗑 User `{user_id_to_remove}` has been removed.")
        else:
            await message.reply_text("🤷 User not found in the authorized list.")
    except (IndexError, ValueError):
        await message.reply_text("⚠️ **Usage:** /removeuser `<user_id>`")
        
@app.on_message(filters.command("listusers"))
@is_admin
async def list_users_handler(client: Client, message: Message):
    user_list = "\n".join([f"- `{user_id}`" for user_id in ALLOWED_USERS])
    await message.reply_text(f"👥 **Authorized Users:**\n{user_list}")

@app.on_message(filters.command("stats"))
@is_admin
async def stats_handler(client: Client, message: Message):
    """Show bot statistics"""
    stats_text = (
        f"🤖 **Bot Statistics**\n"
        f"• Authorized users: {len(ALLOWED_USERS)}\n"
        f"• Wasabi connected: {'✅' if s3_client else '❌'}\n"
        f"• Bucket: {WASABI_BUCKET}\n"
        f"• Region: {WASABI_REGION}\n"
        f"• Player URL: {RENDER_URL}"
    )
    await message.reply_text(stats_text)

# --- File Handling Logic ---
@app.on_message(filters.document | filters.video | filters.audio)
@is_authorized
async def file_handler(client: Client, message: Message):
    if not s3_client:
        await message.reply_text("❌ **Error:** Wasabi client is not initialized. Check server logs.")
        return

    media = message.document or message.video or message.audio
    file_name = media.file_name
    file_size = media.file_size
    
    # Telegram's limit for bots is 2GB for download, 4GB for upload with MTProto API
    if file_size > 4 * 1024 * 1024 * 1024:
        await message.reply_text("❌ **Error:** File is larger than 4GB, which is not supported.")
        return

    status_message = await message.reply_text("🚀 Preparing to process your file...")
    
    # Create unique file path to avoid conflicts
    timestamp = int(time.time())
    safe_filename = f"{timestamp}_{file_name}"
    file_path = f"./downloads/{safe_filename}"
    os.makedirs(os.path.dirname(file_path), exist_ok=True)

    try:
        # 1. Download from Telegram
        await client.download_media(
            message=message,
            file_name=file_path,
            progress=progress_callback,
            progress_args=(status_message, "Downloading...")
        )
        await status_message.edit_text("✅ Download complete. Starting upload to Wasabi...")

        # 2. Upload to Wasabi
        await upload_to_wasabi(file_path, safe_filename, status_message)
        await status_message.edit_text("✅ Upload complete. Generating shareable link...")
        
        # 3. Generate a pre-signed URL (valid for 7 days)
        presigned_url = await generate_presigned_url(safe_filename)
        
        # 4. Generate player URL for video files - FIXED: using correct function name
        player_url = None
        if is_video_file(file_name) and presigned_url:
            player_url = generate_player_url(safe_filename, presigned_url)  # Fixed function name
        
        # 5. Prepare final message
        if presigned_url:
            final_message = (
                f"✅ **File Uploaded Successfully!**\n\n"
                f"**File:** `{file_name}`\n"
                f"**Size:** {humanbytes(file_size)}\n"
                f"**Stored as:** `{safe_filename}`\n"
                f"**Direct Link (7 days):**\n`{presigned_url}`\n"
            )
            
            # Add player URL for videos
            if player_url:
                final_message += f"\n**🎥 Player URL:**\n{player_url}"
            
            await status_message.edit_text(final_message, disable_web_page_preview=False)
        else:
            error_message = (
                f"✅ **File Uploaded Successfully!**\n\n"
                f"**File:** `{file_name}`\n"
                f"**Size:** {humanbytes(file_size)}\n"
                f"**Stored as:** `{safe_filename}`\n"
                f"⚠️ *Could not generate shareable link*"
            )
            if player_url:
                error_message += f"\n\n**🎥 Player URL:**\n{player_url}"
            
            await status_message.edit_text(error_message, disable_web_page_preview=False)

    except Exception as e:
        logger.error(f"An error occurred during file processing: {e}", exc_info=True)
        await status_message.edit_text(f"❌ **Upload failed:**\n`{str(e)}`")
    finally:
        # 6. Cleanup
        if os.path.exists(file_path):
            os.remove(file_path)
            logger.info(f"Cleaned up local file: {file_path}")
        if status_message.id in last_update_time:
             del last_update_time[status_message.id]

# --- Player URL Generation Command ---
@app.on_message(filters.command("player"))
@is_authorized
async def player_url_handler(client: Client, message: Message):
    """Generate player URL for existing files in Wasabi"""
    try:
        filename = message.text.split(" ", 1)[1].strip()
        
        # Check if file exists in Wasabi
        try:
            s3_client.head_object(Bucket=WASABI_BUCKET, Key=filename)
            
            if is_video_file(filename):
                presigned_url = await generate_presigned_url(filename)
                if presigned_url:
                    player_url = generate_player_url(filename, presigned_url)  # Fixed function name
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

# --- Main Execution ---
if __name__ == "__main__":
    logger.info("Bot is starting...")
    logger.info(f"Player base URL: {RENDER_URL}")
    logger.info(f"Supported video formats: {SUPPORTED_VIDEO_FORMATS}")
    app.run()
    logger.info("Bot has stopped.")
