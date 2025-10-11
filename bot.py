import os
import asyncio
import logging
from typing import Optional, Dict, Tuple
from pathlib import Path

from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardButton, InlineKeyboardMarkup
from pyrogram.enums import ParseMode
import boto3
from botocore.exceptions import ClientError

from config import config

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class WasabiTelegramBot:
    def __init__(self):
        # Validate configuration
        if not config.validate_config():
            raise ValueError("Invalid configuration. Please check your environment variables.")
        
        # Initialize Wasabi S3 client
        self.s3_client = boto3.client(
            's3',
            endpoint_url=config.wasabi_endpoint,
            aws_access_key_id=config.WASABI_ACCESS_KEY,
            aws_secret_access_key=config.WASABI_SECRET_KEY,
            region_name=config.WASABI_REGION
        )
        
        # Initialize Telegram client
        self.app = Client(
            "wasabi_bot",
            api_id=config.API_ID,
            api_hash=config.API_HASH,
            bot_token=config.BOT_TOKEN,
            workers=config.WORKERS,
            max_concurrent_transmissions=config.MAX_CONCURRENT_TRANSMISSIONS
        )
        
        # Store config reference
        self.config = config
        
        # User sessions for file operations
        self.user_sessions: Dict[int, Dict] = {}
        
        self.setup_handlers()
    
    def setup_handlers(self):
        """Setup message handlers"""
        
        @self.app.on_message(filters.command("start"))
        async def start_command(client, message: Message):
            await message.reply_text(
                "🤖 **Wasabi Storage Bot**\n\n"
                "I can help you upload files to Wasabi storage and download them back.\n\n"
                "**Commands:**\n"
                "/upload - Upload a file to Wasabi\n"
                "/download - Download a file from Wasabi\n"
                "/list - List your files in Wasabi\n"
                "/rename - Rename a file\n"
                "/thumbnail - Set custom thumbnail\n"
                "/prefix - Set custom filename prefix\n"
                "/help - Show this help message\n\n"
                "📁 **Max File Size:** 4GB\n"
                "☁️ **Storage:** Wasabi Cloud\n\n"
                "Just send me any file to upload it automatically!",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📤 Upload File", callback_data="upload_help")],
                    [InlineKeyboardButton("📥 Download File", callback_data="download_help")]
                ])
            )
        
        @self.app.on_message(filters.command("help"))
        async def help_command(client, message: Message):
            await start_command(client, message)
        
        @self.app.on_message(filters.command("upload"))
        async def upload_command(client, message: Message):
            await message.reply_text(
                "📤 **Upload File to Wasabi**\n\n"
                "Simply send me any file (document, video, audio, photo) and I'll upload it to Wasabi storage.\n\n"
                "**Customization Options:**\n"
                "• `/rename new_filename` - Set custom filename\n"
                "• `/thumbnail` (reply to image) - Set custom thumbnail\n"
                "• `/prefix myprefix_` - Set filename prefix\n\n"
                "**Supported Files:**\n"
                "• Documents (up to 4GB)\n"
                "• Videos (up to 4GB)\n"
                "• Audio files\n"
                "• Photos\n\n"
                "Just send your file now! 🚀",
                parse_mode=ParseMode.MARKDOWN
            )
        
        @self.app.on_message(filters.document | filters.video | filters.audio | filters.photo)
        async def handle_files(client, message: Message):
            user_id = message.from_user.id
            
            # Check file size
            file_size = await self.get_file_size(message)
            if file_size > config.MAX_FILE_SIZE:
                await message.reply_text(
                    f"❌ **File too large!**\n\n"
                    f"**File Size:** {self.format_size(file_size)}\n"
                    f"**Max Size:** {self.format_size(config.MAX_FILE_SIZE)}\n\n"
                    "Please upload a file smaller than 4GB.",
                    parse_mode=ParseMode.MARKDOWN
                )
                return
            
            # Initialize user session if not exists
            if user_id not in self.user_sessions:
                self.user_sessions[user_id] = {
                    'rename': None,
                    'thumbnail': None,
                    'prefix': None
                }
            
            await self.upload_file_to_wasabi(message)
        
        @self.app.on_message(filters.command("rename"))
        async def rename_command(client, message: Message):
            if len(message.command) < 2:
                await message.reply_text(
                    "📝 **Set Custom Filename**\n\n"
                    "Please provide a new filename.\n\n"
                    "**Example:**\n"
                    "`/rename my_document.pdf`\n"
                    "`/rename vacation_video.mp4`\n\n"
                    "This will be used for your next upload.",
                    parse_mode=ParseMode.MARKDOWN
                )
                return
            
            user_id = message.from_user.id
            new_name = ' '.join(message.command[1:])
            
            if user_id not in self.user_sessions:
                self.user_sessions[user_id] = {}
            
            self.user_sessions[user_id]['rename'] = new_name
            await message.reply_text(
                f"✅ **Filename Set**\n\n"
                f"Your next upload will be saved as:\n"
                f"`{new_name}`",
                parse_mode=ParseMode.MARKDOWN
            )
        
        @self.app.on_message(filters.command("prefix"))
        async def prefix_command(client, message: Message):
            if len(message.command) < 2:
                await message.reply_text(
                    "🏷️ **Set Filename Prefix**\n\n"
                    "Please provide a prefix for your files.\n\n"
                    "**Example:**\n"
                    "`/prefix myfiles_`\n"
                    "`/prefix 2024_`\n"
                    "`/prefix project_docs_`\n\n"
                    "This will be added to the beginning of your filenames.",
                    parse_mode=ParseMode.MARKDOWN
                )
                return
            
            user_id = message.from_user.id
            prefix = ' '.join(message.command[1:])
            
            if user_id not in self.user_sessions:
                self.user_sessions[user_id] = {}
            
            self.user_sessions[user_id]['prefix'] = prefix
            await message.reply_text(
                f"✅ **Prefix Set**\n\n"
                f"Your next upload will have the prefix:\n"
                f"`{prefix}`",
                parse_mode=ParseMode.MARKDOWN
            )
        
        @self.app.on_message(filters.command("thumbnail"))
        async def thumbnail_command(client, message: Message):
            if not (message.reply_to_message and (message.reply_to_message.photo or message.reply_to_message.document)):
                await message.reply_text(
                    "🖼️ **Set Custom Thumbnail**\n\n"
                    "Please reply to an image with this command to set it as thumbnail.\n\n"
                    "**How to use:**\n"
                    "1. Send an image\n"
                    "2. Reply to it with `/thumbnail`\n\n"
                    "This thumbnail will be used for your next upload.",
                    parse_mode=ParseMode.MARKDOWN
                )
                return
            
            user_id = message.from_user.id
            if user_id not in self.user_sessions:
                self.user_sessions[user_id] = {}
            
            self.user_sessions[user_id]['thumbnail'] = message.reply_to_message
            await message.reply_text(
                "✅ **Thumbnail Set**\n\n"
                "Custom thumbnail has been set! It will be used for your next file upload.",
                parse_mode=ParseMode.MARKDOWN
            )
        
        @self.app.on_message(filters.command("download"))
        async def download_command(client, message: Message):
            if len(message.command) < 2:
                # Show file list if no filename provided
                await self.list_files(message)
                return
            
            filename = ' '.join(message.command[1:])
            await self.download_file_from_wasabi(message, filename)
        
        @self.app.on_message(filters.command("list"))
        async def list_command(client, message: Message):
            await self.list_files(message)
        
        @self.app.on_message(filters.command("stats"))
        async def stats_command(client, message: Message):
            await self.show_stats(message)
    
    async def get_file_size(self, message: Message) -> int:
        """Get file size from message"""
        if message.document:
            return message.document.file_size
        elif message.video:
            return message.video.file_size
        elif message.audio:
            return message.audio.file_size
        elif message.photo:
            return message.photo.file_size
        return 0
    
    async def upload_file_to_wasabi(self, message: Message):
        """Upload file to Wasabi storage with progress"""
        user_id = message.from_user.id
        
        try:
            # Determine file type and get file info
            file_obj, file_type, original_filename = await self.get_file_info(message)
            if not file_obj:
                await message.reply_text("❌ Unsupported file type")
                return
            
            # Apply user preferences
            session = self.user_sessions.get(user_id, {})
            final_filename = session.get('rename') or original_filename
            if session.get('prefix'):
                final_filename = f"{session['prefix']}{final_filename}"
            
            # Clean filename
            final_filename = self.clean_filename(final_filename)
            
            # Download file locally first
            status_msg = await message.reply_text(
                f"📥 **Downloading File**\n\n"
                f"**Filename:** `{original_filename}`\n"
                f"**Size:** {self.format_size(file_obj.file_size)}\n"
                f"**From:** Telegram → Local",
                parse_mode=ParseMode.MARKDOWN
            )
            
            download_path = await self.download_telegram_file(message, file_obj, status_msg)
            if not download_path:
                return
            
            # Upload to Wasabi
            await status_msg.edit_text(
                f"☁️ **Uploading to Wasabi**\n\n"
                f"**Filename:** `{final_filename}`\n"
                f"**Size:** {self.format_size(file_obj.file_size)}\n"
                f"**Destination:** {config.WASABI_BUCKET}",
                parse_mode=ParseMode.MARKDOWN
            )
            
            # Upload with progress
            await self.upload_to_wasabi_with_progress(
                download_path, final_filename, status_msg
            )
            
            # Clean up local file
            os.remove(download_path)
            
            # Clear user session
            if user_id in self.user_sessions:
                self.user_sessions[user_id] = {}
            
            # Generate download link
            download_url = f"{config.wasabi_endpoint}/{config.WASABI_BUCKET}/{final_filename}"
            
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("🌐 Download URL", url=download_url)],
                [InlineKeyboardButton("🗑 Delete File", callback_data=f"delete_{final_filename}")],
                [InlineKeyboardButton("📁 List Files", callback_data="list_files")]
            ])
            
            await status_msg.edit_text(
                f"✅ **Upload Complete!**\n\n"
                f"**Filename:** `{final_filename}`\n"
                f"**Size:** {self.format_size(file_obj.file_size)}\n"
                f"**Storage:** Wasabi Cloud\n"
                f"**Bucket:** `{config.WASABI_BUCKET}`\n\n"
                f"Use `/download {final_filename}` to download anytime.",
                reply_markup=keyboard,
                parse_mode=ParseMode.MARKDOWN
            )
            
        except Exception as e:
            logger.error(f"Upload error: {e}")
            await message.reply_text(f"❌ **Upload Failed**\n\nError: `{str(e)}`", parse_mode=ParseMode.MARKDOWN)
    
    async def get_file_info(self, message: Message) -> Tuple:
        """Extract file information from message"""
        if message.document:
            return message.document, "document", message.document.file_name
        elif message.video:
            return message.video, "video", f"video_{message.id}.mp4"
        elif message.audio:
            return message.audio, "audio", getattr(message.audio, 'file_name', f"audio_{message.id}.mp3")
        elif message.photo:
            return message.photo, "photo", f"photo_{message.id}.jpg"
        return None, None, None
    
    def clean_filename(self, filename: str) -> str:
        """Clean filename for S3 compatibility"""
        # Replace spaces and special characters
        cleaned = filename.replace(' ', '_')
        # Remove problematic characters
        cleaned = ''.join(c for c in cleaned if c.isalnum() or c in '._-')
        return cleaned
    
    async def download_telegram_file(self, message: Message, file_obj, status_msg) -> Optional[str]:
        """Download file from Telegram with progress"""
        try:
            # Create downloads directory
            download_dir = Path(config.DOWNLOAD_PATH)
            download_dir.mkdir(exist_ok=True)
            
            file_path = await message.download(
                file_name=str(download_dir / "temp_file"),
                progress=self.download_progress,
                progress_args=(status_msg, "Downloading from Telegram")
            )
            
            if not file_path:
                await status_msg.edit_text("❌ Failed to download file from Telegram")
                return None
            
            return file_path
            
        except Exception as e:
            logger.error(f"Download error: {e}")
            await status_msg.edit_text(f"❌ Download failed: {str(e)}")
            return None
    
    async def download_progress(self, current, total, status_msg, operation):
        """Progress callback for download"""
        await self.update_progress(status_msg, current, total, operation)
    
    async def upload_to_wasabi_with_progress(self, file_path: str, filename: str, status_msg: Message):
        """Upload file to Wasabi with progress tracking"""
        try:
            file_size = os.path.getsize(file_path)
            
            # Upload to Wasabi
            self.s3_client.upload_file(
                file_path,
                config.WASABI_BUCKET,
                filename,
                Callback=lambda bytes_transferred: asyncio.create_task(
                    self.update_progress(status_msg, bytes_transferred, file_size, "Uploading to Wasabi")
                )
            )
            
        except ClientError as e:
            logger.error(f"Wasabi upload error: {e}")
            raise Exception(f"Wasabi upload failed: {str(e)}")
    
    async def download_file_from_wasabi(self, message: Message, filename: str):
        """Download file from Wasabi and send to user"""
        try:
            status_msg = await message.reply_text(
                f"📥 **Downloading from Wasabi**\n\n"
                f"**Filename:** `{filename}`\n"
                f"**From:** {config.WASABI_BUCKET}",
                parse_mode=ParseMode.MARKDOWN
            )
            
            # Create downloads directory
            download_dir = Path(config.DOWNLOAD_PATH)
            download_dir.mkdir(exist_ok=True)
            
            local_path = download_dir / filename
            
            # Download from Wasabi with progress
            file_size = await self.get_wasabi_file_size(filename)
            if not file_size:
                await status_msg.edit_text("❌ File not found in Wasabi storage")
                return
            
            self.s3_client.download_file(
                config.WASABI_BUCKET,
                filename,
                str(local_path),
                Callback=lambda bytes_transferred: asyncio.create_task(
                    self.update_progress(status_msg, bytes_transferred, file_size, "Downloading from Wasabi")
                )
            )
            
            # Send file to user
            await status_msg.edit_text("📤 **Sending File**\n\nSending file to Telegram...")
            
            await message.reply_document(
                document=str(local_path),
                caption=f"📁 `{filename}`\n✅ Downloaded from Wasabi Storage\n🏪 Bucket: `{config.WASABI_BUCKET}`",
                parse_mode=ParseMode.MARKDOWN
            )
            
            await status_msg.delete()
            
            # Clean up local file
            os.remove(local_path)
            
        except Exception as e:
            logger.error(f"Download error: {e}")
            await message.reply_text(f"❌ **Download Failed**\n\nError: `{str(e)}`", parse_mode=ParseMode.MARKDOWN)
    
    async def list_files(self, message: Message):
        """List files in Wasabi bucket"""
        try:
            status_msg = await message.reply_text("📁 Fetching your files from Wasabi...")
            
            response = self.s3_client.list_objects_v2(Bucket=config.WASABI_BUCKET)
            
            if 'Contents' not in response:
                await status_msg.edit_text("📭 No files found in your Wasabi storage")
                return
            
            files = response['Contents']
            file_list = []
            
            for file in files[:50]:  # Limit to 50 files
                filename = file['Key']
                size = self.format_size(file['Size'])
                file_list.append(f"• `{filename}` ({size})")
            
            file_count = len(files)
            file_text = "\n".join(file_list)
            
            await status_msg.edit_text(
                f"📁 **Files in Wasabi Storage**\n\n"
                f"**Bucket:** `{config.WASABI_BUCKET}`\n"
                f"**Total Files:** {file_count}\n\n"
                f"{file_text}\n\n"
                f"Use `/download filename` to download any file.",
                parse_mode=ParseMode.MARKDOWN
            )
            
        except Exception as e:
            logger.error(f"List files error: {e}")
            await message.reply_text(f"❌ Failed to list files: {str(e)}")
    
    async def show_stats(self, message: Message):
        """Show storage statistics"""
        try:
            response = self.s3_client.list_objects_v2(Bucket=config.WASABI_BUCKET)
            
            if 'Contents' not in response:
                await message.reply_text("📊 **Storage Statistics**\n\nNo files in storage")
                return
            
            files = response['Contents']
            total_size = sum(file['Size'] for file in files)
            file_count = len(files)
            
            await message.reply_text(
                f"📊 **Storage Statistics**\n\n"
                f"**Bucket:** `{config.WASABI_BUCKET}`\n"
                f"**Total Files:** {file_count}\n"
                f"**Total Size:** {self.format_size(total_size)}\n"
                f"**Region:** `{config.WASABI_REGION}`\n"
                f"**Max File Size:** {self.format_size(config.MAX_FILE_SIZE)}",
                parse_mode=ParseMode.MARKDOWN
            )
            
        except Exception as e:
            logger.error(f"Stats error: {e}")
            await message.reply_text(f"❌ Failed to get statistics: {str(e)}")
    
    async def get_wasabi_file_size(self, filename: str) -> Optional[int]:
        """Get file size from Wasabi"""
        try:
            response = self.s3_client.head_object(Bucket=config.WASABI_BUCKET, Key=filename)
            return response['ContentLength']
        except ClientError:
            return None
    
    async def update_progress(self, message: Message, current: int, total: int, operation: str):
        """Update progress message"""
        try:
            percent = (current / total) * 100
            bar_length = 20
            filled_length = int(bar_length * current // total)
            bar = '█' * filled_length + '░' * (bar_length - filled_length)
            
            progress_text = (
                f"**{operation}**\n\n"
                f"`{bar}` {percent:.1f}%\n"
                f"**Progress:** {self.format_size(current)} / {self.format_size(total)}\n"
                f"**Estimated:** {self.format_size(total - current)} remaining"
            )
            
            await message.edit_text(progress_text, parse_mode=ParseMode.MARKDOWN)
        except Exception as e:
            logger.debug(f"Progress update failed: {e}")
    
    @staticmethod
    def format_size(size_bytes: int) -> str:
        """Format file size in human readable format"""
        if size_bytes == 0:
            return "0 B"
        
        size_names = ["B", "KB", "MB", "GB", "TB"]
        i = 0
        while size_bytes >= 1024 and i < len(size_names) - 1:
            size_bytes /= 1024.0
            i += 1
        
        return f"{size_bytes:.2f} {size_names[i]}"
    
    def run(self):
        """Start the bot"""
        logger.info("Starting Wasabi Telegram Bot...")
        logger.info(f"Bucket: {config.WASABI_BUCKET}")
        logger.info(f"Region: {config.WASABI_REGION}")
        logger.info(f"Max File Size: {self.format_size(config.MAX_FILE_SIZE)}")
        self.app.run()

# Callback query handler for inline buttons
@Client.on_callback_query()
async def handle_callback_query(client, callback_query):
    data = callback_query.data
    
    if data.startswith("delete_"):
        filename = data.replace("delete_", "")
        bot = client.wasabi_bot
        
        try:
            bot.s3_client.delete_object(Bucket=bot.config.WASABI_BUCKET, Key=filename)
            await callback_query.answer("✅ File deleted from Wasabi")
            await callback_query.message.edit_text(f"🗑 `{filename}` has been deleted from Wasabi storage.", parse_mode=ParseMode.MARKDOWN)
        except Exception as e:
            await callback_query.answer("❌ Failed to delete file")
            logger.error(f"Delete error: {e}")
    
    elif data == "upload_help":
        await callback_query.message.reply_text(
            "📤 **Upload Help**\n\n"
            "Simply send any file (document, video, audio, photo) and I'll upload it to Wasabi.\n\n"
            "**Customization:**\n"
            "• /rename - Set custom filename\n"
            "• /thumbnail - Set custom thumbnail\n"
            "• /prefix - Add filename prefix\n\n"
            "Send your file now! 🚀",
            parse_mode=ParseMode.MARKDOWN
        )
        await callback_query.answer()
    
    elif data == "download_help":
        await callback_query.message.reply_text(
            "📥 **Download Help**\n\n"
            "**To download a file:**\n"
            "• Use `/list` to see all files\n"
            "• Use `/download filename` to download\n\n"
            "**Examples:**\n"
            "`/download myfile.pdf`\n"
            "`/download vacation_video.mp4`\n\n"
            "Use `/list` to see available files.",
            parse_mode=ParseMode.MARKDOWN
        )
        await callback_query.answer()
    
    elif data == "list_files":
        await callback_query.message.reply_text("📁 Fetching your files...")
        bot = client.wasabi_bot
        await bot.list_files(callback_query.message)
        await callback_query.answer()

if __name__ == "__main__":
    try:
        bot = WasabiTelegramBot()
        bot.app.wasabi_bot = bot  # Store reference for callbacks
        bot.run()
    except Exception as e:
        logger.error(f"Failed to start bot: {e}")
        exit(1)
