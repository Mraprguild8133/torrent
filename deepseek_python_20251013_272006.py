import os
import time
import asyncio
import hashlib
import boto3
from botocore.exceptions import NoCredentialsError, ClientError
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from flask import Flask, render_template, request, redirect, url_for
import threading
from urllib.parse import quote

from config import (
    API_ID, API_HASH, BOT_TOKEN, ADMIN_ID,
    WASABI_ACCESS_KEY, WASABI_SECRET_KEY, 
    WASABI_BUCKET, WASABI_REGION, WASABI_ENDPOINT_URL,
    RENDER_EXTERNAL_URL  # Add this to your config
)

# Store file information temporarily (in production, use a database)
file_store = {}

# Flask app for online player
app_flask = Flask(__name__)
app_flask.config['SECRET_KEY'] = os.urandom(24)

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

def get_stream_url(file_id):
    """Generate stream URL for online player"""
    if file_id in file_store:
        file_info = file_store[file_id]
        return f"{RENDER_EXTERNAL_URL}/stream/{file_id}"
    return None

def is_streamable_file(filename):
    """Check if file type is streamable"""
    streamable_extensions = {
        '.mp4', '.mkv', '.avi', '.mov', '.wmv', '.flv', '.webm',
        '.mp3', '.m4a', '.wav', '.flac', '.ogg', '.aac',
        '.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp'
    }
    ext = os.path.splitext(filename.lower())[1]
    return ext in streamable_extensions

def get_file_type(filename):
    """Determine file type for proper player rendering"""
    ext = os.path.splitext(filename.lower())[1]
    video_extensions = {'.mp4', '.mkv', '.avi', '.mov', '.wmv', '.flv', '.webm'}
    audio_extensions = {'.mp3', '.m4a', '.wav', '.flac', '.ogg', '.aac'}
    image_extensions = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp'}
    
    if ext in video_extensions:
        return 'video'
    elif ext in audio_extensions:
        return 'audio'
    elif ext in image_extensions:
        return 'image'
    else:
        return 'download'

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

# --- Flask Routes for Online Player ---
@app_flask.route('/')
def index():
    """Home page showing uploaded files"""
    cleanup_old_entries()
    files = []
    for file_id, file_info in file_store.items():
        files.append({
            'id': file_id,
            'name': file_info['file_name'],
            'size': file_info.get('size', 0),
            'type': get_file_type(file_info['file_name']),
            'upload_time': time.strftime('%Y-%m-%d %H:%M:%S', 
                                       time.localtime(file_info['timestamp']))
        })
    
    return render_template('index.html', files=files)

@app_flask.route('/stream/<file_id>')
def stream_file(file_id):
    """Stream file from Wasabi"""
    if file_id not in file_store:
        return "File not found or expired", 404
    
    file_info = file_store[file_id]
    file_name = file_info['file_name']
    file_type = get_file_type(file_name)
    
    # Generate presigned URL for streaming
    try:
        presigned_url = s3_client.generate_presigned_url(
            'get_object',
            Params={'Bucket': WASABI_BUCKET, 'Key': file_name},
            ExpiresIn=3600  # 1 hour for streaming
        )
        
        if file_type == 'video':
            return render_template('video_player.html', 
                                video_url=presigned_url, 
                                file_name=file_name)
        elif file_type == 'audio':
            return render_template('audio_player.html', 
                                audio_url=presigned_url, 
                                file_name=file_name)
        elif file_type == 'image':
            return render_template('image_viewer.html', 
                                image_url=presigned_url, 
                                file_name=file_name)
        else:
            # For non-streamable files, redirect to download
            return redirect(presigned_url)
            
    except Exception as e:
        return f"Error generating stream URL: {str(e)}", 500

@app_flask.route('/download/<file_id>')
def download_file(file_id):
    """Direct download endpoint"""
    if file_id not in file_store:
        return "File not found or expired", 404
    
    file_info = file_store[file_id]
    
    try:
        presigned_url = s3_client.generate_presigned_url(
            'get_object',
            Params={'Bucket': WASABI_BUCKET, 'Key': file_info['file_name']},
            ExpiresIn=3600
        )
        return redirect(presigned_url)
    except Exception as e:
        return f"Error generating download URL: {str(e)}", 500

@app_flask.route('/api/files')
def api_files():
    """API endpoint to get file list"""
    cleanup_old_entries()
    files = []
    for file_id, file_info in file_store.items():
        files.append({
            'id': file_id,
            'name': file_info['file_name'],
            'size': file_info.get('size', 0),
            'type': get_file_type(file_info['file_name']),
            'stream_url': f"/stream/{file_id}",
            'download_url': f"/download/{file_id}",
            'upload_time': file_info['timestamp']
        })
    
    return {'files': files}

# Flask Templates (inline for simplicity)
@app_flask.route('/templates/<template_name>')
def serve_template(template_name):
    """Serve HTML templates"""
    templates = {
        'index.html': '''
<!DOCTYPE html>
<html>
<head>
    <title>Wasabi File Player</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        body { font-family: Arial, sans-serif; margin: 20px; background: #f5f5f5; }
        .container { max-width: 1200px; margin: 0 auto; }
        .header { background: white; padding: 20px; border-radius: 10px; margin-bottom: 20px; }
        .file-list { display: grid; gap: 15px; }
        .file-card { 
            background: white; 
            padding: 15px; 
            border-radius: 8px; 
            display: flex; 
            justify-content: space-between;
            align-items: center;
            box-shadow: 0 2px 5px rgba(0,0,0,0.1);
        }
        .file-info { flex-grow: 1; }
        .file-actions { display: flex; gap: 10px; }
        .btn { 
            padding: 8px 15px; 
            border: none; 
            border-radius: 5px; 
            cursor: pointer; 
            text-decoration: none;
            color: white;
            font-size: 14px;
        }
        .btn-stream { background: #007bff; }
        .btn-download { background: #28a745; }
        .file-type { 
            padding: 2px 8px; 
            border-radius: 12px; 
            font-size: 12px; 
            color: white;
            margin-left: 10px;
        }
        .video { background: #dc3545; }
        .audio { background: #ffc107; color: black; }
        .image { background: #17a2b8; }
        .download { background: #6c757d; }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🎬 Wasabi File Player</h1>
            <p>Stream and download your uploaded files</p>
        </div>
        
        <div class="file-list">
            {% for file in files %}
            <div class="file-card">
                <div class="file-info">
                    <strong>{{ file.name }}</strong>
                    <span class="file-type {{ file.type }}">{{ file.type.upper() }}</span>
                    <br>
                    <small>Size: {{ file.size }} | Uploaded: {{ file.upload_time }}</small>
                </div>
                <div class="file-actions">
                    <a href="/stream/{{ file.id }}" class="btn btn-stream">🎬 Play</a>
                    <a href="/download/{{ file.id }}" class="btn btn-download">📥 Download</a>
                </div>
            </div>
            {% else %}
            <div class="file-card">
                <p>No files available. Upload files through the Telegram bot.</p>
            </div>
            {% endfor %}
        </div>
    </div>
</body>
</html>
        ''',
        
        'video_player.html': '''
<!DOCTYPE html>
<html>
<head>
    <title>Video Player - {{ file_name }}</title>
    <style>
        body { 
            margin: 0; 
            padding: 20px; 
            background: #000; 
            display: flex; 
            justify-content: center; 
            align-items: center;
            min-height: 100vh;
        }
        .player-container {
            max-width: 1000px;
            width: 100%;
            background: #111;
            border-radius: 10px;
            padding: 20px;
        }
        video {
            width: 100%;
            border-radius: 8px;
            outline: none;
        }
        .back-btn {
            color: white;
            text-decoration: none;
            padding: 10px 20px;
            background: #333;
            border-radius: 5px;
            margin-bottom: 15px;
            display: inline-block;
        }
    </style>
</head>
<body>
    <div class="player-container">
        <a href="/" class="back-btn">← Back to Files</a>
        <video controls autoplay>
            <source src="{{ video_url }}" type="video/mp4">
            Your browser does not support the video tag.
        </video>
        <p style="color: white; text-align: center; margin-top: 10px;">{{ file_name }}</p>
    </div>
</body>
</html>
        ''',
        
        'audio_player.html': '''
<!DOCTYPE html>
<html>
<head>
    <title>Audio Player - {{ file_name }}</title>
    <style>
        body { 
            margin: 0; 
            padding: 20px; 
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            display: flex; 
            justify-content: center; 
            align-items: center;
            min-height: 100vh;
            font-family: Arial, sans-serif;
        }
        .player-container {
            background: white;
            padding: 30px;
            border-radius: 15px;
            box-shadow: 0 10px 30px rgba(0,0,0,0.3);
            text-align: center;
            max-width: 500px;
            width: 100%;
        }
        audio {
            width: 100%;
            margin: 20px 0;
        }
        .back-btn {
            color: #333;
            text-decoration: none;
            padding: 10px 20px;
            background: #f8f9fa;
            border-radius: 5px;
            border: 1px solid #ddd;
        }
    </style>
</head>
<body>
    <div class="player-container">
        <h2>🎵 Audio Player</h2>
        <p><strong>{{ file_name }}</strong></p>
        <audio controls autoplay>
            <source src="{{ audio_url }}" type="audio/mpeg">
            Your browser does not support the audio element.
        </audio>
        <br>
        <a href="/" class="back-btn">← Back to Files</a>
    </div>
</body>
</html>
        ''',
        
        'image_viewer.html': '''
<!DOCTYPE html>
<html>
<head>
    <title>Image Viewer - {{ file_name }}</title>
    <style>
        body { 
            margin: 0; 
            padding: 20px; 
            background: #333; 
            display: flex; 
            justify-content: center; 
            align-items: center;
            min-height: 100vh;
        }
        .image-container {
            max-width: 90%;
            max-height: 90vh;
            text-align: center;
        }
        img {
            max-width: 100%;
            max-height: 80vh;
            border-radius: 10px;
            box-shadow: 0 5px 15px rgba(0,0,0,0.3);
        }
        .back-btn {
            color: white;
            text-decoration: none;
            padding: 10px 20px;
            background: #555;
            border-radius: 5px;
            margin-top: 15px;
            display: inline-block;
        }
    </style>
</head>
<body>
    <div class="image-container">
        <img src="{{ image_url }}" alt="{{ file_name }}">
        <br>
        <a href="/" class="back-btn">← Back to Files</a>
    </div>
</body>
</html>
        '''
    }
    
    return templates.get(template_name, 'Template not found'), 200, {'Content-Type': 'text/html'}

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
        "3. 🔗 Provide you with direct, streamable links\n"
        "4. 🎬 Online player available at: {RENDER_EXTERNAL_URL}\n\n"
        "**Note:** This bot is for authorized users only.\n"
        "Use /status to check bot connectivity."
    )

@app.on_message(filters.command("web") & filters.private)
async def web_handler(client, message: Message):
    """Send web player link"""
    if message.from_user.id != ADMIN_ID:
        await message.reply_text("❌ Unauthorized")
        return
    
    await message.reply_text(
        f"**🌐 Online File Player**\n\n"
        f"Access your uploaded files through the web interface:\n"
        f"{RENDER_EXTERNAL_URL}\n\n"
        f"Features:\n"
        f"• 🎬 Video streaming\n"
        f"• 🎵 Audio playback\n"
        f"• 🖼️ Image viewing\n"
        f"• 📥 Direct downloads"
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
        # Test Flask server
        import requests
        response = requests.get(f"{RENDER_EXTERNAL_URL}/", timeout=5)
        web_status = "Working" if response.status_code == 200 else "Not responding"
        
        status_msg = (
            "✅ **Bot Status:** Online\n"
            "✅ **Wasabi Connection:** Working\n"
            f"✅ **Web Player:** {web_status}\n"
            f"🌐 **Player URL:** {RENDER_EXTERNAL_URL}"
        )
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
    streamable_files = len([f for f in file_store.values() if is_streamable_file(f['file_name'])])
    
    stats_msg = (
        f"**📊 Bot Statistics**\n\n"
        f"**Stored Files:** {len(file_store)}\n"
        f"**Streamable Files:** {streamable_files}\n"
        f"**Web Player:** {RENDER_EXTERNAL_URL}\n"
        f"**Active Links:** {len([v for v in file_store.values() if time.time() - v['timestamp'] < 604800])}"
    )
    
    await message.reply_text(stats_msg)

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
            'size': humanbytes(file_size),
            'timestamp': time.time()
        }
        
        # 5. Create buttons based on file type
        file_type = get_file_type(file_name)
        stream_url = get_stream_url(file_id)
        web_url = f"{RENDER_EXTERNAL_URL}/stream/{file_id}"
        
        buttons = []
        if file_type in ['video', 'audio', 'image']:
            buttons.append([InlineKeyboardButton("🎬 Online Player", url=web_url)])
        buttons.append([InlineKeyboardButton("🔗 Direct Download", url=presigned_url)])
        buttons.append([InlineKeyboardButton("📋 Copy URL", callback_data=f"url_{file_id}")])
        
        markup = InlineKeyboardMarkup(buttons)
        
        # 6. Send success message with links
        final_message = (
            f"✅ **File Uploaded Successfully!**\n\n"
            f"**📁 File:** `{file_name}`\n"
            f"**💾 Size:** {humanbytes(file_size)}\n"
            f"**📊 Type:** {file_type.upper()}\n"
            f"**⏰ Link Expires:** 7 days\n\n"
        )
        
        if file_type in ['video', 'audio', 'image']:
            final_message += f"**🌐 Online Player:** {web_url}\n\n"
        
        final_message += "Use the buttons below to access your file:"
        
        await message.reply_text(final_message, reply_markup=markup, quote=True)
        await status_message.delete()
        
    except Exception as e:
        error_msg = f"❌ Error processing file: {str(e)}"
        try:
            await status_message.edit_text(error_msg)
        except:
            await message.reply_text(error_msg)
        
    finally:
        # 7. Clean up the downloaded file
        if downloaded_file_path and os.path.exists(downloaded_file_path):
            try:
                os.remove(downloaded_file_path)
            except Exception as e:
                print(f"Warning: Could not delete temporary file: {e}")

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
    file_type = get_file_type(file_name)
    web_url = f"{RENDER_EXTERNAL_URL}/stream/{file_id}"
    
    await callback_query.answer("URLs copied to chat!", show_alert=False)
    
    # Send the URLs as a separate message
    url_message = (
        f"**🔗 URLs for `{file_name}`:**\n\n"
        f"**Direct Download:**\n"
        f"`{presigned_url}`\n\n"
    )
    
    if file_type in ['video', 'audio', 'image']:
        url_message += f"**🎬 Online Player:**\n`{web_url}`\n\n"
    
    url_message += (
        f"**Expires in:** 7 days\n"
        f"**Use these URLs for:**\n"
        f"• Direct downloads\n"
        f"• Streaming media\n"
        f"• Sharing with others"
    )
    
    await callback_query.message.reply_text(url_message)

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
            "Use /status to check bot connectivity.\n"
            "Use /web to get online player link."
        )

def run_flask():
    """Run Flask app on port 5000"""
    app_flask.run(host='0.0.0.0', port=5000, debug=False)

# --- Main Execution ---
if __name__ == "__main__":
    print("🤖 Bot is starting...")
    
    # Validate RENDER_EXTERNAL_URL
    if not RENDER_EXTERNAL_URL:
        print("❌ RENDER_EXTERNAL_URL not configured")
        exit(1)
    
    try:
        # Clean up any old entries on startup
        cleanup_old_entries()
        
        # Start Flask server in a separate thread
        print("🌐 Starting Flask web server on port 5000...")
        flask_thread = threading.Thread(target=run_flask, daemon=True)
        flask_thread.start()
        
        print(f"✅ Web player available at: {RENDER_EXTERNAL_URL}")
        
        # Start the Telegram bot
        print("🤖 Starting Telegram bot...")
        app.run()
        
    except KeyboardInterrupt:
        print("\n🛑 Bot stopped by user")
    except Exception as e:
        print(f"❌ Bot crashed with error: {e}")
    finally:
        print("👋 Bot has stopped.")