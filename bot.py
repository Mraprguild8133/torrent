import os
import time
import asyncio
import hashlib
import boto3
import urllib.parse
from botocore.exceptions import NoCredentialsError, ClientError
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton

from config import (
    API_ID, API_HASH, BOT_TOKEN, ADMIN_ID,
    WASABI_ACCESS_KEY, WASABI_SECRET_KEY, 
    WASABI_BUCKET, WASABI_REGION, WASABI_ENDPOINT_URL,
    BASE_URL
)

# Store file information temporarily
file_store = {}

# Supported video formats for streaming
STREAMABLE_EXTENSIONS = {'.mp4', '.mkv', '.avi', '.mov', '.wmv', '.flv', '.webm', '.m4v', '.3gp'}

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

def cleanup_old_entries():
    """Clean up old file store entries"""
    current_time = time.time()
    global file_store
    initial_count = len(file_store)
    file_store = {k: v for k, v in file_store.items() if current_time - v['timestamp'] < 7200}
    if initial_count != len(file_store):
        print(f"🧹 Cleaned up {initial_count - len(file_store)} old file store entries")

def is_streamable(file_name):
    """Check if file is streamable"""
    if not file_name:
        return False
    return any(file_name.lower().endswith(ext) for ext in STREAMABLE_EXTENSIONS)

def validate_url(url):
    """Validate and ensure URL is properly formatted"""
    if not url:
        return None
    if url.startswith('intent:') or url.startswith('vlc://'):
        return url
    if not url.startswith(('http://', 'https://')):
        return f"https://{url}"
    return url

class ProgressTracker:
    """Track progress for individual uploads/downloads"""
    def __init__(self):
        self.last_update_time = 0
        self.start_time = 0
    
    async def progress_callback(self, current, total, message: Message, operation: str):
        """Progress callback to show real-time status"""
        current_time = time.time()
        
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
        
        filled_blocks = int(percentage / 5)
        empty_blocks = 20 - filled_blocks
        progress_bar = f"[{'█' * filled_blocks}{'░' * empty_blocks}]"
        
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
            pass

# Create progress tracker instance
progress_tracker = ProgressTracker()

# Initialize Boto3 S3 Client for Wasabi
try:
    s3_client = boto3.client(
        's3',
        endpoint_url=WASABI_ENDPOINT_URL,
        aws_access_key_id=WASABI_ACCESS_KEY,
        aws_secret_access_key=WASABI_SECRET_KEY,
        region_name=WASABI_REGION
    )
    s3_client.list_buckets()
    print("✅ Successfully connected to Wasabi")
except NoCredentialsError:
    print("❌ Wasabi credentials not found")
    exit(1)
except ClientError as e:
    print(f"❌ Failed to connect to Wasabi: {e}")
    exit(1)

# Initialize Pyrogram Client
app = Client(
    "wasabi_uploader_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN
)

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
        "3. 🔗 Provide you with direct, streamable links\n\n"
        "**Supported Players:**\n"
        "• 🌐 Online HTML5 Player\n"
        "• 📱 MX Player (Android)\n"
        "• ▶️ VLC Player (All platforms)\n\n"
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
    
    cleanup_old_entries()
    await message.reply_text(f"🧹 Cleanup completed. {len(file_store)} entries remain.")

@app.on_message(filters.command("stats") & filters.private)
async def stats_handler(client, message: Message):
    """Show bot statistics"""
    if message.from_user.id != ADMIN_ID:
        await message.reply_text("❌ Unauthorized")
        return
    
    cleanup_old_entries()
    streamable_count = len([v for v in file_store.values() if v.get('is_streamable', False)])
    
    stats_msg = (
        f"**📊 Bot Statistics**\n\n"
        f"**Stored Files:** {len(file_store)}\n"
        f"**Streamable Videos:** {streamable_count}\n"
        f"**Active Links:** {len([v for v in file_store.values() if time.time() - v['timestamp'] < 604800])}"
    )
    
    await message.reply_text(stats_msg)

@app.on_message((filters.document | filters.video | filters.audio | filters.photo) & filters.private)
async def file_handler(client, message: Message):
    """Main handler for processing incoming files."""
    if message.from_user.id != ADMIN_ID:
        await message.reply_text("❌ You are not authorized to send files.")
        return

    cleanup_old_entries()

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
    is_video_streamable = is_streamable(file_name)
    
    # Inform user that the process has started
    status_message = await message.reply_text(
        f"**📁 Processing File**\n"
        f"**Name:** `{file_name}`\n"
        f"**Size:** {humanbytes(file_size)}\n"
        f"**Streamable:** {'Yes' if is_video_streamable else 'No'}\n"
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
        
        await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: s3_client.upload_file(
                downloaded_file_path,
                WASABI_BUCKET,
                file_name
            )
        )
        
        await status_message.edit_text("✅ File uploaded successfully to Wasabi.\n**Status:** Generating shareable links...")
        
        # 3. Generate a pre-signed shareable link
        presigned_url = s3_client.generate_presigned_url(
            'get_object',
            Params={'Bucket': WASABI_BUCKET, 'Key': file_name},
            ExpiresIn=604800  # 7 days
        )
        
        # Validate the presigned URL
        presigned_url = validate_url(presigned_url)
        if not presigned_url:
            await status_message.edit_text("❌ Failed to generate valid download URL")
            return
        
        # 4. Generate a unique file ID
        file_id = generate_file_id(file_name)
        file_store[file_id] = {
            'file_name': file_name,
            'presigned_url': presigned_url,
            'is_streamable': is_video_streamable,
            'timestamp': time.time()
        }
        
        # 5. Create appropriate buttons based on file type
        # Use only callback data for all buttons to avoid URL issues
        if is_video_streamable:
            markup = InlineKeyboardMarkup([
                [InlineKeyboardButton("🎬 Online Player", callback_data=f"online_{file_id}")],
                [InlineKeyboardButton("📱 MX Player", callback_data=f"mx_{file_id}")],
                [InlineKeyboardButton("▶️ VLC Player", callback_data=f"vlc_{file_id}")],
                [
                    InlineKeyboardButton("🔗 Direct Download", callback_data=f"direct_{file_id}"),
                    InlineKeyboardButton("📋 Copy URL", callback_data=f"url_{file_id}")
                ]
            ])
        else:
            markup = InlineKeyboardMarkup([
                [InlineKeyboardButton("🔗 Direct Download", callback_data=f"direct_{file_id}")],
                [InlineKeyboardButton("📋 Copy URL", callback_data=f"url_{file_id}")]
            ])
        
        # 6. Send success message
        final_message = (
            f"✅ **File Uploaded Successfully!**\n\n"
            f"**📁 File:** `{file_name}`\n"
            f"**💾 Size:** {humanbytes(file_size)}\n"
            f"**🎥 Streamable:** {'Yes' if is_video_streamable else 'No'}\n"
            f"**⏰ Link Expires:** 7 days\n\n"
        )
        
        if is_video_streamable:
            final_message += "**Choose your preferred player:**"
        else:
            final_message += "**Use the buttons below to access your file:**"
        
        await message.reply_text(final_message, reply_markup=markup, quote=True)
        await status_message.delete()
        
    except Exception as e:
        error_msg = f"❌ Error processing file: {str(e)}"
        try:
            await status_message.edit_text(error_msg)
        except:
            await message.reply_text(error_msg)
        
    finally:
        if downloaded_file_path and os.path.exists(downloaded_file_path):
            try:
                os.remove(downloaded_file_path)
            except Exception as e:
                print(f"Warning: Could not delete temporary file: {e}")

# Callback query handlers
@app.on_callback_query(filters.regex("^url_"))
async def copy_url_callback(client, callback_query):
    """Handle copy URL callback"""
    file_id = callback_query.data.replace("url_", "")
    
    cleanup_old_entries()
    
    if file_id not in file_store:
        await callback_query.answer("❌ URL expired or not found. Please re-upload the file.", show_alert=True)
        return
    
    file_info = file_store[file_id]
    presigned_url = file_info['presigned_url']
    file_name = file_info['file_name']
    
    await callback_query.answer("URL sent to chat!", show_alert=False)
    
    await callback_query.message.reply_text(
        f"**🔗 Direct URL for `{file_name}`:**\n\n"
        f"`{presigned_url}`\n\n"
        f"**Expires in:** 7 days\n"
        f"**Use this URL for:**\n"
        f"• Direct downloads\n"
        f"• Streaming (if supported by file type)\n"
        f"• Sharing with others"
    )

@app.on_callback_query(filters.regex("^direct_"))
async def direct_download_callback(client, callback_query):
    """Handle direct download callback"""
    file_id = callback_query.data.replace("direct_", "")
    
    cleanup_old_entries()
    
    if file_id not in file_store:
        await callback_query.answer("❌ URL expired or not found. Please re-upload the file.", show_alert=True)
        return
    
    file_info = file_store[file_id]
    presigned_url = file_info['presigned_url']
    file_name = file_info['file_name']
    
    # Create a proper URL button for direct download
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔗 Download Now", url=presigned_url)],
        [InlineKeyboardButton("📋 Copy URL", callback_data=f"url_{file_id}")]
    ])
    
    await callback_query.message.reply_text(
        f"**🔗 Direct Download: `{file_name}`**\n\n"
        f"Click the button below to download the file directly:",
        reply_markup=markup
    )
    
    await callback_query.answer()

@app.on_callback_query(filters.regex("^online_"))
async def online_player_callback(client, callback_query):
    """Handle online player callback"""
    file_id = callback_query.data.replace("online_", "")
    
    cleanup_old_entries()
    
    if file_id not in file_store:
        await callback_query.answer("❌ URL expired or not found. Please re-upload the file.", show_alert=True)
        return
    
    file_info = file_store[file_id]
    
    if not file_info['is_streamable']:
        await callback_query.answer("❌ This file is not streamable.", show_alert=True)
        return
    
    presigned_url = file_info['presigned_url']
    file_name = file_info['file_name']
    
    # Create online player URL with parameters
    online_url = f"{BASE_URL}/player.html?url={urllib.parse.quote(presigned_url)}&title={urllib.parse.quote(file_name)}"
    
    # Send the online player link with a proper URL button
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("🎬 Open Online Player", url=online_url)],
        [InlineKeyboardButton("📋 Copy Player Link", callback_data=f"copy_online_{file_id}")]
    ])
    
    await callback_query.message.reply_text(
        f"**🎬 Online Player for `{file_name}`**\n\n"
        f"Click the button below to open the video in your browser:",
        reply_markup=markup
    )
    
    await callback_query.answer()

@app.on_callback_query(filters.regex("^mx_"))
async def mx_player_callback(client, callback_query):
    """Handle MX Player callback"""
    file_id = callback_query.data.replace("mx_", "")
    
    cleanup_old_entries()
    
    if file_id not in file_store:
        await callback_query.answer("❌ URL expired or not found. Please re-upload the file.", show_alert=True)
        return
    
    file_info = file_store[file_id]
    
    if not file_info['is_streamable']:
        await callback_query.answer("❌ This file is not streamable.", show_alert=True)
        return
    
    presigned_url = file_info['presigned_url']
    file_name = file_info['file_name']
    
    # Create MX Player intent URL
    mx_url = f"intent:{presigned_url}#Intent;package=com.mxtech.videoplayer.ad;scheme=http;end"
    
    # Send MX Player link with proper URL button
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("📱 Open in MX Player", url=mx_url)],
        [InlineKeyboardButton("📋 Copy MX Player URL", callback_data=f"copy_mx_{file_id}")]
    ])
    
    await callback_query.message.reply_text(
        f"**📱 MX Player for `{file_name}`**\n\n"
        f"Click the button below to open in MX Player:",
        reply_markup=markup
    )
    
    await callback_query.answer()

@app.on_callback_query(filters.regex("^vlc_"))
async def vlc_player_callback(client, callback_query):
    """Handle VLC Player callback"""
    file_id = callback_query.data.replace("vlc_", "")
    
    cleanup_old_entries()
    
    if file_id not in file_store:
        await callback_query.answer("❌ URL expired or not found. Please re-upload the file.", show_alert=True)
        return
    
    file_info = file_store[file_id]
    
    if not file_info['is_streamable']:
        await callback_query.answer("❌ This file is not streamable.", show_alert=True)
        return
    
    presigned_url = file_info['presigned_url']
    file_name = file_info['file_name']
    
    # Create VLC URL
    vlc_url = f"vlc://{presigned_url}"
    
    # Send VLC Player link with proper URL button
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("▶️ Open in VLC Player", url=vlc_url)],
        [InlineKeyboardButton("📋 Copy VLC URL", callback_data=f"copy_vlc_{file_id}")]
    ])
    
    await callback_query.message.reply_text(
        f"**▶️ VLC Player for `{file_name}`**\n\n"
        f"Click the button below to open in VLC Player:",
        reply_markup=markup
    )
    
    await callback_query.answer()

@app.on_callback_query(filters.regex("^copy_"))
async def copy_special_url_callback(client, callback_query):
    """Handle copy special URL callbacks"""
    data = callback_query.data.replace("copy_", "")
    
    if data.startswith("online_"):
        file_id = data.replace("online_", "")
        url_type = "Online Player"
    elif data.startswith("mx_"):
        file_id = data.replace("mx_", "")
        url_type = "MX Player"
    elif data.startswith("vlc_"):
        file_id = data.replace("vlc_", "")
        url_type = "VLC Player"
    else:
        await callback_query.answer("❌ Invalid copy request", show_alert=True)
        return
    
    cleanup_old_entries()
    
    if file_id not in file_store:
        await callback_query.answer("❌ URL expired or not found.", show_alert=True)
        return
    
    file_info = file_store[file_id]
    file_name = file_info['file_name']
    presigned_url = file_info['presigned_url']
    
    # Recreate the URLs
    if url_type == "Online Player":
        url_to_copy = f"{BASE_URL}/player.html?url={urllib.parse.quote(presigned_url)}&title={urllib.parse.quote(file_name)}"
    elif url_type == "MX Player":
        url_to_copy = f"intent:{presigned_url}#Intent;package=com.mxtech.videoplayer.ad;scheme=http;end"
    elif url_type == "VLC Player":
        url_to_copy = f"vlc://{presigned_url}"
    
    await callback_query.message.reply_text(
        f"**📋 {url_type} URL for `{file_name}`:**\n\n"
        f"`{url_to_copy}`"
    )
    
    await callback_query.answer(f"{url_type} URL copied to chat!")

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

if __name__ == "__main__":
    print("🤖 Bot is starting...")
    
    try:
        cleanup_old_entries()
        app.run()
    except KeyboardInterrupt:
        print("\n🛑 Bot stopped by user")
    except Exception as e:
        print(f"❌ Bot crashed with error: {e}")
    finally:
        print("👋 Bot has stopped.")
