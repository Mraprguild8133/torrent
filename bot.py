import os
import asyncio
import logging
import uuid
import sys
import time
from typing import Optional, Tuple
from pyrogram import Client, filters, idle
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.enums import MessageMediaType
import aiofiles
import aioboto3
from botocore.config import Config

from config import config

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class TelegramWasabiBot:
    def __init__(self):
        # Validate configuration first
        try:
            config.validate()
            config.print_config_status()
        except ValueError as e:
            logger.error(f"Configuration error: {e}")
            sys.exit(1)
        
        # Initialize Pyrogram client
        self.app = Client(
            "wasabi_bot",
            api_id=config.API_ID,
            api_hash=config.API_HASH,
            bot_token=config.BOT_TOKEN,
            in_memory=True
        )
        
        # Configure boto3 for Wasabi
        self.boto_config = Config(
            region_name=config.WASABI_REGION,
            signature_version='s3v4'
        )
        
        # Create temp directory if it doesn't exist
        os.makedirs(config.TEMP_DIR, exist_ok=True)
        
        # Rate limiting
        self.user_requests = {}
        
        # Register handlers
        self.register_handlers()
    
    def register_handlers(self):
        """Register all message handlers"""
        
        @self.app.on_message(filters.command("start") & filters.private)
        async def start_command(client, message: Message):
            logger.info(f"Start command from user {message.from_user.id}")
            await message.reply_text(
                "🤖 **Telegram Wasabi Bot**\n\n"
                "Send me any file and I'll upload it to Wasabi storage "
                "and provide you with a direct download link!\n\n"
                "**Features:**\n"
                "• 📤 Upload files up to 54GB\n"
                "• ☁️ Wasabi cloud storage\n"
                "• 🔗 Instant download links\n"
                "• 📊 Progress tracking\n\n"
                "Just send me a file to get started!"
            )
        
        @self.app.on_message(filters.command("help") & filters.private)
        async def help_command(client, message: Message):
            await message.reply_text(
                "**How to use this bot:**\n\n"
                "1. Send any file (document, video, audio, photo)\n"
                "2. Bot will upload it to Wasabi cloud storage\n"
                "3. You'll receive a direct download link\n"
                "4. Link expires in 7 days\n\n"
                "**Supported files:**\n"
                "• Documents (PDF, ZIP, etc.)\n"
                "• Videos (MP4, AVI, etc.)\n"
                "• Audio files (MP3, WAV, etc.)\n"
                "• Images (JPG, PNG, etc.)\n\n"
                "**Max file size:** 54GB"
            )
        
        @self.app.on_message(filters.command("status") & filters.private)
        async def status_command(client, message: Message):
            await message.reply_text("✅ Bot is running and ready to receive files!")
        
        @self.app.on_message(filters.media & filters.private)
        async def handle_media(client, message: Message):
            """Handle media files (documents, video, audio, etc.)"""
            try:
                logger.info(f"Received media from user {message.from_user.id}")
                
                # Check rate limit
                if not await self.check_rate_limit(message.from_user.id):
                    await message.reply_text("⏳ Too many requests. Please wait a minute.")
                    return
                
                if not message.media:
                    await message.reply_text("Please send a file to upload.")
                    return
                
                # Get file information based on media type
                file_info = self._get_file_info(message)
                if not file_info:
                    await message.reply_text("❌ Unsupported file type.")
                    return
                
                file_name, file_size = file_info
                logger.info(f"Processing file: {file_name} ({file_size} bytes)")
                
                # Check file size limit
                if file_size and file_size > config.MAX_FILE_SIZE:
                    await message.reply_text("❌ File size exceeds 54GB limit.")
                    return
                
                # Download file from Telegram
                status_msg = await message.reply_text("📥 Downloading file from Telegram...")
                
                download_path = await message.download(
                    file_name=os.path.join(config.TEMP_DIR, f"temp_{uuid.uuid4()}_{file_name}"),
                    progress=self._progress_callback,
                    progress_args=(status_msg, "Downloading from Telegram")
                )
                
                await status_msg.edit_text("✅ File downloaded! Starting Wasabi upload...")
                
                # Upload to Wasabi
                await self.handle_file_upload(message, download_path, file_name)
                
            except Exception as e:
                error_msg = f"❌ Error processing file: {str(e)}"
                logger.error(f"Media handling error: {e}")
                await message.reply_text(error_msg)
        
        @self.app.on_message(filters.text & filters.private)
        async def handle_text(client, message: Message):
            """Handle text messages"""
            if not message.text.startswith('/'):
                await message.reply_text(
                    "Send me a file to upload to Wasabi storage!\n"
                    "Use /help for instructions."
                )
        
        @self.app.on_callback_query()
        async def handle_callbacks(client, callback_query):
            """Handle button callbacks"""
            try:
                data = callback_query.data
                logger.info(f"Callback received: {data}")
                
                if data.startswith("copy_"):
                    object_name = data[5:]
                    await callback_query.answer(
                        "Use the download link in the message above!",
                        show_alert=True
                    )
                
                await callback_query.answer()
                
            except Exception as e:
                logger.error(f"Callback error: {e}")
                await callback_query.answer("Error processing request", show_alert=True)
    
    async def upload_to_wasabi(self, file_path: str, object_name: str) -> Tuple[str, str]:
        """Upload file to Wasabi storage and return URL and file info"""
        session = aioboto3.Session()
        
        try:
            # Get file size for progress tracking
            file_size = os.path.getsize(file_path)
            logger.info(f"Uploading {file_path} to Wasabi as {object_name}")
            
            async with session.client(
                's3',
                aws_access_key_id=config.WASABI_ACCESS_KEY,
                aws_secret_access_key=config.WASABI_SECRET_KEY,
                endpoint_url=config.wasabi_endpoint,
                config=self.boto_config
            ) as s3_client:
                
                # Upload file using upload_file (which handles large files better)
                await s3_client.upload_file(
                    file_path,
                    config.WASABI_BUCKET,
                    object_name
                )
                
                # Generate presigned URL
                url = await s3_client.generate_presigned_url(
                    'get_object',
                    Params={
                        'Bucket': config.WASABI_BUCKET,
                        'Key': object_name
                    },
                    ExpiresIn=config.DOWNLOAD_URL_EXPIRY
                )
                
                logger.info(f"Upload successful. URL generated for {object_name}")
                return url, self._format_size(file_size)
                
        except Exception as e:
            logger.error(f"Error uploading to Wasabi: {e}")
            raise
    
    async def download_from_wasabi(self, object_name: str, local_path: str):
        """Download file from Wasabi storage"""
        session = aioboto3.Session()
        
        try:
            logger.info(f"Downloading {object_name} from Wasabi to {local_path}")
            
            async with session.client(
                's3',
                aws_access_key_id=config.WASABI_ACCESS_KEY,
                aws_secret_access_key=config.WASABI_SECRET_KEY,
                endpoint_url=config.wasabi_endpoint,
                config=self.boto_config
            ) as s3_client:
                
                await s3_client.download_file(
                    config.WASABI_BUCKET,
                    object_name,
                    local_path
                )
                
            logger.info(f"Download successful: {object_name}")
        except Exception as e:
            logger.error(f"Error downloading from Wasabi: {e}")
            raise
    
    async def handle_file_upload(self, message: Message, file_path: str, file_name: str):
        """Handle file upload with progress updates"""
        status_msg = await message.reply_text("📤 Starting upload to Wasabi...")
        
        try:
            # Generate unique object name
            object_name = f"telegram_files/{uuid.uuid4()}_{file_name}"
            
            # Update status
            await status_msg.edit_text("🔄 Uploading to Wasabi storage...")
            
            # Upload to Wasabi
            download_url, file_size = await self.upload_to_wasabi(file_path, object_name)
            
            # Create download button
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("📥 Download Link", url=download_url)],
                [InlineKeyboardButton("🔗 Copy Info", callback_data=f"copy_{object_name}")]
            ])
            
            await status_msg.edit_text(
                f"✅ **Upload Complete!**\n\n"
                f"**File:** `{file_name}`\n"
                f"**Size:** {file_size}\n"
                f"**Storage:** Wasabi Cloud\n"
                f"**Link Expires:** 7 days\n\n"
                f"Click below to download:",
                reply_markup=keyboard
            )
            
            logger.info(f"Upload completed for {file_name}")
            
        except Exception as e:
            error_msg = f"❌ Upload failed: {str(e)}"
            await status_msg.edit_text(error_msg)
            logger.error(f"Upload error: {e}")
        
        finally:
            # Clean up local file
            if os.path.exists(file_path):
                os.remove(file_path)
                logger.info(f"Cleaned up local file: {file_path}")
    
    def _get_file_info(self, message: Message) -> Optional[Tuple[str, int]]:
        """Extract file name and size from message"""
        try:
            if message.document:
                return message.document.file_name, message.document.file_size
            elif message.video:
                if message.video.file_name:
                    return message.video.file_name, message.video.file_size
                return f"video_{message.video.file_id}.mp4", message.video.file_size
            elif message.audio:
                if message.audio.file_name:
                    return message.audio.file_name, message.audio.file_size
                return f"audio_{message.audio.file_id}.mp3", message.audio.file_size
            elif message.photo:
                return f"photo_{message.photo.file_id}.jpg", 0
            elif message.animation:
                return f"animation_{message.animation.file_id}.gif", message.animation.file_size
            elif message.sticker:
                return f"sticker_{message.sticker.file_id}.webp", message.sticker.file_size
            elif message.voice:
                return f"voice_{message.voice.file_id}.ogg", message.voice.file_size
            elif message.video_note:
                return f"video_note_{message.video_note.file_id}.mp4", message.video_note.file_size
            else:
                return None
        except Exception as e:
            logger.error(f"Error getting file info: {e}")
            return None
    
    def _format_size(self, size_bytes: int) -> str:
        """Format file size in human-readable format"""
        if size_bytes == 0:
            return "0B"
        
        size_names = ["B", "KB", "MB", "GB"]
        i = 0
        while size_bytes >= 1024 and i < len(size_names) - 1:
            size_bytes /= 1024.0
            i += 1
        
        return f"{size_bytes:.2f} {size_names[i]}"
    
    async def _progress_callback(self, current, total, status_msg, operation):
        """Progress callback for upload/download operations"""
        try:
            percent = (current / total) * 100
            progress_bar = self._create_progress_bar(percent)
            
            await status_msg.edit_text(
                f"{operation}...\n"
                f"{progress_bar} {percent:.1f}%\n"
                f"📊 {self._format_size(current)} / {self._format_size(total)}"
            )
        except Exception as e:
            logger.debug(f"Progress update failed: {e}")
    
    def _create_progress_bar(self, percent: float, length: int = 20) -> str:
        """Create a visual progress bar"""
        filled = int(length * percent / 100)
        bar = "█" * filled + "░" * (length - filled)
        return f"[{bar}]"
    
    async def check_rate_limit(self, user_id: int) -> bool:
        """Check if user is within rate limits"""
        now = time.time()
        if user_id not in self.user_requests:
            self.user_requests[user_id] = []
        
        # Keep only requests from last minute
        self.user_requests[user_id] = [
            req_time for req_time in self.user_requests[user_id] 
            if now - req_time < 60
        ]
        
        # Allow up to 5 requests per minute
        if len(self.user_requests[user_id]) >= 5:
            return False
        
        self.user_requests[user_id].append(now)
        return True
    
    async def start(self):
        """Start the bot"""
        try:
            await self.app.start()
            me = await self.app.get_me()
            logger.info(f"Bot started successfully as @{me.username}")
            
            # Print bot info
            print(f"\n🤖 Bot is running as @{me.username}")
            print("📍 Send /start to your bot in Telegram to test")
            print("⏹️  Press Ctrl+C to stop the bot\n")
            
            # Keep the bot running
            await idle()
            
        except Exception as e:
            logger.error(f"Failed to start bot: {e}")
            raise
    
    async def stop(self):
        """Stop the bot"""
        await self.app.stop()
        logger.info("Bot stopped")

async def main():
    """Main function"""
    bot = TelegramWasabiBot()
    
    try:
        await bot.start()
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Bot failed: {e}")
    finally:
        await bot.stop()

if __name__ == "__main__":
    # Check if we're in an async environment
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # If in Jupyter/async environment
            task = loop.create_task(main())
        else:
            # Standard execution
            asyncio.run(main())
    except RuntimeError:
        # No event loop, create one
        asyncio.run(main())
