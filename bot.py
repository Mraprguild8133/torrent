import os
import time
import asyncio
import hashlib
import boto3
from botocore.exceptions import NoCredentialsError, ClientError
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton

from config import (
    API_ID, API_HASH, BOT_TOKEN, ADMIN_ID,
    WASABI_ACCESS_KEY, WASABI_SECRET_KEY, 
    WASABI_BUCKET, WASABI_REGION, WASABI_ENDPOINT_URL
)

# Initialize Pyrogram Client
app = Client(
    "wasabi_uploader_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN
)

# Initialize Boto3 S3 Client for Wasabi
try:
    s3_client = boto3.client(
        's3',
        endpoint_url=WASABI_ENDPOINT_URL,
        aws_access_key_id=WASABI_ACCESS_KEY,
        aws_secret_access_key=WASABI_SECRET_KEY,
        region_name=WASABI_REGION
    )
    # Test connection by listing buckets
    s3_client.list_buckets()
    print("✅ Successfully connected to Wasabi")
except NoCredentialsError:
    print("❌ Wasabi credentials not found")
    exit(1)
except ClientError as e:
    print(f"❌ Failed to connect to Wasabi: {e}")
    exit(1)

# Store file information temporarily (in production, use a database)
file_store = {}

# --- Helper Functions ---
def humanbytes(size):
    """Converts bytes to a human-readable format."""
    if not size or size == 0:
        return "0B"
    power = 1024
    power_dict = {0: "B", 1: "KB", 2: "MB", 3: "GB", 4: "TB"}
    
    for i in range(len(power_dict)):
        if size < power ** (i + 1) or i == len(power_dict) - 1:
            return f"{size / (power ** i):.2f} {power_dict[i]}"

def generate_file_id(file_name):
    """Generate a short unique ID for the file to use in callback data"""
    return hashlib.md5(f"{file_name}_{time.time()}".encode()).hexdigest()[:16]

class ProgressTracker:
    """Track progress for individual uploads/downloads"""
    def __init__(self):
        self.last_update_time = 0
        self.start_time = 0
    
    async def progress_callback(self, current, total, message: Message, operation: str):
        """Progress callback to show real-time status"""
        current_time = time.time()
        
        # Update every 3 seconds to avoid being rate-limited
        if current_time - self.last_update_time < 3:
            return
        
        self.last_update_time = current_time
        
        if total == 0:
            percentage = 0
        else:
            percentage = current * 100 / total
        
        elapsed_time = current_time - self.start_time
        if elapsed_time > 0:
            speed = current / elapsed_time
        else:
            speed = 0
        
        # Progress bar visualization
        filled_blocks = int(percentage / 5)
        empty_blocks = 20 - filled_blocks
        progress_bar = f"[{'█' * filled_blocks}{'░' * empty_blocks}]"
        
        # Status message formatting
        status_text = (
            f"**{operation}**\n"
            f"{progress_bar} {percentage:.2f}%\n"
            f"**Progress:** {humanbytes(current)} / {humanbytes(total)}\n"
            f"**Speed:** {humanbytes(speed)}/s\n"
            f"**Elapsed:** {int(elapsed_time)}s"
        )
        
        try:
            await message.edit_text(status_text)
        except Exception:
            # Ignore errors if message can't be edited
            pass

# Create progress tracker instance
progress_tracker = ProgressTracker()

# --- Bot Command Handlers ---
@app.on_message(filters.command("start") & filters.private)
async def start_handler(client, message: Message):
    """Handler for the /start command."""
    if message.from_user.id != ADMIN_ID:
        await message.reply_text("❌ Sorry, you are not authorized to use this bot.")
        return
        
    await message.reply_text(
        "**🤖 Welcome to the Wasabi Uploader Bot!**\n\n"
        "I can handle files up to 4GB. Simply send me any file, and I will:\n"
        "1. 📥 Download it from Telegram\n"
        "2. ☁️ Upload it to Wasabi cloud storage\n"
        "3. 🔗 Provide you with a direct, streamable link\n\n"
        "**Note:** This bot is for authorized users only.\n"
        "Use /status to check bot connectivity."
    )

@app.on_message(filters.command("status") & filters.private)
async def status_handler(client, message: Message):
    """Check bot status"""
    if message.from_user.id != ADMIN_ID:
        await message.reply_text("❌ Unauthorized")
        return
    
    try:
        # Test Wasabi connection
        s3_client.list_buckets()
        status_msg = "✅ **Bot Status:** Online\n✅ **Wasabi Connection:** Working"
    except Exception as e:
        status_msg = f"✅ **Bot Status:** Online\n❌ **Wasabi Connection:** Failed - {e}"
    
    await message.reply_text(status_msg)

@app.on_message(filters.command("cleanup") & filters.private)
async def cleanup_handler(client, message: Message):
    """Cleanup stored file data"""
    if message.from_user.id != ADMIN_ID:
        await message.reply_text("❌ Unauthorized")
        return
    
    global file_store
    count = len(file_store)
    # Remove old entries (older than 1 hour)
    current_time = time.time()
    file_store = {k: v for k, v in file_store.items() if current_time - v['timestamp'] < 3600}
    
    await message.reply_text(f"🧹 Cleaned up {count - len(file_store)} old entries. {len(file_store)} entries remain.")

@app.on_message((filters.document | filters.video | filters.audio | filters.photo) & filters.private)
async def file_handler(client, message: Message):
    """Main handler for processing incoming files."""
    if message.from_user.id != ADMIN_ID:
        await message.reply_text("❌ You are not authorized to send files.")
        return

    # Get file information
    if message.document:
        media = message.document
        file_name = media.file_name
    elif message.video:
        media = message.video
        file_name = media.file_name or f"video_{message.id}.mp4"
    elif message.audio:
        media = message.audio
        file_name = media.file_name or f"audio_{message.id}.mp3"
    elif message.photo:
        media = message.photo
        file_name = f"photo_{message.id}.jpg"
    else:
        await message.reply_text("❌ Unsupported file type.")
        return

    file_size = media.file_size
    
    # Inform user that the process has started
    status_message = await message.reply_text(
        f"**📁 Processing File**\n"
        f"**Name:** `{file_name}`\n"
        f"**Size:** {humanbytes(file_size)}\n"
        f"**Status:** Starting download..."
    )
    
    downloaded_file_path = None
    
    try:
        # 1. Download from Telegram
        progress_tracker.start_time = time.time()
        progress_tracker.last_update_time = 0
        
        downloaded_file_path = await message.download(
            file_name=file_name,
            progress=progress_tracker.progress_callback,
            progress_args=(status_message, "📥 Downloading from Telegram")
        )
        
        if not downloaded_file_path:
            await status_message.edit_text("❌ Failed to download file: No file path returned")
            return
            
        await status_message.edit_text("✅ File downloaded successfully from Telegram.\n**Status:** Starting upload to Wasabi...")
        
        # 2. Upload to Wasabi
        progress_tracker.start_time = time.time()
        progress_tracker.last_update_time = 0
        
        # Upload file to Wasabi
        await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: s3_client.upload_file(
                downloaded_file_path,
                WASABI_BUCKET,
                file_name
            )
        )
        
        await status_message.edit_text("✅ File uploaded successfully to Wasabi.\n**Status:** Generating shareable link...")
        
        # 3. Generate a pre-signed shareable link
        presigned_url = s3_client.generate_presigned_url(
            'get_object',
            Params={'Bucket': WASABI_BUCKET, 'Key': file_name},
            ExpiresIn=604800  # Link expires in 7 days
        )
        
        # 4. Generate a unique file ID for callback data
        file_id = generate_file_id(file_name)
        file_store[file_id] = {
            'file_name': file_name,
            'presigned_url': presigned_url,
            'timestamp': time.time()
        }
        
        # 5. Send success message with links
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔗 Direct Download", url=presigned_url)],
            [InlineKeyboardButton("📋 Copy URL", callback_data=f"url_{file_id}")]
        ])
        
        final_message = (
            f"✅ **File Uploaded Successfully!**\n\n"
            f"**📁 File:** `{file_name}`\n"
            f"**💾 Size:** {humanbytes(file_size)}\n"
            f"**⏰ Link Expires:** 7 days\n\n"
            f"Use the buttons below to access your file:"
        )
        
        await message.reply_text(final_message, reply_markup=markup, quote=True)
        await status_message.delete()
        
    except Exception as e:
        error_msg = f"❌ Error processing file: {str(e)}"
        try:
            await status_message.edit_text(error_msg)
        except:
            await message.reply_text(error_msg)
        
    finally:
        # 6. Clean up the downloaded file
        if downloaded_file_path and os.path.exists(downloaded_file_path):
            try:
                os.remove(downloaded_file_path)
            except Exception as e:
                print(f"Warning: Could not delete temporary file: {e}")

@app.on_callback_query(filters.regex("^url_"))
async def copy_url_callback(client, callback_query):
    """Handle copy URL callback"""
    file_id = callback_query.data.replace("url_", "")
    
    if file_id not in file_store:
        await callback_query.answer("❌ URL expired or not found. Please re-upload the file.", show_alert=True)
        return
    
    file_info = file_store[file_id]
    presigned_url = file_info['presigned_url']
    file_name = file_info['file_name']
    
    await callback_query.answer("URL copied to chat!", show_alert=False)
    
    # Send the URL as a separate message
    await callback_query.message.reply_text(
        f"**🔗 Direct URL for `{file_name}`:**\n\n"
        f"`{presigned_url}`\n\n"
        f"**Expires in:** 7 days\n"
        f"**Use this URL for:**\n"
        f"• Direct downloads\n"
        f"• Streaming (if supported by file type)\n"
        f"• Sharing with others"
    )

# Error handler
@app.on_message(filters.private)
async def invalid_handler(client, message: Message):
    """Handle invalid messages"""
    if message.from_user.id != ADMIN_ID:
        return
        
    if not (message.document or message.video or message.audio or message.photo):
        await message.reply_text(
            "❌ Please send a file (document, video, audio, or photo) to upload to Wasabi.\n\n"
            "Use /start to see bot instructions.\n"
            "Use /status to check bot connectivity."
        )

# Cleanup old file store entries periodically
async def cleanup_task():
    """Periodically clean up old file store entries"""
    while True:
        await asyncio.sleep(3600)  # Run every hour
        current_time = time.time()
        global file_store
        initial_count = len(file_store)
        file_store = {k: v for k, v in file_store.items() if current_time - v['timestamp'] < 7200}  # Keep for 2 hours
        if initial_count != len(file_store):
            print(f"🧹 Cleaned up {initial_count - len(file_store)} old file store entries")

@app.on_startup()
async def startup_handler(client):
    """Startup handler to initialize background tasks"""
    print("🚀 Bot started successfully!")
    # Start cleanup task only when the bot is running
    asyncio.create_task(cleanup_task())

@app.on_shutdown()
async def shutdown_handler(client):
    """Shutdown handler"""
    print("👋 Bot is shutting down...")

# --- Main Execution ---
if __name__ == "__main__":
    print("🤖 Bot is starting...")
    
    try:
        app.run()
    except KeyboardInterrupt:
        print("\n🛑 Bot stopped by user")
    except Exception as e:
        print(f"❌ Bot crashed with error: {e}")
    finally:
        print("👋 Bot has stopped.")
