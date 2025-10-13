import os
import time
import math
import asyncio
import logging
import base64
import aiofiles
import json
import hashlib
from functools import wraps
from urllib.parse import quote
from threading import Thread, Lock
from concurrent.futures import ThreadPoolExecutor

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

# Performance optimization settings
CHUNK_SIZE = 16 * 1024 * 1024
MAX_WORKERS = min(32, (os.cpu_count() or 1) + 4)
BUFFER_SIZE = 256 * 1024

# Thread pool for parallel operations
thread_pool = ThreadPoolExecutor(max_workers=MAX_WORKERS)

# --- Enhanced Data Storage with File Locking ---
class DataManager:
    """Manage authorized users and callback data with robust persistence"""
    
    def __init__(self):
        self.users_file = "authorized_users.json"
        self.callback_file = "callback_data.json"
        self.lock = Lock()
        self.authorized_users = self.load_users()
        self.callback_map = self.load_callbacks()
        self.next_callback_id = self.get_next_callback_id()
        logger.info(f"✅ DataManager initialized with {len(self.authorized_users)} users and {len(self.callback_map)} callbacks")
        
    def get_next_callback_id(self):
        """Get the next available callback ID"""
        with self.lock:
            if self.callback_map:
                max_id = max(int(k) for k in self.callback_map.keys() if k.isdigit())
                return max_id + 1
            return 1
    
    def load_users(self):
        """Load authorized users from file with comprehensive error handling"""
        try:
            if os.path.exists(self.users_file):
                with open(self.users_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    users = set(data.get('users', [ADMIN_ID]))
                    users.add(ADMIN_ID)  # Always include admin
                    logger.info(f"✅ Loaded {len(users)} authorized users from {self.users_file}")
                    return users
            else:
                # Create initial users file
                initial_data = {'users': [ADMIN_ID], 'created_at': time.time()}
                with open(self.users_file, 'w', encoding='utf-8') as f:
                    json.dump(initial_data, f, indent=2)
                logger.info(f"✅ Created new users file with admin: {ADMIN_ID}")
                return {ADMIN_ID}
        except Exception as e:
            logger.error(f"❌ Error loading users from {self.users_file}: {e}")
            return {ADMIN_ID}
    
    def save_users(self):
        """Save authorized users to file with atomic write"""
        with self.lock:
            try:
                temp_file = f"{self.users_file}.tmp"
                with open(temp_file, 'w', encoding='utf-8') as f:
                    json.dump({
                        'users': list(self.authorized_users),
                        'last_updated': time.time(),
                        'total_users': len(self.authorized_users),
                        'updated_at': time.strftime('%Y-%m-%d %H:%M:%S')
                    }, f, indent=2, ensure_ascii=False)
                
                # Atomic replace
                os.replace(temp_file, self.users_file)
                logger.debug(f"✅ Saved {len(self.authorized_users)} users to {self.users_file}")
            except Exception as e:
                logger.error(f"❌ Error saving users to {self.users_file}: {e}")
    
    def load_callbacks(self):
        """Load callback data from file with robust error recovery"""
        callbacks = {}
        try:
            if os.path.exists(self.callback_file):
                with open(self.callback_file, 'r', encoding='utf-8') as f:
                    raw_data = f.read().strip()
                    if not raw_data:
                        logger.warning("⚠️ Callback file is empty")
                        return {}
                    
                    data = json.loads(raw_data)
                    
                    # Validate and clean callback data (48 hours expiration)
                    current_time = time.time()
                    valid_count = 0
                    expired_count = 0
                    
                    for callback_id, callback_data in data.items():
                        timestamp = callback_data.get('timestamp', 0)
                        # Keep callbacks for 48 hours (172800 seconds)
                        if current_time - timestamp < 172800:
                            callbacks[callback_id] = callback_data
                            valid_count += 1
                        else:
                            expired_count += 1
                    
                    logger.info(f"✅ Loaded {valid_count} valid callbacks, expired: {expired_count}")
                    
                    # Save cleaned version if we removed expired entries
                    if expired_count > 0:
                        self.callback_map = callbacks
                        self.save_callbacks()
                        
            else:
                logger.info("ℹ️ No callback file found, will create on first callback")
                
        except json.JSONDecodeError as e:
            logger.error(f"❌ JSON decode error in {self.callback_file}: {e}")
            # Create backup of corrupted file
            if os.path.exists(self.callback_file):
                backup_name = f"{self.callback_file}.corrupted.{int(time.time())}"
                os.rename(self.callback_file, backup_name)
                logger.info(f"✅ Backed up corrupted file to {backup_name}")
        except Exception as e:
            logger.error(f"❌ Error loading callbacks from {self.callback_file}: {e}")
            
        return callbacks
    
    def save_callbacks(self):
        """Save callback data to file with atomic write and backup"""
        with self.lock:
            try:
                if not self.callback_map:
                    logger.debug("ℹ️ No callbacks to save")
                    return
                    
                temp_file = f"{self.callback_file}.tmp"
                with open(temp_file, 'w', encoding='utf-8') as f:
                    json.dump(self.callback_map, f, indent=2, ensure_ascii=False)
                
                # Atomic replace
                os.replace(temp_file, self.callback_file)
                logger.debug(f"✅ Saved {len(self.callback_map)} callbacks to {self.callback_file}")
                
            except Exception as e:
                logger.error(f"❌ Error saving callbacks to {self.callback_file}: {e}")
    
    def add_user(self, user_id):
        """Add user to authorized list"""
        if user_id not in self.authorized_users:
            self.authorized_users.add(user_id)
            self.save_users()
            logger.info(f"✅ Added user {user_id} to authorized list")
            return True
        return False
    
    def remove_user(self, user_id):
        """Remove user from authorized list (except admin)"""
        if user_id != ADMIN_ID and user_id in self.authorized_users:
            self.authorized_users.remove(user_id)
            self.save_users()
            logger.info(f"✅ Removed user {user_id} from authorized list")
            return True
        return False
    
    def is_authorized(self, user_id):
        """Check if user is authorized"""
        return user_id in self.authorized_users
    
    def is_admin(self, user_id):
        """Check if user is admin"""
        return user_id == ADMIN_ID
    
    def store_callback(self, filename, user_id=None, original_filename=None):
        """Store filename and return short callback ID with enhanced metadata"""
        with self.lock:
            callback_id = str(self.next_callback_id)
            
            self.callback_map[callback_id] = {
                'filename': filename,
                'timestamp': time.time(),
                'user_id': user_id,
                'original_filename': original_filename or filename,
                'created_at': time.strftime('%Y-%m-%d %H:%M:%S'),
                'callback_id': callback_id,
                'file_hash': hashlib.md5(filename.encode()).hexdigest()[:8]  # For verification
            }
            
            self.next_callback_id += 1
            
            # Save after each new callback to ensure persistence
            self.save_callbacks()
            
            logger.debug(f"✅ Stored callback {callback_id} for file {filename} (user: {user_id})")
            return callback_id
    
    def get_callback(self, callback_id):
        """Get filename from callback ID with timestamp update"""
        callback_data = self.get_callback_data(callback_id)
        if callback_data:
            return callback_data['filename']
        return None
    
    def get_callback_data(self, callback_id):
        """Get complete callback data with access tracking"""
        callback_data = self.callback_map.get(str(callback_id))
        if callback_data:
            # Update access time
            callback_data['last_accessed'] = time.strftime('%Y-%m-%d %H:%M:%S')
            callback_data['access_count'] = callback_data.get('access_count', 0) + 1
            
            # Periodically save (every 10 accesses or if 5 minutes passed)
            if callback_data['access_count'] % 10 == 0:
                self.save_callbacks()
                
            return callback_data
        return None
    
    def validate_callback(self, callback_id):
        """Validate callback exists and is not expired"""
        callback_data = self.callback_map.get(str(callback_id))
        if not callback_data:
            return False
            
        # Check if callback is expired (48 hours)
        if time.time() - callback_data.get('timestamp', 0) > 172800:
            # Remove expired callback
            del self.callback_map[str(callback_id)]
            self.save_callbacks()
            return False
            
        return True
    
    def cleanup_old_callbacks(self, max_age_hours=48):
        """Remove callbacks older than specified hours"""
        with self.lock:
            current_time = time.time()
            max_age_seconds = max_age_hours * 3600
            
            expired_ids = [
                callback_id for callback_id, data in self.callback_map.items()
                if current_time - data['timestamp'] > max_age_seconds
            ]
            
            if expired_ids:
                for callback_id in expired_ids:
                    del self.callback_map[callback_id]
                self.save_callbacks()
                logger.info(f"🧹 Cleaned up {len(expired_ids)} expired callbacks")
                return len(expired_ids)
            return 0
    
    def get_callback_stats(self):
        """Get callback statistics"""
        current_time = time.time()
        recent_callbacks = [
            data for data in self.callback_map.values()
            if current_time - data['timestamp'] < 3600  # Last hour
        ]
        
        active_users = set()
        for data in self.callback_map.values():
            if data.get('user_id'):
                active_users.add(data['user_id'])
        
        return {
            'total_callbacks': len(self.callback_map),
            'recent_callbacks': len(recent_callbacks),
            'active_users': len(active_users),
            'oldest_callback': min([data['timestamp'] for data in self.callback_map.values()]) if self.callback_map else 0,
            'file_sizes': {k: len(str(v)) for k, v in self.callback_map.items()}
        }
    
    def get_user_stats(self):
        """Get user statistics"""
        return {
            'total_users': len(self.authorized_users),
            'admin_count': 1,
            'regular_users': len(self.authorized_users) - 1
        }
    
    def debug_callback_data(self, callback_id):
        """Debug information for a specific callback"""
        callback_data = self.callback_map.get(str(callback_id))
        if callback_data:
            return {
                'exists': True,
                'filename': callback_data.get('filename'),
                'user_id': callback_data.get('user_id'),
                'timestamp': callback_data.get('timestamp'),
                'age_seconds': time.time() - callback_data.get('timestamp', 0),
                'access_count': callback_data.get('access_count', 0)
            }
        return {'exists': False}

# Global data manager
data_manager = DataManager()

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
        for unit in ['B/s', 'KB/s', 'MB/s', 'GB/s']:
            if speed < 1024.0:
                return f"{speed:.2f} {unit}"
            speed /= 1024.0
        return f"{speed:.2f} TB/s"

# Global stats tracker
transfer_stats = TransferStats()

# --- Helper Functions ---
def is_admin(func):
    @wraps(func)
    async def wrapper(client, message):
        if data_manager.is_admin(message.from_user.id):
            await func(client, message)
        else:
            await message.reply_text("⛔️ Access denied. This command is for the admin only.")
    return wrapper

def is_authorized(func):
    @wraps(func)
    async def wrapper(client, message):
        if data_manager.is_authorized(message.from_user.id):
            await func(client, message)
        else:
            await message.reply_text("⛔️ You are not authorized to use this bot. Contact the admin.")
    return wrapper

def humanbytes(size):
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
    return os.path.splitext(filename)[1].lower()

def is_video_file(filename):
    return get_file_extension(filename) in SUPPORTED_VIDEO_FORMATS

def generate_player_url(filename, presigned_url):
    if not RENDER_URL:
        return None
    if is_video_file(filename):
        encoded_url = base64.urlsafe_b64encode(presigned_url.encode()).decode().rstrip('=')
        return f"{RENDER_URL}/player/video/{encoded_url}"
    return None

def create_link_buttons(direct_url, player_url, filename, user_id, original_filename):
    """Create buttons with persistent callback data"""
    buttons = []
    
    # Store callback with user info - THIS IS THE KEY FIX
    file_id = data_manager.store_callback(filename, user_id, original_filename)
    
    logger.info(f"📝 Created callback {file_id} for file {filename}")
    
    if direct_url:
        buttons.append([InlineKeyboardButton("📥 Direct Download", url=direct_url)])
    
    if player_url:
        buttons.append([InlineKeyboardButton("🎥 Stream Video", url=player_url)])
    
    if direct_url:
        buttons.append([
            InlineKeyboardButton("📋 Copy Direct", callback_data=f"cd_{file_id}"),
            InlineKeyboardButton("📋 Copy Player", callback_data=f"cp_{file_id}")
        ])
    
    # Admin-only buttons
    if data_manager.is_admin(user_id):
        buttons.append([
            InlineKeyboardButton("🗑 Delete File", callback_data=f"del_{file_id}"),
            InlineKeyboardButton("🔄 New Links", callback_data=f"ref_{file_id}")
        ])
    
    return InlineKeyboardMarkup(buttons)

# --- FIXED Callback Query Handler ---
@app.on_callback_query()
async def handle_callback_query(client, callback_query):
    """Handle button callbacks with enhanced validation and error recovery"""
    user_id = callback_query.from_user.id
    data = callback_query.data
    message = callback_query.message
    
    logger.info(f"🔄 Callback received: {data} from user {user_id}")
    
    try:
        if '_' not in data:
            await callback_query.answer("❌ Invalid button data", show_alert=True)
            return
            
        action, file_id = data.split('_', 1)
        
        # Validate callback exists and is not expired
        if not data_manager.validate_callback(file_id):
            logger.warning(f"❌ Callback {file_id} not found or expired for user {user_id}")
            await callback_query.answer("❌ File data expired or invalid", show_alert=True)
            return
        
        callback_data = data_manager.get_callback_data(file_id)
        
        if not callback_data:
            logger.error(f"❌ Callback data missing for {file_id}")
            await callback_query.answer("❌ File data expired or invalid", show_alert=True)
            return
        
        filename = callback_data['filename']
        original_filename = callback_data.get('original_filename', filename)
        
        logger.info(f"✅ Processing {action} for file: {filename} (user: {user_id})")
        
        if action == "cd":  # Copy Direct
            if not data_manager.is_authorized(user_id):
                await callback_query.answer("⛔️ You are not authorized!", show_alert=True)
                return
                
            presigned_url = await generate_presigned_url(filename)
            
            if presigned_url:
                await callback_query.answer("📋 Direct link sent to chat!", show_alert=False)
                await message.reply_text(
                    f"**📥 Direct Download Link**\n\n"
                    f"**File:** `{original_filename}`\n"
                    f"**Link:** `{presigned_url}`",
                    reply_to_message_id=message.id
                )
            else:
                await callback_query.answer("❌ Failed to generate link", show_alert=True)
                
        elif action == "cp":  # Copy Player
            if not data_manager.is_authorized(user_id):
                await callback_query.answer("⛔️ You are not authorized!", show_alert=True)
                return
                
            presigned_url = await generate_presigned_url(filename)
            player_url = generate_player_url(filename, presigned_url) if presigned_url else None
            
            if player_url:
                await callback_query.answer("📋 Player link sent to chat!", show_alert=False)
                await message.reply_text(
                    f"**🎥 Player URL**\n\n"
                    f"**File:** `{original_filename}`\n"
                    f"**Player:** {player_url}",
                    reply_to_message_id=message.id,
                    disable_web_page_preview=True
                )
            else:
                await callback_query.answer("❌ Not a video file or link expired", show_alert=True)
                
        elif action == "del":  # Delete
            if not data_manager.is_admin(user_id):
                await callback_query.answer("⛔️ Only admin can delete files!", show_alert=True)
                return
                
            try:
                s3_client.delete_object(Bucket=WASABI_BUCKET, Key=filename)
                await callback_query.answer("✅ File deleted successfully!", show_alert=True)
                await message.edit_text(
                    f"🗑 **File Deleted**\n\n"
                    f"**File:** `{original_filename}`\n"
                    f"**Stored as:** `{filename}`\n\n"
                    f"File has been permanently removed from storage.",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🔙 Back to Bot", url=f"https://t.me/{client.me.username}")]
                    ])
                )
            except Exception as e:
                logger.error(f"❌ Delete failed for {filename}: {e}")
                await callback_query.answer("❌ Delete failed", show_alert=True)
                
        elif action == "ref":  # Refresh
            if not data_manager.is_authorized(user_id):
                await callback_query.answer("⛔️ You are not authorized!", show_alert=True)
                return
                
            await callback_query.answer("🔄 Generating fresh links...", show_alert=False)
            
            presigned_url = await generate_presigned_url(filename)
            player_url = generate_player_url(filename, presigned_url) if is_video_file(filename) else None
            
            if presigned_url:
                # Create new buttons with fresh callback
                if data_manager.is_admin(user_id):
                    keyboard = create_link_buttons(presigned_url, player_url, filename, user_id, original_filename)
                else:
                    keyboard = create_simple_buttons(presigned_url, player_url, filename, user_id, original_filename)
                
                await message.edit_reply_markup(reply_markup=keyboard)
                await callback_query.answer("✅ Links refreshed!", show_alert=False)
            else:
                await callback_query.answer("❌ Failed to refresh links", show_alert=True)
                
        else:
            await callback_query.answer("❌ Unknown action", show_alert=True)
            
    except Exception as e:
        logger.error(f"❌ Callback error for {data}: {e}")
        await callback_query.answer("❌ An error occurred", show_alert=True)

# --- Debug Command for Callback Issues ---
@app.on_message(filters.command("debug_callback"))
@is_admin
async def debug_callback_handler(client: Client, message: Message):
    """Debug callback data - ADMIN ONLY"""
    try:
        if len(message.text.split()) > 1:
            callback_id = message.text.split()[1]
            debug_info = data_manager.debug_callback_data(callback_id)
            await message.reply_text(f"🔍 Callback Debug:\n```{json.dumps(debug_info, indent=2)}```")
        else:
            stats = data_manager.get_callback_stats()
            await message.reply_text(f"📊 Callback Stats:\n```{json.dumps(stats, indent=2)}```")
    except Exception as e:
        await message.reply_text(f"❌ Debug error: {e}")

# --- File Handler with Guaranteed Callback Persistence ---
@app.on_message(filters.document | filters.video | filters.audio)
async def file_handler(client: Client, message: Message):
    if not data_manager.is_authorized(message.from_user.id):
        await message.reply_text("⛔️ You are not authorized to use this bot.")
        return
        
    if not s3_client:
        await message.reply_text("❌ Wasabi client is not initialized.")
        return

    media = message.document or message.video or message.audio
    file_name = media.file_name
    file_size = media.file_size
    
    if file_size > 4 * 1024 * 1024 * 1024:
        await message.reply_text("❌ File is larger than 4GB, which is not supported.")
        return

    status_message = await message.reply_text("🚀 Starting ultra-fast transfer...")
    
    timestamp = int(time.time())
    safe_filename = f"{timestamp}_{file_name}"
    file_path = f"./downloads/{safe_filename}"
    os.makedirs(os.path.dirname(file_path), exist_ok=True)

    try:
        # Download process
        transfer_stats.start()
        await client.download_media(
            message=message,
            file_name=file_path,
        )
        await status_message.edit_text("✅ Download complete. Starting instant upload...")

        # Upload process
        await upload_to_wasabi_parallel(file_path, safe_filename, status_message)
        await status_message.edit_text("✅ Upload complete! Generating links...")
        
        # Generate URLs
        presigned_url = await generate_presigned_url(safe_filename)
        player_url = generate_player_url(safe_filename, presigned_url) if is_video_file(file_name) else None
        
        # Create buttons - THIS IS WHERE CALLBACK DATA IS CREATED
        keyboard = create_link_buttons(presigned_url, player_url, safe_filename, message.from_user.id, file_name)
        
        final_message = (
            f"✅ **File Uploaded Successfully!** ⚡\n\n"
            f"**File:** `{file_name}`\n"
            f"**Size:** {humanbytes(file_size)}\n"
            f"**Stored as:** `{safe_filename}`\n\n"
            f"**Links valid for 7 days**"
        )
        
        await status_message.edit_text(final_message, reply_markup=keyboard, disable_web_page_preview=True)
        logger.info(f"✅ File upload completed: {safe_filename} for user {message.from_user.id}")

    except Exception as e:
        logger.error(f"❌ Transfer failed: {e}")
        await status_message.edit_text(f"❌ **Transfer failed:**\n`{str(e)}`")
    finally:
        if os.path.exists(file_path):
            os.remove(file_path)

# Add this function for simple buttons
def create_simple_buttons(direct_url, player_url, filename, user_id, original_filename):
    """Create simple buttons for non-admin users"""
    buttons = []
    
    file_id = data_manager.store_callback(filename, user_id, original_filename)
    logger.info(f"📝 Created simple callback {file_id} for file {filename}")
    
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

# Add upload function
async def upload_to_wasabi_parallel(file_path, file_name, status_message):
    """Upload file to Wasabi"""
    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            thread_pool,
            lambda: s3_client.upload_file(file_path, WASABI_BUCKET, file_name)
        )
        return True
    except Exception as e:
        logger.error(f"Upload failed: {e}")
        raise e

# --- Flask App ---
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

def run_flask():
    flask_app.run(host="0.0.0.0", port=8000, debug=False)

# Start Flask in background
Thread(target=run_flask, daemon=True).start()

# --- Main Execution ---
if __name__ == "__main__":
    logger.info("⚡ Ultra-Fast Bot is starting...")
    logger.info(f"👥 Loaded {len(data_manager.authorized_users)} authorized users")
    logger.info(f"📁 Loaded {len(data_manager.callback_map)} callback entries")
    
    # Perform initial cleanup
    cleaned = data_manager.cleanup_old_callbacks()
    if cleaned > 0:
        logger.info(f"🧹 Cleaned {cleaned} expired callbacks on startup")
    
    app.run()