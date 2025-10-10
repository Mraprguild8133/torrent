import os
import asyncio
import logging
import uuid
from typing import Optional, Tuple
from pyrogram import Client, filters
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
        # Validate configuration
        config.validate()
        
        # Initialize Pyrogram client
        self.app = Client(
            "wasabi_bot",
            api_id=config.API_ID,
            api_hash=config.API_HASH,
            bot_token=config.BOT_TOKEN
        )
        
        # Configure boto3 for Wasabi
        self.boto_config = Config(
            region_name=config.WASABI_REGION,
            signature_version='s3v4'
        )
        
        # Create temp directory if it doesn't exist
        os.makedirs(config.TEMP_DIR, exist_ok=True)
    
    async def get_s3_client(self):
        """Get async S3 client for Wasabi"""
        session = aioboto3.Session()
        return session.client(
            's3',
            aws_access_key_id=config.WASABI_ACCESS_KEY,
            aws_secret_access_key=config.WASABI_SECRET_KEY,
            endpoint_url=config.wasabi_endpoint,
            config=self.boto_config
        )
    
    async def upload_to_wasabi(self, file_path: str, object_name: str) -> Tuple[str, str]:
        """Upload file to Wasabi storage and return URL and file info"""
        s3_client = await self.get_s3_client()
        
        try:
            # Get file size for progress tracking
            file_size = os.path.getsize(file_path)
            
            # Upload file
            async with aiofiles.open(file_path, 'rb') as file:
                await s3_client.upload_fileobj(
                    file,
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
            
            return url, self._format_size(file_size)
            
        except Exception as e:
            logger.error(f"Error uploading to Wasabi: {e}")
            raise
    
    async def download_from_wasabi(self, object_name: str, local_path: str):
        """Download file from Wasabi storage"""
        s3_client = await self.get_s3_client()
        
        try:
            await s3_client.download_file(
                config.WASABI_BUCKET,
                object_name,
                local_path
            )
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
                [InlineKeyboardButton("🔗 Copy Link", callback_data=f"copy_{object_name}")]
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
            
        except Exception as e:
            await status_msg.edit_text(f"❌ Upload failed: {str(e)}")
            logger.error(f"Upload error: {e}")
        
        finally:
            # Clean up local file
            if os.path.exists(file_path):
                os.remove(file_path)
    
    async def handle_download_request(self, message: Message, object_name: str):
        """Handle file download from Wasabi"""
        status_msg = await message.reply_text("📥 Starting download from Wasabi...")
        temp_file = os.path.join(config.TEMP_DIR, f"temp_{uuid.uuid4()}.download")
        
        try:
            await status_msg.edit_text("🔄 Downloading from Wasabi storage...")
            
            # Download from Wasabi
            await self.download_from_wasabi(object_name, temp_file)
            
            # Get file info
            file_size = os.path.getsize(temp_file)
            file_name = object_name.split('_', 1)[-1] if '_' in object_name else object_name
            
            await status_msg.edit_text(f"✅ Download complete! Sending file...")
            
            # Send file to user
            async with aiofiles.open(temp_file, 'rb') as file:
                await message.reply_document(
                    document=file,
                    file_name=file_name,
                    caption=f"📁 {file_name}\n💾 {self._format_size(file_size)}"
                )
            
            await status_msg.delete()
            
        except Exception as e:
            await status_msg.edit_text(f"❌ Download failed: {str(e)}")
            logger.error(f"Download error: {e}")
        
        finally:
            # Clean up
            if os.path.exists(temp_file):
                os.remove(temp_file)
    
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
    
    async def start(self):
        """Start the bot and register handlers"""
        
        @self.app.on_message(filters.command("start"))
        async def start_command(client, message: Message):
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
        
        @self.app.on_message(filters.command("help"))
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
        
        @self.app.on_message(filters.media & filters.private)
        async def handle_media(client, message: Message):
            """Handle media files (documents, video, audio, etc.)"""
            try:
                if not message.media:
                    await message.reply_text("Please send a file to upload.")
                    return
                
                # Get file information based on media type
                file_info = self._get_file_info(message)
                if not file_info:
                    await message.reply_text("Unsupported file type.")
                    return
                
                file_name, file_size = file_info
                
                # Check file size limit
                if file_size and file_size > config.MAX_FILE_SIZE:
                    await message.reply_text("❌ File size exceeds 54GB limit.")
                    return
                
                # Download file from Telegram
                status_msg = await message.reply_text("📥 Downloading file from Telegram...")
                
                download_path = await message.download(
                    file_name=os.path.join(config.TEMP_DIR, f"temp_{file_name}"),
                    progress=self._progress_callback,
                    progress_args=(status_msg, "Downloading from Telegram")
                )
                
                await status_msg.edit_text("✅ File downloaded! Starting Wasabi upload...")
                
                # Upload to Wasabi
                await self.handle_file_upload(message, download_path, file_name)
                
            except Exception as e:
                await message.reply_text(f"❌ Error processing file: {str(e)}")
                logger.error(f"Media handling error: {e}")
        
        @self.app.on_callback_query()
        async def handle_callbacks(client, callback_query):
            """Handle button callbacks"""
            try:
                data = callback_query.data
                
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
    
    def _get_file_info(self, message: Message) -> Optional[Tuple[str, int]]:
        """Extract file name and size from message"""
        if message.document:
            return message.document.file_name, message.document.file_size
        elif message.video:
            return f"video_{message.video.file_id}.mp4", message.video.file_size
        elif message.audio:
            return f"audio_{message.audio.file_id}.mp3", message.audio.file_size
        elif message.photo:
            return f"photo_{message.photo.file_id}.jpg", 0
        elif message.animation:
            return f"animation_{message.animation.file_id}.gif", message.animation.file_size
        elif message.sticker:
            return f"sticker_{message.sticker.file_id}.webp", message.sticker.file_size
        else:
            return None
    
    async def _progress_callback(self, current, total, status_msg, operation):
        """Progress callback for upload/download operations"""
        percent = (current / total) * 100
        progress_bar = self._create_progress_bar(percent)
        
        try:
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
    
    async def run(self):
        """Run the bot"""
        await self.start()
        logger.info("Bot started successfully!")
        await self.app.run()

async def main():
    """Main function"""
    try:
        bot = TelegramWasabiBot()
        await bot.run()
    except ValueError as e:
        logger.error(f"Configuration error: {e}")
    except Exception as e:
        logger.error(f"Bot failed to start: {e}")

if __name__ == "__main__":
    asyncio.run(main())
