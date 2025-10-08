# ------------------ bot.py ------------------
#
# Enhanced Torrent File Analyzer Bot
# Provides detailed analysis of .torrent files with improved security and features.

import logging
import os
import re
import hashlib
from urllib.parse import urlparse
from datetime import datetime
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# The bencode library is specifically for decoding .torrent files
import bencode

# --- Import configuration ---
try:
    from config import TELEGRAM_BOT_TOKEN
except ImportError:
    try:
        from config import config
        TELEGRAM_BOT_TOKEN = config.TELEGRAM_BOT_TOKEN
    except ImportError:
        print("FATAL ERROR: Could not import TELEGRAM_BOT_TOKEN")
        print("Please make sure you have a config.py file with your TELEGRAM_BOT_TOKEN")
        exit()

# --- Configuration Constants ---
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB
ALLOWED_TRACKER_DOMAINS = {
    'tracker.openbittorrent.com', 'tracker.leechers-paradise.org',
    'open.nyaatorrents.info', 'exodus.desync.com', 'tracker.publicbt.com'
}
SUPPORTED_EXTENSIONS = {'.torrent'}

# --- Logging Setup ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Helper Functions ---
def format_size(size_bytes: int) -> str:
    """Converts a size in bytes to a human-readable string."""
    if not isinstance(size_bytes, int) or size_bytes < 0:
        return "0 B"
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size_bytes < 1024.0:
            return f"{size_bytes:.2f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.2f} PB"

def calculate_info_hash(info_dict: bytes) -> str:
    """Calculate the info hash of a torrent."""
    return hashlib.sha1(bencode.encode(info_dict)).hexdigest()

def is_safe_tracker(tracker_url: str) -> bool:
    """Check if tracker URL is from a known safe domain."""
    try:
        domain = urlparse(tracker_url).netloc
        return domain in ALLOWED_TRACKER_DOMAINS or any(
            safe_domain in domain for safe_domain in ALLOWED_TRACKER_DOMAINS
        )
    except Exception:
        return False

def sanitize_filename(filename: str) -> str:
    """Remove potentially dangerous characters from filenames."""
    return re.sub(r'[^\w\s\-_.()]', '', filename)

def analyze_torrent_structure(torrent_data: dict) -> dict:
    """Comprehensive analysis of torrent structure."""
    analysis = {
        'is_multi_file': False,
        'file_count': 0,
        'total_size': 0,
        'largest_file': {'name': '', 'size': 0},
        'file_extensions': set(),
        'trackers': [],
        'creation_date': None,
        'comment': None,
        'created_by': None
    }
    
    info = torrent_data.get(b'info', {})
    
    # Basic info
    analysis['is_multi_file'] = b'files' in info
    analysis['comment'] = torrent_data.get(b'comment', b'').decode('utf-8', 'ignore') or None
    analysis['created_by'] = torrent_data.get(b'created by', b'').decode('utf-8', 'ignore') or None
    
    # Creation date
    if b'creation date' in torrent_data:
        try:
            analysis['creation_date'] = datetime.fromtimestamp(torrent_data[b'creation date'])
        except (ValueError, OSError):
            pass
    
    # Trackers
    if b'announce-list' in torrent_data:
        for tracker_group in torrent_data[b'announce-list']:
            for tracker in tracker_group:
                analysis['trackers'].append(tracker.decode('utf-8', 'ignore'))
    elif b'announce' in torrent_data:
        analysis['trackers'].append(torrent_data[b'announce'].decode('utf-8', 'ignore'))
    
    # File analysis
    if analysis['is_multi_file']:
        for file_info in info[b'files']:
            size = file_info[b'length']
            analysis['total_size'] += size
            analysis['file_count'] += 1
            
            # Track largest file
            if size > analysis['largest_file']['size']:
                path_parts = [p.decode('utf-8', 'ignore') for p in file_info[b'path']]
                filename = os.path.join(*path_parts)
                analysis['largest_file'] = {'name': filename, 'size': size}
            
            # Track file extensions
            if b'path' in file_info and file_info[b'path']:
                last_part = file_info[b'path'][-1].decode('utf-8', 'ignore')
                ext = os.path.splitext(last_part)[1].lower()
                if ext:
                    analysis['file_extensions'].add(ext)
    else:
        size = info.get(b'length', 0)
        analysis['total_size'] = size
        analysis['file_count'] = 1
        name = info.get(b'name', b'').decode('utf-8', 'ignore')
        analysis['largest_file'] = {'name': name, 'size': size}
        ext = os.path.splitext(name)[1].lower()
        if ext:
            analysis['file_extensions'].add(ext)
    
    return analysis

# --- Bot Command Handlers ---
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Sends a welcome message when the /start command is issued."""
    user = update.effective_user
    welcome_message = (
        f"Hi {user.first_name}! 👋\n\n"
        "I am your friendly **Torrent File Analyzer**.\n\n"
        "🔍 **What I can do:**\n"
        "• Analyze .torrent file contents\n"
        "• Show file list with sizes\n"
        "• Display tracker information\n"
        "• Calculate info hash\n"
        "• Security checks\n\n"
        "📁 **Just send or forward any `.torrent` file to get started!**\n\n"
        "⚙️ **Commands:**\n"
        "/start - Show this welcome message\n"
        "/help - Get help and usage instructions"
    )
    await update.message.reply_html(welcome_message)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Sends help information."""
    help_text = (
        "📖 **Torrent Analyzer Help**\n\n"
        "**How to use:**\n"
        "1. Send me a .torrent file\n"
        "2. I'll analyze it and show you:\n"
        "   • File list with sizes\n"
        "   • Total size\n"
        "   • Tracker information\n"
        "   • Security analysis\n"
        "   • Technical details\n\n"
        "🔒 **Security Features:**\n"
        "• File size limits (10MB max)\n"
        "• Tracker domain validation\n"
        "• Safe filename handling\n"
        "• Automatic file cleanup\n\n"
        "⚠️ **Limitations:**\n"
        "• I only analyze metadata, no downloading\n"
        "• Maximum file size: 10MB\n"
        "• Supports standard .torrent files only"
    )
    await update.message.reply_text(help_text, parse_mode='Markdown')

async def handle_torrent_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Processes an uploaded .torrent file with enhanced security and analysis."""
    document = update.message.document
    
    # Validate file type
    if not document.file_name or not document.file_name.lower().endswith('.torrent'):
        await update.message.reply_text("❌ This doesn't look like a .torrent file. Please upload a valid .torrent file.")
        return
    
    # Check file size
    if document.file_size > MAX_FILE_SIZE:
        await update.message.reply_text(f"❌ File too large. Maximum size is {format_size(MAX_FILE_SIZE)}.")
        return
    
    file_path = ""
    try:
        # Download the file
        torrent_file = await context.bot.get_file(document.file_id)
        safe_filename = sanitize_filename(document.file_name)
        file_path = f"temp_{document.file_id}_{safe_filename}"
        await torrent_file.download_to_drive(custom_path=file_path)
        logger.info(f"Downloaded torrent file: {safe_filename}")
        
        # Read and decode torrent file
        with open(file_path, 'rb') as f:
            torrent_data = bencode.decode(f.read())
        
        # Comprehensive analysis
        info = torrent_data.get(b'info', {})
        analysis = analyze_torrent_structure(torrent_data)
        
        # Basic info
        main_name = info.get(b'name', b'N/A').decode('utf-8', 'ignore')
        info_hash = calculate_info_hash(info)
        
        # Build response message
        response_parts = []
        
        # Header
        response_parts.append(f"✅ **Torrent Analysis Complete**")
        response_parts.append(f"**Name:** `{main_name}`")
        response_parts.append(f"**Info Hash:** `{info_hash}`")
        
        # Creation info
        if analysis['creation_date']:
            response_parts.append(f"**Created:** {analysis['creation_date'].strftime('%Y-%m-%d %H:%M:%S')}")
        if analysis['created_by']:
            response_parts.append(f"**Created By:** `{analysis['created_by']}`")
        if analysis['comment']:
            response_parts.append(f"**Comment:** `{analysis['comment'][:100]}{'...' if len(analysis['comment']) > 100 else ''}`")
        
        # Statistics
        response_parts.append("--- 📊 Statistics ---")
        response_parts.append(f"**Total Size:** {format_size(analysis['total_size'])}")
        response_parts.append(f"**File Count:** {analysis['file_count']}")
        response_parts.append(f"**Multi-file:** {'Yes' if analysis['is_multi_file'] else 'No'}")
        
        if analysis['file_extensions']:
            response_parts.append(f"**File Types:** {', '.join(sorted(analysis['file_extensions']))}")
        
        # Trackers
        if analysis['trackers']:
            response_parts.append("--- 🌐 Trackers ---")
            for i, tracker in enumerate(analysis['trackers'][:5], 1):  # Show first 5 trackers
                safety = "🟢" if is_safe_tracker(tracker) else "🟡"
                response_parts.append(f"{safety} `{tracker}`")
            if len(analysis['trackers']) > 5:
                response_parts.append(f"*... and {len(analysis['trackers']) - 5} more trackers*")
        
        # Files section
        response_parts.append("--- 🗂️ Files ---")
        if analysis['is_multi_file']:
            # Show first 10 files for multi-file torrents
            file_count = 0
            total_shown_size = 0
            
            for file_info in info[b'files']:
                if file_count >= 10:  # Limit to prevent message overflow
                    break
                path_parts = [p.decode('utf-8', 'ignore') for p in file_info[b'path']]
                file_path_str = os.path.join(*path_parts)
                size = file_info[b'length']
                total_shown_size += size
                response_parts.append(f"• `{file_path_str}` ({format_size(size)})")
                file_count += 1
            
            if analysis['file_count'] > 10:
                remaining_size = analysis['total_size'] - total_shown_size
                response_parts.append(f"*... and {analysis['file_count'] - 10} more files ({format_size(remaining_size)})*")
        else:
            # Single file
            size = info.get(b'length', 0)
            response_parts.append(f"• `{main_name}` ({format_size(size)})")
        
        # Largest file info
        if analysis['largest_file']['name']:
            response_parts.append(f"**Largest File:** `{analysis['largest_file']['name']}` ({format_size(analysis['largest_file']['size'])})")
        
        # Security notes
        response_parts.append("--- 🔒 Security Notes ---")
        safe_trackers = sum(1 for tracker in analysis['trackers'] if is_safe_tracker(tracker))
        if safe_trackers > 0:
            response_parts.append(f"🟢 Found {safe_trackers} known tracker(s)")
        else:
            response_parts.append("🟡 No known trackers found - use caution")
        
        if analysis['total_size'] == 0:
            response_parts.append("⚠️ **Warning:** Total size is 0 bytes")
        
        # Send the analysis
        full_response = "\n".join(response_parts)
        await update.message.reply_text(full_response, parse_mode='Markdown')
        
        logger.info(f"Successfully analyzed torrent: {main_name}")
        
    except bencode.BencodeDecodeError as e:
        logger.error(f"Bencode decode error: {e}")
        await update.message.reply_text("❌ Invalid torrent file: Could not decode bencoded data.")
    except Exception as e:
        logger.error(f"Error processing torrent file: {e}", exc_info=True)
        error_msg = (
            "❌ Sorry, I encountered an error processing that file.\n\n"
            "**Possible reasons:**\n"
            "• File is corrupted\n"
            "• Unsupported torrent format\n"
            "• Encoding issues\n"
            "• File is too complex\n\n"
            f"Error: {str(e)}"
        )
        await update.message.reply_text(error_msg)
    
    finally:
        # Clean up downloaded file
        if file_path and os.path.exists(file_path):
            try:
                os.remove(file_path)
                logger.info(f"Cleaned up: {file_path}")
            except Exception as e:
                logger.error(f"Error cleaning up file {file_path}: {e}")

async def handle_unsupported_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles unsupported file types."""
    await update.message.reply_text(
        "❌ I only support .torrent files.\n\n"
        "Please send a valid .torrent file for analysis."
    )

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles errors in the telegram bot."""
    logger.error(f"Update {update} caused error: {context.error}", exc_info=context.error)

def main() -> None:
    """The main function to start the bot."""
    if TELEGRAM_BOT_TOKEN == "TELEGRAM_BOT_TOKEN" or not TELEGRAM_BOT_TOKEN:
        logger.error("Bot token is not set! Please update it in config.py")
        return

    # Create the Application
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # --- Register Handlers ---
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(MessageHandler(filters.Document.ALL & filters.Document.FileExtension("torrent"), handle_torrent_file))
    application.add_handler(MessageHandler(filters.Document.ALL, handle_unsupported_file))
    
    # Error handler
    application.add_error_handler(error_handler)

    # Run the bot
    logger.info("Bot is starting...")
    print("🤖 Torrent Analyzer Bot is running...")
    print("Press Ctrl+C to stop the bot")
    
    application.run_polling()
    logger.info("Bot has stopped.")

if __name__ == '__main__':
    main()
