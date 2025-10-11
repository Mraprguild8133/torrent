import os
import time
import asyncio
import sqlite3
import boto3
from contextlib import contextmanager
from typing import Dict, Any, Optional
from botocore.exceptions import NoCredentialsError, ClientError
from pyrogram import Client, filters
from pyrogram.errors import FloodWait, FileIdInvalid, FileReferenceExpired
from pyrogram.types import Message, InlineKeyboardButton, InlineKeyboardMarkup

# Import configuration
from config import config

# --- Custom Exceptions ---
class WasabiBotError(Exception):
    """Base exception for bot errors"""
    pass

class FileTooLargeError(WasabiBotError):
    pass

class InvalidFileError(WasabiBotError):
    pass

# --- Database for User Settings ---
class UserSettings:
    def __init__(self, db_path: str = "user_settings.db"):
        self.db_path = db_path
        self._init_db()
    
    def _init_db(self):
        with self._get_connection() as conn:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS user_settings (
                    user_id INTEGER PRIMARY KEY,
                    prefix TEXT,
                    thumbnail_path TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
    
    @contextmanager
    def _get_connection(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()
    
    def get_settings(self, user_id: int) -> Dict[str, Any]:
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT prefix, thumbnail_path FROM user_settings WHERE user_id = ?",
                (user_id,)
            ).fetchone()
            return dict(row) if row else {}
    
    def update_settings(self, user_id: int, **kwargs):
        with self._get_connection() as conn:
            existing = self.get_settings(user_id)
            if existing:
                conn.execute(
                    "UPDATE user_settings SET prefix = ?, thumbnail_path = ?, updated_at = CURRENT_TIMESTAMP WHERE user_id = ?",
                    (kwargs.get('prefix', existing.get('prefix')),
                     kwargs.get('thumbnail_path', existing.get('thumbnail_path')),
                     user_id)
                )
            else:
                conn.execute(
                    "INSERT INTO user_settings (user_id, prefix, thumbnail_path) VALUES (?, ?, ?)",
                    (user_id, kwargs.get('prefix'), kwargs.get('thumbnail_path'))
                )

# --- Error Handlers ---
async def handle_telegram_errors(func, *args, **kwargs):
    """Decorator to handle common Telegram errors"""
    try:
        return await func(*args, **kwargs)
    except FloodWait as e:
        await asyncio.sleep(e.value)
        return await func(*args, **kwargs)
    except (FileIdInvalid, FileReferenceExpired):
        raise InvalidFileError("File is no longer available")
    except Exception as e:
        raise WasabiBotError(f"Telegram error: {e}")

def handle_wasabi_sync(func, *args, **kwargs):
    """Sync wrapper for Wasabi operations"""
    try:
        return func(*args, **kwargs)
    except ClientError as e:
        error_code = e.response['Error']['Code']
        if error_code == 'NoSuchKey':
            raise WasabiBotError("File not found in Wasabi storage")
        elif error_code == 'AccessDenied':
            raise WasabiBotError("Access denied to Wasabi storage")
        elif error_code == 'NoSuchBucket':
            raise WasabiBotError("Bucket not found")
        else:
            raise WasabiBotError(f"Wasabi error: {error_code}")

# --- Globals & Bot Initialization ---
user_settings = UserSettings()

# Initialize Pyrogram Client
app = Client("wasabi_bot", api_id=config.API_ID, api_hash=config.API_HASH, bot_token=config.BOT_TOKEN)

# Initialize Boto3 S3 Client for Wasabi
try:
    s3_client = boto3.client(
        's3',
        endpoint_url=f'https://s3.{config.WASABI_REGION}.wasabisys.com',
        aws_access_key_id=config.WASABI_ACCESS_KEY,
        aws_secret_access_key=config.WASABI_SECRET_KEY,
        region_name=config.WASABI_REGION
    )
    print("Successfully connected to Wasabi.")
except NoCredentialsError:
    print("Credentials not available. Please check your environment variables.")
    s3_client = None
except Exception as e:
    print(f"An error occurred while connecting to Wasabi: {e}")
    s3_client = None

# --- Helper Functions ---
def humanbytes(size):
    """Converts bytes to a human-readable format."""
    if not size:
        return "0 B"
    power = 2**10
    n = 0
    power_labels = {0: 'B', 1: 'KB', 2: 'MB', 3: 'GB', 4: 'TB'}
    while size > power and n < len(power_labels) - 1:
        size /= power
        n += 1
    return f"{size:.2f} {power_labels[n]}"

def validate_file_size(file_size: int) -> bool:
    """Validate if file size is within limits"""
    return file_size <= config.MAX_FILE_SIZE

# --- Progress Callbacks ---
async def progress_telegram(current, total, message, start_time, action):
    """Updates the progress message for Telegram operations."""
    elapsed_time = time.time() - start_time
    if elapsed_time == 0:
        elapsed_time = 1
    
    speed = current / elapsed_time
    percentage = current * 100 / total
    progress_bar = "[{0}{1}]".format(
        '█' * int(percentage / 5),
        ' ' * (20 - int(percentage / 5))
    )
    
    time_remaining = "Calculating..."
    if speed > 0 and total > current:
        time_remaining_seconds = (total - current) / speed
        time_remaining = time.strftime('%H:%M:%S', time.gmtime(time_remaining_seconds))
    
    progress_str = (
        f"**{action}**\n"
        f"{progress_bar} {percentage:.1f}%\n"
        f"**Size:** {humanbytes(total)}\n"
        f"**Done:** {humanbytes(current)}\n"
        f"**Speed:** {humanbytes(speed)}/s\n"
        f"**Time Left:** {time_remaining}"
    )

    try:
        await message.edit_text(progress_str)
    except Exception:
        # Avoid crashing on message edit errors
        pass

class Boto3Progress:
    """Callback class to display Boto3 upload/download progress."""
    def __init__(self, message, file_size, start_time, loop, action="Processing"):
        self._message = message
        self._seen_so_far = 0
        self._file_size = file_size
        self._start_time = start_time
        self._loop = loop
        self._action = action
        self._last_update_time = 0

    def __call__(self, bytes_amount):
        current_time = time.time()
        # Update progress at most once per second to avoid flooding
        if current_time - self._last_update_time < 1:
            return
            
        self._seen_so_far += bytes_amount
        percentage = (self._seen_so_far / self._file_size) * 100 if self._file_size > 0 else 0
        
        elapsed_time = current_time - self._start_time
        if elapsed_time == 0:
            elapsed_time = 1
            
        speed = self._seen_so_far / elapsed_time
        progress_bar = "[{0}{1}]".format(
            '█' * int(percentage / 5),
            ' ' * (20 - int(percentage / 5))
        )
        
        progress_str = (
            f"**{self._action}**\n"
            f"{progress_bar} {percentage:.1f}%\n"
            f"**Size:** {humanbytes(self._file_size)}\n"
            f"**Done:** {humanbytes(self._seen_so_far)}\n"
            f"**Speed:** {humanbytes(speed)}/s"
        )
        
        asyncio.run_coroutine_threadsafe(self._message.edit_text(progress_str), self._loop)
        self._last_update_time = current_time

# --- Bot Command Handlers ---
@app.on_message(filters.command("start"))
async def start_command(client, message: Message):
    await message.reply_text(
        "**Welcome to the Wasabi Upload Bot!**\n\n"
        f"I can handle files up to {humanbytes(config.MAX_FILE_SIZE)}.\n\n"
        "**How to use:**\n"
        "1. **Send any file** to me.\n"
        "2. To use a **custom filename**, send the file with a caption.\n"
        "3. To set a **filename prefix**, use `/prefix your_prefix_` (e.g., `/prefix project_`). This prefix will be added to all subsequent uploads.\n"
        "4. To set a **custom thumbnail** for videos, reply to an image with `/setthumb`.\n"
        "5. To **download a file** from Wasabi, use `/download filename.ext`.\n"
        "6. To **list your files**, use `/list` or `/list prefix`."
    )

@app.on_message(filters.command("prefix"))
async def prefix_command(client, message: Message):
    user_id = message.from_user.id
    if len(message.command) > 1:
        prefix = message.command[1]
        user_settings.update_settings(user_id, prefix=prefix)
        await message.reply_text(f"Prefix set to: `{prefix}`")
    else:
        # Remove prefix if it exists
        current_settings = user_settings.get_settings(user_id)
        if current_settings.get('prefix'):
            user_settings.update_settings(user_id, prefix=None)
            await message.reply_text("Prefix removed.")
        else:
            await message.reply_text("Usage: `/prefix your_prefix_` to set, or `/prefix` to remove.")

@app.on_message(filters.command("setthumb") & filters.reply)
async def setthumb_command(client, message: Message):
    user_id = message.from_user.id
    if message.reply_to_message.photo:
        status_msg = await message.reply_text("Downloading thumbnail...", quote=True)
        try:
            thumb_path = await handle_telegram_errors(
                client.download_media, 
                message.reply_to_message.photo.file_id,
                file_name=f"thumb_{user_id}.jpg"
            )
            
            # Clean up old thumbnail if it exists
            current_settings = user_settings.get_settings(user_id)
            if current_settings.get('thumbnail_path') and os.path.exists(current_settings['thumbnail_path']):
                os.remove(current_settings['thumbnail_path'])
                
            user_settings.update_settings(user_id, thumbnail_path=thumb_path)
            await status_msg.edit_text("✅ Custom thumbnail saved!")
            
        except WasabiBotError as e:
            await status_msg.edit_text(f"❌ Error: {e}")
    else:
        await message.reply_text("Please reply to an image to set it as a thumbnail.")

@app.on_message(filters.command("list"))
async def list_files_command(client, message: Message):
    if not s3_client:
        await message.reply_text("Wasabi client is not initialized.")
        return
        
    prefix = " ".join(message.command[1:]) if len(message.command) > 1 else ""
    
    try:
        status_msg = await message.reply_text("Fetching files from Wasabi...")
        
        # Run sync Wasabi operation in thread
        response = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: handle_wasabi_sync(
                s3_client.list_objects_v2,
                Bucket=config.WASABI_BUCKET,
                Prefix=prefix,
                MaxKeys=50
            )
        )
        
        if 'Contents' not in response:
            await status_msg.edit_text("No files found." if not prefix else f"No files found with prefix '{prefix}'")
            return
            
        files = response['Contents']
        file_list = []
        for file in files[:20]:  # Show first 20 files
            size = humanbytes(file['Size'])
            file_list.append(f"• `{file['Key']}` ({size})")
        
        text = f"**Files in storage** ({len(files)} total):\n\n" + "\n".join(file_list)
        
        if len(files) > 20:
            text += f"\n\n... and {len(files) - 20} more files"
            
        await status_msg.edit_text(text)
        
    except WasabiBotError as e:
        await message.reply_text(f"❌ Error listing files: {e}")

@app.on_message(filters.document | filters.video | filters.audio)
async def handle_file(client, message: Message):
    if not s3_client:
        await message.reply_text("Wasabi client is not initialized.")
        return

    user_id = message.from_user.id
    file_media = message.document or message.video or message.audio
    original_filename = getattr(file_media, 'file_name', 'unknown_file')
    file_size = file_media.file_size

    # Validate file size
    if not validate_file_size(file_size):
        await message.reply_text(
            f"❌ File too large. Maximum size is {humanbytes(config.MAX_FILE_SIZE)}"
        )
        return

    # Determine filename: caption > original filename
    filename = message.caption if message.caption else original_filename
    
    # Apply prefix if set
    user_config = user_settings.get_settings(user_id)
    prefix = user_config.get('prefix', '')
    final_filename = f"{prefix}{filename}"

    # Create downloads directory
    os.makedirs(config.DOWNLOAD_PATH, exist_ok=True)

    # --- 1. Download from Telegram ---
    status_msg = await message.reply_text(f"Starting download of `{final_filename}`...", quote=True)
    start_time = time.time()
    
    try:
        download_path = await handle_telegram_errors(
            client.download_media,
            message,
            file_name=os.path.join(config.DOWNLOAD_PATH, final_filename),
            progress=progress_telegram,
            progress_args=(status_msg, start_time, "Downloading from Telegram...")
        )
    except WasabiBotError as e:
        await status_msg.edit_text(f"❌ Error downloading from Telegram: {e}")
        return

    # --- 2. Upload to Wasabi ---
    await status_msg.edit_text("Preparing to upload to Wasabi...")
    
    try:
        loop = asyncio.get_event_loop()
        boto_progress = Boto3Progress(status_msg, file_size, time.time(), loop, "Uploading to Wasabi")
        
        # Run sync upload in thread
        await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: handle_wasabi_sync(
                s3_client.upload_file,
                download_path,
                config.WASABI_BUCKET,
                final_filename,
                Callback=boto_progress
            )
        )
        
        upload_url = f"https://{config.WASABI_BUCKET}.s3.{config.WASABI_REGION}.wasabisys.com/{final_filename}"
        
        # Create inline keyboard with download button
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("📥 Download", callback_data=f"download_{final_filename}")
        ]])
        
        await status_msg.edit_text(
            f"✅ **Upload Successful!**\n\n"
            f"**File:** `{final_filename}`\n"
            f"**Size:** {humanbytes(file_size)}\n"
            f"**URL:** {upload_url}",
            reply_markup=keyboard
        )
        
    except WasabiBotError as e:
        await status_msg.edit_text(f"❌ Error uploading to Wasabi: {e}")
    finally:
        # Cleanup local file
        if download_path and os.path.exists(download_path):
            os.remove(download_path)

@app.on_message(filters.command("download"))
async def download_from_wasabi(client, message: Message):
    if not s3_client:
        await message.reply_text("Wasabi client is not initialized.")
        return
        
    if len(message.command) < 2:
        await message.reply_text("Usage: `/download <filename_from_wasabi>`")
        return
        
    file_key = " ".join(message.command[1:])
    local_path = os.path.join(config.DOWNLOAD_PATH, file_key)
    os.makedirs(config.DOWNLOAD_PATH, exist_ok=True)
    
    status_msg = await message.reply_text(f"Downloading `{file_key}` from Wasabi...", quote=True)
    
    try:
        # Get file metadata
        meta = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: handle_wasabi_sync(
                s3_client.head_object,
                Bucket=config.WASABI_BUCKET,
                Key=file_key
            )
        )
        file_size = meta.get('ContentLength', 0)
        
        # Download from Wasabi
        loop = asyncio.get_event_loop()
        boto_progress = Boto3Progress(status_msg, file_size, time.time(), loop, "Downloading from Wasabi")
        
        await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: handle_wasabi_sync(
                s3_client.download_file,
                config.WASABI_BUCKET,
                file_key,
                local_path,
                Callback=boto_progress
            )
        )
        
        await status_msg.edit_text("Download complete. Uploading to Telegram...")
        
        # Get user thumbnail
        user_id = message.from_user.id
        user_config = user_settings.get_settings(user_id)
        thumb_path = user_config.get('thumbnail_path')
        if thumb_path and not os.path.exists(thumb_path):
            thumb_path = None

        # Upload to Telegram
        await handle_telegram_errors(
            client.send_document,
            chat_id=message.chat.id,
            document=local_path,
            thumb=thumb_path,
            caption=f"`{file_key}`",
            progress=progress_telegram,
            progress_args=(status_msg, time.time(), "Uploading to Telegram...")
        )
        await status_msg.delete()
        
    except WasabiBotError as e:
        await status_msg.edit_text(f"❌ Error: {e}")
    finally:
        if os.path.exists(local_path):
            os.remove(local_path)

@app.on_callback_query(filters.regex(r"^download_"))
async def handle_download_callback(client, callback_query):
    file_key = callback_query.data.replace("download_", "")
    
    # Create a mock message object for the download function
    class MockMessage:
        def __init__(self, chat_id, message_id, from_user, command):
            self.chat = type('Chat', (), {'id': chat_id})()
            self.message_id = message_id
            self.from_user = from_user
            self.command = command
    
    mock_message = MockMessage(
        chat_id=callback_query.message.chat.id,
        message_id=callback_query.message.id,
        from_user=callback_query.from_user,
        command=["download", file_key]
    )
    
    await download_from_wasabi(client, mock_message)
    await callback_query.answer()

# --- Main Execution ---
async def main():
    print("Bot is starting...")
    await app.start()
    print("Bot has started successfully.")
    
    # Test Wasabi connection
    if s3_client:
        try:
            await asyncio.get_event_loop().run_in_executor(
                None, 
                lambda: handle_wasabi_sync(s3_client.head_bucket, Bucket=config.WASABI_BUCKET)
            )
            print("Wasabi bucket is accessible.")
        except WasabiBotError as e:
            print(f"Warning: Wasabi bucket access issue: {e}")
    
    print("Bot is now running. Press Ctrl+C to stop.")
    await asyncio.Event().wait()

if __name__ == "__main__":
    # Create necessary directories
    os.makedirs(config.DOWNLOAD_PATH, exist_ok=True)
    
    try:
        # Run the bot
        app.run(main())
    except KeyboardInterrupt:
        print("Bot stopped by user")
    except Exception as e:
        print(f"Bot crashed with error: {e}")
