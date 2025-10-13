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

def cleanup_old_entries():
    """Clean up old file store entries"""
    current_time = time.time()
    global file_store
    initial_count = len(file_store)
    file_store = {k: v for k, v in file_store.items() if current_time - v['timestamp'] < 7200}  # Keep for 2 hours
    if initial_count != len(file_store):
        print(f"🧹 Cleaned up {initial_count - len(file_store)} old file store entries")

def get_file_extension(file_name):
    """Get file extension from filename"""
    return os.path.splitext(file_name)[1].lower() if '.' in file_name else ''

def is_video_file(file_name):
    """Check if file is a video"""
    video_extensions = {'.mp4', '.mkv', '.avi', '.mov', '.wmv', '.flv', '.webm', '.m4v', '.3gp'}
    return get_file_extension(file_name) in video_extensions

def is_audio_file(file_name):
    """Check if file is an audio file"""
    audio_extensions = {'.mp3', '.wav', '.ogg', '.flac', '.m4a', '.aac', '.wma'}
    return get_file_extension(file_name) in audio_extensions

def is_image_file(file_name):
    """Check if file is an image"""
    image_extensions = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', '.tiff'}
    return get_file_extension(file_name) in image_extensions

def generate_player_links(presigned_url, file_name, file_type):
    """Generate online player links based on file type"""
    players = []
    
    if is_video_file(file_name):
        players.extend([
            ["🎬 Streamable Player", f"https://streamable.com/upload?url={presigned_url}"],
            ["📹 CloudStream Player", f"https://cloudstream.com/player?url={presigned_url}"],
            ["🎥 GDrive Player", f"https://gdplayer.net/player?url={presigned_url}"],
            ["🔴 VLC Player", f"vlc://{presigned_url}"]
        ])
    
    elif is_audio_file(file_name):
        players.extend([
            ["🎵 Web Audio Player", f"https://webaudio-player.com/?url={presigned_url}"],
            ["🔊 SoundCloud Style", f"https://audiomack.com/embed?url={presigned_url}"],
            ["🎶 Music Player", f"https://musicplayer.com/play?url={presigned_url}"]
        ])
    
    elif is_image_file(file_name):
        players.extend([
            ["🖼️ Image Viewer", presigned_url],
            ["🎨 Photo Editor", f"https://pixlr.com/e/?image={presigned_url}"]
        ])
    
    # Universal players that work with many file types
    universal_players = [
        ["🌐 Universal Player", f"https://player.url2img.com/?url={presigned_url}"],
        ["📱 Mobile Player", f"https://mxplayer.com/play?url={presigned_url}"],
        ["💻 HTML5 Player", f"https://html5player.com/?url={presigned_url}"]
    ]
    
    # Add universal players for all file types
    players.extend(universal_players)
    
    return players

def create_player_markup(presigned_url, file_name, file_id):
    """Create inline keyboard markup with player options"""
    buttons = []
    
    # Direct download button
    buttons.append([InlineKeyboardButton("📥 Direct Download", url=presigned_url)])
    
    # Player buttons based on file type
    player_links = generate_player_links(presigned_url, file_name, "auto")
    
    # Add up to 3 player buttons
    for player in player_links[:3]:
        buttons.append([InlineKeyboardButton(player[0], url=player[1])])
    
    # Additional action buttons
    buttons.extend([
        [InlineKeyboardButton("📋 Copy URL", callback_data=f"url_{file_id}"),
         InlineKeyboardButton("🎮 More Players", callback_data=f"players_{file_id}")],
        [InlineKeyboardButton("🔄 Refresh Link", callback_data=f"refresh_{file_id}")]
    ])
    
    return InlineKeyboardMarkup(buttons)

def create_extended_players_markup(presigned_url, file_name, file_id):
    """Create extended player options markup"""
    buttons = []
    player_links = generate_player_links(presigned_url, file_name, "auto")
    
    # Add all available players
    for i in range(0, len(player_links), 2):
        row = []
        if i < len(player_links):
            row.append(InlineKeyboardButton(player_links[i][0], url=player_links[i][1]))
        if i + 1 < len(player_links):
            row.append(InlineKeyboardButton(player_links[i+1][0], url=player_links[i+1][1]))
        if row:
            buttons.append(row)
    
    # Navigation buttons
    buttons.extend([
        [InlineKeyboardButton("⬅️ Back to Main", callback_data=f"main_{file_id}")],
        [InlineKeyboardButton("📥 Direct Download", url=presigned_url)]
    ])
    
    return InlineKeyboardMarkup(buttons)

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
        "3. 🔗 Provide you with direct download and online player links\n\n"
        "**Supported Players:**\n"
        "• 🎬 Video players (MP4, MKV, AVI, etc.)\n"
        "• 🎵 Audio players (MP3, WAV, etc.)\n"
        "• 🖼️ Image viewers\n"
        "• 🌐 Universal players\n\n"
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
    
    cleanup_old_entries()
    await message.reply_text(f"🧹 Cleanup completed. {len(file_store)} entries remain.")

@app.on_message(filters.command("players") & filters.private)
async def players_info_handler(client, message: Message):
    """Show information about available players"""
    if message.from_user.id != ADMIN_ID:
        await message.reply_text("❌ Unauthorized")
        return
    
    info_text = (
        "**🎮 Available Online Players**\n\n"
        "**For Videos:**\n"
        "• Streamable Player - Web-based video player\n"
        "• CloudStream Player - Universal streaming\n"
        "• GDrive Player - Google Drive style player\n"
        "• VLC Player - Direct VLC protocol (requires VLC)\n\n"
        "**For Audio:**\n"
        "• Web Audio Player - HTML5 audio player\n"
        "• SoundCloud Style - Music streaming interface\n\n"
        "**For Images:**\n"
        "• Direct Image View - Browser image viewer\n"
        "• Photo Editor - Online editing tools\n\n"
        "**Universal Players:**\n"
        "• Works with most file types\n"
        "• Mobile-friendly options\n"
        "• HTML5 compatible players\n\n"
        "Just upload any file and choose your preferred player!"
    )
    
    await message.reply_text(info_text)

@app.on_message((filters.document | filters.video | filters.audio | filters.photo) & filters.private)
async def file_handler(client, message: Message):
    """Main handler for processing incoming files."""
    if message.from_user.id != ADMIN_ID:
        await message.reply_text("❌ You are not authorized to send files.")
        return

    # Clean up old entries before processing new file
    cleanup_old_entries()

    # Get file information
    if message.document:
        media = message.document
        file_name = media.file_name
        file_type = "document"
    elif message.video:
        media = message.video
        file_name = media.file_name or f"video_{message.id}.mp4"
        file_type = "video"
    elif message.audio:
        media = message.audio
        file_name = media.file_name or f"audio_{message.id}.mp3"
        file_type = "audio"
    elif message.photo:
        media = message.photo
        file_name = f"photo_{message.id}.jpg"
        file_type = "photo"
    else:
        await message.reply_text("❌ Unsupported file type.")
        return

    file_size = media.file_size
    
    # Inform user that the process has started
    status_message = await message.reply_text(
        f"**📁 Processing File**\n"
        f"**Name:** `{file_name}`\n"
        f"**Size:** {humanbytes(file_size)}\n"
        f"**Type:** {file_type.title()}\n"
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
        
        await status_message.edit_text("✅ File uploaded successfully to Wasabi.\n**Status:** Generating shareable links...")
        
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
            'timestamp': time.time(),
            'file_type': file_type
        }
        
        # 5. Determine file category for message
        if is_video_file(file_name):
            file_category = "🎬 Video"
            player_note = "Choose a video player below to stream online!"
        elif is_audio_file(file_name):
            file_category = "🎵 Audio"
            player_note = "Choose an audio player below to listen online!"
        elif is_image_file(file_name):
            file_category = "🖼️ Image"
            player_note = "Choose a viewer below to see your image!"
        else:
            file_category = "📄 File"
            player_note = "Use the universal players below to view online!"
        
        # 6. Create player markup
        markup = create_player_markup(presigned_url, file_name, file_id)
        
        # 7. Send success message with player options
        final_message = (
            f"✅ **File Uploaded Successfully!**\n\n"
            f"**📁 File:** `{file_name}`\n"
            f"**💾 Size:** {humanbytes(file_size)}\n"
            f"**📦 Type:** {file_category}\n"
            f"**⏰ Link Expires:** 7 days\n\n"
            f"{player_note}"
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
        # 8. Clean up the downloaded file
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
    
    # Clean up old entries first
    cleanup_old_entries()
    
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
        f"• Streaming in supported apps\n"
        f"• Sharing with others"
    )

@app.on_callback_query(filters.regex("^players_"))
async def show_players_callback(client, callback_query):
    """Show extended player options"""
    file_id = callback_query.data.replace("players_", "")
    
    if file_id not in file_store:
        await callback_query.answer("❌ File not found", show_alert=True)
        return
    
    file_info = file_store[file_id]
    presigned_url = file_info['presigned_url']
    file_name = file_info['file_name']
    
    # Create extended players markup
    markup = create_extended_players_markup(presigned_url, file_name, file_id)
    
    # Update message with more player options
    await callback_query.message.edit_reply_markup(markup)
    await callback_query.answer("🎮 More players loaded!")

@app.on_callback_query(filters.regex("^main_"))
async def back_to_main_callback(client, callback_query):
    """Return to main menu"""
    file_id = callback_query.data.replace("main_", "")
    
    if file_id not in file_store:
        await callback_query.answer("❌ File not found", show_alert=True)
        return
    
    file_info = file_store[file_id]
    presigned_url = file_info['presigned_url']
    file_name = file_info['file_name']
    
    # Create main player markup
    markup = create_player_markup(presigned_url, file_name, file_id)
    
    # Update message back to main menu
    await callback_query.message.edit_reply_markup(markup)
    await callback_query.answer("⬅️ Back to main menu")

@app.on_callback_query(filters.regex("^refresh_"))
async def refresh_link_callback(client, callback_query):
    """Refresh the presigned URL"""
    file_id = callback_query.data.replace("refresh_", "")
    
    if file_id not in file_store:
        await callback_query.answer("❌ File not found", show_alert=True)
        return
    
    file_info = file_store[file_id]
    file_name = file_info['file_name']
    
    try:
        # Generate new presigned URL
        new_presigned_url = s3_client.generate_presigned_url(
            'get_object',
            Params={'Bucket': WASABI_BUCKET, 'Key': file_name},
            ExpiresIn=604800
        )
        
        # Update file store
        file_store[file_id]['presigned_url'] = new_presigned_url
        file_store[file_id]['timestamp'] = time.time()
        
        # Create updated markup
        markup = create_player_markup(new_presigned_url, file_name, file_id)
        
        # Update message
        await callback_query.message.edit_reply_markup(markup)
        await callback_query.answer("🔄 Link refreshed for another 7 days!")
        
    except Exception as e:
        await callback_query.answer(f"❌ Failed to refresh link: {e}", show_alert=True)

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
            "Use /players to see available online players.\n"
            "Use /status to check bot connectivity."
        )

# --- Main Execution ---
if __name__ == "__main__":
    print("🤖 Bot is starting...")
    
    try:
        # Clean up any old entries on startup
        cleanup_old_entries()
        
        # Start the bot
        app.run()
    except KeyboardInterrupt:
        print("\n🛑 Bot stopped by user")
    except Exception as e:
        print(f"❌ Bot crashed with error: {e}")
    finally:
        print("👋 Bot has stopped.")