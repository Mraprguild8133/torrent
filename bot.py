import os
import time
import boto3
import asyncio
import re
import base64
from threading import Thread
from flask import Flask, render_template
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardButton, InlineKeyboardMarkup
from pyrogram.errors import FloodWait
from dotenv import load_dotenv
import logging
from collections import defaultdict
from datetime import datetime, timedelta
import botocore

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

# Configuration
API_ID = os.getenv("API_ID")
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")
WASABI_ACCESS_KEY = os.getenv("WASABI_ACCESS_KEY")
WASABI_SECRET_KEY = os.getenv("WASABI_SECRET_KEY")
WASABI_BUCKET = os.getenv("WASABI_BUCKET")
WASABI_REGION = os.getenv("WASABI_REGION", "us-east-1")
RENDER_URL = os.getenv("RENDER_URL", "http://localhost:8000")
MAX_FILE_SIZE = 2000 * 1024 * 1024  # 2GB

# Validate environment variables
missing_vars = []
for var_name, var_value in [
    ("API_ID", API_ID),
    ("API_HASH", API_HASH),
    ("BOT_TOKEN", BOT_TOKEN),
    ("WASABI_ACCESS_KEY", WASABI_ACCESS_KEY),
    ("WASABI_SECRET_KEY", WASABI_SECRET_KEY),
    ("WASABI_BUCKET", WASABI_BUCKET)
]:
    if not var_value:
        missing_vars.append(var_name)

if missing_vars:
    raise Exception(f"Missing environment variables: {', '.join(missing_vars)}")

# Initialize clients
app = Client("wasabi_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# Configure Wasabi S3 client
try:
    wasabi_endpoint_url = f'https://s3.{WASABI_REGION}.wasabisys.com'
    
    # Wasabi requires special configuration
    s3_client = boto3.client(
        's3',
        endpoint_url=wasabi_endpoint_url,
        aws_access_key_id=WASABI_ACCESS_KEY,
        aws_secret_access_key=WASABI_SECRET_KEY,
        region_name=WASABI_REGION,
        config=boto3.session.Config(
            s3={'addressing_style': 'virtual'},
            signature_version='s3v4'
        )
    )
    
    # Test connection
    s3_client.head_bucket(Bucket=WASABI_BUCKET)
    logger.info("Successfully connected to Wasabi bucket")
    
except Exception as e:
    logger.error(f"Wasabi connection failed: {e}")
    # Try alternative endpoint format (some regions use different formats)
    try:
        wasabi_endpoint_url = f'https://{WASABI_BUCKET}.s3.{WASABI_REGION}.wasabisys.com'
        s3_client = boto3.client(
            's3',
            endpoint_url=wasabi_endpoint_url,
            aws_access_key_id=WASABI_ACCESS_KEY,
            aws_secret_access_key=WASABI_SECRET_KEY,
            region_name=WASABI_REGION
        )
        s3_client.head_bucket(Bucket=WASABI_BUCKET)
        logger.info("Successfully connected to Wasabi bucket with alternative endpoint")
    except Exception as alt_e:
        logger.error(f"Alternative connection also failed: {alt_e}")
        raise Exception(f"Could not connect to Wasabi: {alt_e}")

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
        # Add padding if needed for base64 decoding
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
# Helper Functions
# -----------------------------
MEDIA_EXTENSIONS = {
    'video': ['.mp4', '.mov', '.avi', '.mkv', '.webm', '.m4v'],
    'audio': ['.mp3', '.m4a', '.ogg', '.wav', '.flac'],
    'image': ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp']
}

def get_file_type(filename):
    ext = os.path.splitext(filename)[1].lower()
    for file_type, extensions in MEDIA_EXTENSIONS.items():
        if ext in extensions:
            return file_type
    return 'other'

def generate_player_url(filename, presigned_url):
    if not RENDER_URL:
        return None
    file_type = get_file_type(filename)
    if file_type in ['video', 'audio', 'image']:
        encoded_url = base64.urlsafe_b64encode(presigned_url.encode()).decode().rstrip('=')
        return f"{RENDER_URL}/player/{file_type}/{encoded_url}"
    return None

def humanbytes(size):
    """Convert bytes to human readable format"""
    if not size:
        return "0 B"
    power = 1024
    units = ["B", "KB", "MB", "GB", "TB"]
    for unit in units:
        if size < power:
            return f"{size:.2f} {unit}"
        size /= power
    return f"{size:.2f} TB"

def sanitize_filename(filename):
    """Remove potentially dangerous characters from filenames"""
    filename = re.sub(r'[^a-zA-Z0-9 _.-]', '_', filename)
    if len(filename) > 200:
        name, ext = os.path.splitext(filename)
        filename = name[:200-len(ext)] + ext
    return filename

def get_user_folder(user_id):
    return f"user_{user_id}"

def create_download_keyboard(presigned_url, player_url=None):
    """Create inline keyboard with download option"""
    keyboard = []
    
    if player_url:
        keyboard.append([InlineKeyboardButton("🎬 Web Player", url=player_url)])
    
    keyboard.append([InlineKeyboardButton("📥 Direct Download", url=presigned_url)])
    
    return InlineKeyboardMarkup(keyboard)

def create_progress_bar(percentage, length=20):
    """Create a visual progress bar"""
    filled = int(length * percentage / 100)
    empty = length - filled
    return '█' * filled + '○' * empty

def format_eta(seconds):
    """Format seconds into human readable ETA"""
    if seconds <= 0:
        return "00:00"
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours > 0:
        return f"{int(hours):02d}:{int(minutes):02d}:{int(seconds):02d}"
    return f"{int(minutes):02d}:{int(seconds):02d}"

def format_elapsed(seconds):
    """Format elapsed time"""
    return f"{int(seconds // 60):02d}:{int(seconds % 60):02d}"

# Rate limiting
user_requests = defaultdict(list)

def is_rate_limited(user_id, limit=5, period=60):
    now = datetime.now()
    user_requests[user_id] = [req_time for req_time in user_requests[user_id] if now - req_time < timedelta(seconds=period)]
    
    if len(user_requests[user_id]) >= limit:
        return True
    
    user_requests[user_id].append(now)
    return False

# -----------------------------
# Bot Handlers
# -----------------------------
@app.on_message(filters.command("start"))
async def start_command(client, message: Message):
    if is_rate_limited(message.from_user.id):
        await message.reply_text("Too many requests. Please try again in a minute.")
        return
        
    await message.reply_text(
        "🚀 Cloud Storage Bot with Web Player\n\n"
        "Send me any file to upload to Wasabi storage\n"
        "Use /download <filename> to download files\n"
        "Use /play <filename> to get web player links\n"
        "Use /list to see your files\n"
        "Use /delete <filename> to remove files\n\n"
        "<b>⚡ Extreme Performance Features:</b>\n"
        "• 2GB file size support\n"
        "• Real-time speed monitoring with smoothing\n"
        "• Memory optimization for large files\n"
        "• TCP Keepalive for stable connections\n\n"
        "<b>💎 Owner:</b> Mraprguild\n"
        "<b>📧 Email:</b> mraprguild@gmail.com\n"
        "<b>📱 Telegram:</b> @Sathishkumar33"
    )

@app.on_message(filters.document | filters.video | filters.audio | filters.photo)
async def upload_file_handler(client, message: Message):
    if is_rate_limited(message.from_user.id):
        await message.reply_text("Too many requests. Please try again in a minute.")
        return
        
    media = message.document or message.video or message.audio or message.photo
    if not media:
        await message.reply_text("Unsupported file type")
        return

    # Get file size
    if message.photo:
        # For photos, get the largest available size
        file_size = message.photo.sizes[-1].file_size
    else:
        file_size = media.file_size
    
    # Check file size limit
    if file_size > MAX_FILE_SIZE:
        await message.reply_text(f"File too large. Maximum size is {humanbytes(MAX_FILE_SIZE)}")
        return

    status_message = await message.reply_text("📥 Downloading...\n[○○○○○○○○○○○○] 0.0%\nProcessed: 0.00B of 0000MB\nSpeed: 0.00B/s | ETA: -\nElapsed: 00s\nUpload: Telegram\nDownload: Wasabi")

    download_start_time = time.time()
    last_update_time = time.time()
    processed_bytes = 0
    last_processed_bytes = 0
    start_time = time.time()

    async def progress_callback(current, total):
        nonlocal processed_bytes, last_update_time, last_processed_bytes
        processed_bytes = current
        current_time = time.time()
        
        # Update progress every 1 second to avoid flooding
        if current_time - last_update_time >= 1:
            percentage = (current / total) * 100
            elapsed_time = current_time - start_time
            
            # Calculate speed
            speed = (current - last_processed_bytes) / (current_time - last_update_time)
            
            # Calculate ETA
            if speed > 0:
                eta = (total - current) / speed
            else:
                eta = 0
            
            # Format progress message
            progress_bar = create_progress_bar(percentage)
            progress_text = (
                f"📥 Downloading...\n"
                f"[{progress_bar}] {percentage:.1f}%\n"
                f"Processed: {humanbytes(current)} of {humanbytes(total)}\n"
                f"Speed: {humanbytes(speed)}/s | ETA: {format_eta(eta)}\n"
                f"Elapsed: {format_elapsed(elapsed_time)}\n"
                f"Upload: Telegram\n"
                f"Download: Wasabi"
            )
            
            try:
                await status_message.edit_text(progress_text)
                last_update_time = current_time
                last_processed_bytes = current
            except FloodWait as e:
                await asyncio.sleep(e.value)
            except Exception:
                pass  # Ignore other errors during progress updates

    try:
        # Download file with progress callback
        file_path = await message.download(progress=progress_callback)
        file_name = sanitize_filename(os.path.basename(file_path))
        user_file_name = f"{get_user_folder(message.from_user.id)}/{file_name}"
        
        # Update status to uploading
        await status_message.edit_text("📤 Uploading to Wasabi...")
        
        # Upload to Wasabi
        await asyncio.to_thread(
            s3_client.upload_file,
            file_path,
            WASABI_BUCKET,
            user_file_name
        )
        
        # Generate shareable link
        presigned_url = s3_client.generate_presigned_url(
            'get_object', 
            Params={'Bucket': WASABI_BUCKET, 'Key': user_file_name}, 
            ExpiresIn=86400
        )
        
        # Generate player URL if supported
        player_url = generate_player_url(file_name, presigned_url)
        
        # Create keyboard with options
        keyboard = create_download_keyboard(presigned_url, player_url)
        
        total_time = time.time() - start_time
        response_text = (
            f"✅ Upload complete!\n\n"
            f"📁 File: {file_name}\n"
            f"📦 Size: {humanbytes(file_size)}\n"
            f"⏱️ Time: {format_elapsed(total_time)}\n"
            f"⏰ Link expires: 24 hours"
        )
        
        if player_url:
            response_text += f"\n\n🎬 Web Player: {player_url}"
        
        await status_message.edit_text(
            response_text,
            reply_markup=keyboard
        )
        
    except Exception as e:
        logger.error(f"Upload error: {e}")
        await status_message.edit_text(f"❌ Error: {str(e)}")
    finally:
        if 'file_path' in locals() and os.path.exists(file_path):
            os.remove(file_path)

@app.on_message(filters.command("download"))
async def download_file_handler(client, message: Message):
    if is_rate_limited(message.from_user.id):
        await message.reply_text("Too many requests. Please try again in a minute.")
        return
        
    if len(message.command) < 2:
        await message.reply_text("Usage: /download <filename>")
        return

    file_name = " ".join(message.command[1:])
    user_file_name = f"{get_user_folder(message.from_user.id)}/{file_name}"
    
    status_message = await message.reply_text(f"Generating download link for {file_name}...")
    
    try:
        # Check if file exists
        s3_client.head_object(Bucket=WASABI_BUCKET, Key=user_file_name)
        
        # Generate presigned URL
        presigned_url = s3_client.generate_presigned_url(
            'get_object', 
            Params={'Bucket': WASABI_BUCKET, 'Key': user_file_name}, 
            ExpiresIn=86400
        )
        
        # Generate player URL if supported
        player_url = generate_player_url(file_name, presigned_url)
        
        # Create keyboard with options
        keyboard = create_download_keyboard(presigned_url, player_url)
        
        response_text = f"📥 Download ready for: {file_name}\n⏰ Link expires: 24 hours"
        
        if player_url:
            response_text += f"\n\n🎬 Web Player: {player_url}"
        
        await status_message.edit_text(
            response_text,
            reply_markup=keyboard
        )

    except botocore.exceptions.ClientError as e:
        error_code = e.response['Error']['Code']
        if error_code == '404':
            await status_message.edit_text("File not found.")
        else:
            await status_message.edit_text(f"S3 Error: {str(e)}")
    except Exception as e:
        logger.error(f"Download error: {e}")
        await status_message.edit_text(f"Error: {str(e)}")

@app.on_message(filters.command("play"))
async def play_file(client, message: Message):
    if is_rate_limited(message.from_user.id):
        await message.reply_text("Too many requests. Please try again in a minute.")
        return
        
    try:
        if len(message.command) < 2:
            await message.reply_text("Please specify a filename. Usage: /play filename")
            return
            
        filename = " ".join(message.command[1:])
        user_folder = get_user_folder(message.from_user.id)
        user_file_name = f"{user_folder}/{filename}"
        
        # Generate a presigned URL
        presigned_url = s3_client.generate_presigned_url(
            'get_object',
            Params={'Bucket': WASABI_BUCKET, 'Key': user_file_name},
            ExpiresIn=86400
        )
        
        player_url = generate_player_url(filename, presigned_url)
        
        if player_url:
            await message.reply_text(
                f"Player link for {filename}:\n\n{player_url}\n\n"
                "This link will expire in 24 hours."
            )
        else:
            await message.reply_text("This file type doesn't support web playback.")
        
    except Exception as e:
        await message.reply_text(f"File not found or error generating player link: {str(e)}")

@app.on_message(filters.command("list"))
async def list_files(client, message: Message):
    if is_rate_limited(message.from_user.id):
        await message.reply_text("Too many requests. Please try again in a minute.")
        return
        
    try:
        user_prefix = get_user_folder(message.from_user.id) + "/"
        response = s3_client.list_objects_v2(
            Bucket=WASABI_BUCKET, 
            Prefix=user_prefix
        )
        
        if 'Contents' not in response:
            await message.reply_text("No files found")
            return
        
        files = [obj['Key'].replace(user_prefix, "") for obj in response['Contents']]
        files_list = "\n".join([f"• {file}" for file in files[:15]])  # Show first 15 files
        
        if len(files) > 15:
            files_list += f"\n\n...and {len(files) - 15} more files"
        
        await message.reply_text(f"📁 Your files:\n\n{files_list}")
    
    except Exception as e:
        logger.error(f"List files error: {e}")
        await message.reply_text(f"Error: {str(e)}")

@app.on_message(filters.command("delete"))
async def delete_file(client, message: Message):
    if is_rate_limited(message.from_user.id):
        await message.reply_text("Too many requests. Please try again in a minute.")
        return
        
    if len(message.command) < 2:
        await message.reply_text("Usage: /delete <filename>")
        return

    file_name = " ".join(message.command[1:])
    user_file_name = f"{get_user_folder(message.from_user.id)}/{file_name}"
    
    try:
        # Delete file from Wasabi
        s3_client.delete_object(
            Bucket=WASABI_BUCKET,
            Key=user_file_name
        )
        
        await message.reply_text(f"✅ Deleted: {file_name}")
    
    except Exception as e:
        logger.error(f"Delete error: {e}")
        await message.reply_text(f"Error: {str(e)}")

# -----------------------------
# Flask Server Startup
# -----------------------------
print("Starting Flask server on port 8000...")
Thread(target=run_flask, daemon=True).start()

if __name__ == "__main__":
    print("Starting Wasabi Storage Bot with Web Player...")
    app.run()
