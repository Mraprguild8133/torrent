import os
import asyncio
import time
import logging
from urllib.parse import quote
from typing import Optional

import boto3
from botocore.client import Config
from botocore.exceptions import NoCredentialsError, ClientError

from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import FloodWait, RPCError

from config import config

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize Pyrogram Client
app = Client(
    "wasabi_storage_bot",
    api_id=config.API_ID,
    api_hash=config.API_HASH,
    bot_token=config.BOT_TOKEN
)

# Initialize Boto3 S3 Client for Wasabi
s3_client = None
try:
    if all([config.WASABI_ACCESS_KEY, config.WASABI_SECRET_KEY, config.WASABI_BUCKET]):
        s3_client = boto3.client(
            's3',
            endpoint_url=config.WASABI_ENDPOINT_URL,
            aws_access_key_id=config.WASABI_ACCESS_KEY,
            aws_secret_access_key=config.WASABI_SECRET_KEY,
            config=Config(signature_version='s3v4'),
            region_name=config.WASABI_REGION
        )
        logger.info("Wasabi client initialized successfully.")
    else:
        logger.warning("Wasabi credentials not complete. Please check environment variables.")
except Exception as e:
    logger.error(f"Error initializing Wasabi client: {e}")
    s3_client = None


# --- Helper Functions ---
def humanbytes(size: float) -> str:
    """Converts bytes to a human-readable format."""
    if not size:
        return "0 B"
    
    power = 1024
    n = 0
    power_labels = {0: 'B', 1: 'KB', 2: 'MB', 3: 'GB', 4: 'TB'}
    
    while size >= power and n < len(power_labels) - 1:
        size /= power
        n += 1
        
    return f"{size:.2f} {power_labels[n]}"


class ProgressTracker:
    """Track upload/download progress with rate limiting."""
    def __init__(self, message: Message, action: str):
        self.message = message
        self.action = action
        self.start_time = time.time()
        self.last_update_time = 0
        self.update_interval = 3  # Update every 3 seconds to avoid flood waits

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
            logger.error(f"Error updating progress: {e}")


async def download_with_progress(client, message: Message, status_message: Message) -> Optional[str]:
    """Download file from Telegram with progress tracking."""
    progress_tracker = ProgressTracker(status_message, "Downloading")
    
    def progress(current, total):
        asyncio.create_task(progress_tracker.update(current, total))
    
    try:
        file_path = await message.download(progress=progress)
        return file_path
    except Exception as e:
        await status_message.edit_text(f"❌ Download failed: {str(e)}")
        return None


async def upload_to_wasabi(file_path: str, file_name: str, file_size: int, status_message: Message) -> bool:
    """Upload file to Wasabi storage."""
    if not s3_client:
        await status_message.edit_text("❌ Wasabi client not configured")
        return False

    progress_tracker = ProgressTracker(status_message, "Uploading")
    
    try:
        # For upload progress, we need to use a different approach since boto3 doesn't support async callbacks
        # We'll use a thread-based approach with a custom callback
        import threading
        import queue
        
        progress_queue = queue.Queue()
        
        def upload_progress_callback(bytes_transferred):
            progress_queue.put(bytes_transferred)
        
        def upload_thread():
            try:
                s3_client.upload_file(
                    file_path,
                    config.WASABI_BUCKET,
                    file_name,
                    Callback=upload_progress_callback
                )
                progress_queue.put(-1)  # Signal completion
            except Exception as e:
                progress_queue.put(("error", str(e)))  # Signal error
        
        # Start upload in a separate thread
        thread = threading.Thread(target=upload_thread)
        thread.daemon = True
        thread.start()
        
        # Monitor progress
        bytes_uploaded = 0
        start_time = time.time()
        timeout = 300  # 5 minute timeout
        
        while True:
            try:
                data = progress_queue.get(timeout=timeout)
                
                if data == -1:
                    # Upload completed
                    break
                elif isinstance(data, tuple) and data[0] == "error":
                    # Upload error
                    raise Exception(data[1])
                else:
                    bytes_uploaded = data
                    await progress_tracker.update(bytes_uploaded, file_size)
                    
            except queue.Empty:
                await status_message.edit_text("❌ Upload timeout")
                return False
                
        return True
        
    except Exception as e:
        await status_message.edit_text(f"❌ Upload failed: {str(e)}")
        return False


def generate_streaming_link(file_name: str) -> str:
    """Generate public streaming link for the file."""
    encoded_file_name = quote(file_name)
    return f"https://{config.WASABI_BUCKET}.s3.{config.WASABI_REGION}.wasabisys.com/{encoded_file_name}"


# --- Bot Command Handlers ---
@app.on_message(filters.command("start"))
async def start_handler(_, message: Message):
    """Handles the /start command."""
    try:
        await message.reply_text(
            "**Welcome to the Advanced File Storage Bot!** 🚀\n\n"
            "Send me any file, and I will upload it to secure Wasabi storage "
            "and provide you with a high-speed, shareable streaming link.\n\n"
            "**Features:**\n"
            "• Handles files up to 5GB\n"
            "• High-speed uploads and downloads\n"
            "• Secure and reliable storage\n"
            "• Links compatible with VLC, MX Player, and more\n\n"
            "**Simply send a file to get started!**",
            quote=True,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("👨‍💻 Developer", url="https://t.me/tprojects")],
                [InlineKeyboardButton("📚 Help", callback_data="help")]
            ])
        )
    except Exception as e:
        logger.error(f"Error in start handler: {e}")


@app.on_message(filters.command("help"))
async def help_handler(_, message: Message):
    """Handles the /help command."""
    try:
        await message.reply_text(
            "**How to use this bot:**\n\n"
            "1. **Send any file** (document, video, audio)\n"
            "2. Wait for the upload to complete\n"
            "3. Get your **streaming link**\n\n"
            "**Supported formats:**\n"
            "• Videos (MP4, MKV, AVI, etc.)\n"
            "• Audio (MP3, FLAC, WAV, etc.)\n"
            "• Documents (PDF, ZIP, etc.)\n\n"
            "**Maximum file size:** 5GB\n\n"
            "Start by sending me a file! 📁",
            quote=True
        )
    except Exception as e:
        logger.error(f"Error in help handler: {e}")


@app.on_message(filters.command("status"))
async def status_handler(_, message: Message):
    """Check bot and Wasabi status."""
    try:
        status_text = "**Bot Status:** 🟢 Online\n"
        
        if s3_client:
            try:
                # Test Wasabi connection
                s3_client.head_bucket(Bucket=config.WASABI_BUCKET)
                status_text += "**Wasabi Storage:** 🟢 Connected\n"
                status_text += f"**Bucket:** `{config.WASABI_BUCKET}`\n"
                status_text += f"**Region:** `{config.WASABI_REGION}`\n"
            except Exception as e:
                status_text += f"**Wasabi Storage:** 🔴 Error: {str(e)}\n"
        else:
            status_text += "**Wasabi Storage:** 🔴 Not Configured\n"
        
        await message.reply_text(status_text, quote=True)
    except Exception as e:
        logger.error(f"Error in status handler: {e}")


# --- File Handling Logic ---
@app.on_message(filters.document | filters.video | filters.audio)
async def file_handler(client, message: Message):
    """Handles incoming files, uploads them to Wasabi, and returns a link."""
    try:
        if not s3_client:
            await message.reply_text("❌ Bot is not configured correctly. Wasabi client is unavailable.")
            return

        media = message.document or message.video or message.audio
        if not media:
            await message.reply_text("❌ Unsupported file type.")
            return

        file_name = media.file_name or "unnamed_file"
        file_size = media.file_size or 0
        
        if file_size > config.MAX_FILE_SIZE:
            await message.reply_text(
                f"❌ File is too large. The maximum supported size is {humanbytes(config.MAX_FILE_SIZE)}."
            )
            return
            
        status_message = await message.reply_text(
            f"**Starting processing...**\n"
            f"**File:** `{file_name}`\n"
            f"**Size:** `{humanbytes(file_size)}`",
            quote=True
        )

        # 1. Download from Telegram
        file_path = await download_with_progress(client, message, status_message)
        if not file_path:
            return

        # 2. Upload to Wasabi
        upload_success = await upload_to_wasabi(file_path, file_name, file_size, status_message)
        if not upload_success:
            if os.path.exists(file_path):
                os.remove(file_path)
            return

        # 3. Generate Shareable Link
        try:
            streaming_link = generate_streaming_link(file_name)
            
            success_message = (
                f"**✅ File Uploaded Successfully!**\n\n"
                f"**📁 File Name:** `{file_name}`\n"
                f"**📊 File Size:** `{humanbytes(file_size)}`\n"
                f"**🔗 Streaming Link Ready**\n\n"
                f"*The link is compatible with most media players and browsers.*"
            )
            
            await status_message.edit_text(
                success_message,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🎬 Streaming Link", url=streaming_link)],
                    [InlineKeyboardButton("🌐 Open in Browser", url=streaming_link)],
                    [InlineKeyboardButton("📱 Open in VLC", url=f"vlc://{streaming_link}")]
                ])
            )
            
        except Exception as e:
            await status_message.edit_text(f"❌ Could not generate shareable link: {str(e)}")
        finally:
            # Clean up the downloaded file
            if os.path.exists(file_path):
                os.remove(file_path)
                
    except FloodWait as e:
        logger.warning(f"Flood wait: {e.value} seconds")
        await asyncio.sleep(e.value)
    except Exception as e:
        logger.error(f"Error in file handler: {e}")
        try:
            await message.reply_text("❌ An error occurred while processing your file.")
        except:
            pass


@app.on_callback_query(filters.regex("^help$"))
async def help_callback(_, query):
    """Handle help callback."""
    try:
        await query.message.edit_text(
            "**Need Help?**\n\n"
            "Just send me any file and I'll handle the rest!\n\n"
            "**Tips:**\n"
            "• For best performance, use stable internet connection\n"
            "• Large files will take longer to process\n"
            "• Streaming links work with most media players\n\n"
            "Try sending a file now!",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Back", callback_data="back_to_start")]
            ])
        )
    except Exception as e:
        logger.error(f"Error in help callback: {e}")


@app.on_callback_query(filters.regex("^back_to_start$"))
async def back_callback(_, query):
    """Handle back to start callback."""
    try:
        await query.message.edit_text(
            "**Welcome to the Advanced File Storage Bot!** 🚀\n\n"
            "Send me any file, and I will upload it to secure Wasabi storage "
            "and provide you with a high-speed, shareable streaming link.\n\n"
            "**Features:**\n"
            "• Handles files up to 5GB\n"
            "• High-speed uploads and downloads\n"
            "• Secure and reliable storage\n"
            "• Links compatible with VLC, MX Player, and more\n\n"
            "**Simply send a file to get started!**",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("👨‍💻 Developer", url="https://t.me/tprojects")],
                [InlineKeyboardButton("📚 Help", callback_data="help")]
            ])
        )
    except Exception as e:
        logger.error(f"Error in back callback: {e}")


# --- Start the Bot ---
if __name__ == "__main__":
    logger.info("🤖 Bot is starting...")
    
    # Validate required configuration
    if not all([config.API_ID, config.API_HASH, config.BOT_TOKEN]):
        logger.error("❌ Missing Telegram API configuration")
        exit(1)
        
    if not s3_client:
        logger.warning("⚠️  Wasabi storage not configured - file uploads will not work")
    
    try:
        app.run()
        logger.info("✅ Bot stopped gracefully")
    except Exception as e:
        logger.error(f"❌ Bot crashed: {e}")
