import logging
import os
import re
import hashlib
import tempfile
from urllib.parse import urlparse, parse_qs
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
    'open.nyaatorrents.info', 'exodus.desync.com', 'tracker.publicbt.com',
    'tracker.coppersurfer.tk', 'tracker.istole.it', 'tracker.ccc.de'
}

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

def parse_magnet_link(magnet_uri: str) -> dict:
    """
    Parse magnet link and extract information.
    
    magnet:?xt=urn:btih:INFO_HASH&dn=NAME&tr=TRACKER_URL&tr=TRACKER_URL...
    """
    try:
        parsed = urlparse(magnet_uri)
        if parsed.scheme != 'magnet':
            raise ValueError("Not a magnet URI")
        
        query_params = parse_qs(parsed.query)
        result = {
            'info_hash': None,
            'name': None,
            'trackers': [],
            'exact_topic': None,
            'ws': None,
            'kt': None
        }
        
        # Extract info hash
        if 'xt' in query_params:
            for xt in query_params['xt']:
                if xt.startswith('urn:btih:'):
                    result['info_hash'] = xt[9:]  # Remove 'urn:btih:'
                    break
        
        # Extract display name
        if 'dn' in query_params:
            result['name'] = query_params['dn'][0]
        
        # Extract trackers
        if 'tr' in query_params:
            result['trackers'] = query_params['tr']
        
        # Extract exact topic
        if 'xs' in query_params:
            result['exact_topic'] = query_params['xs'][0]
        
        # Extract web seeds
        if 'ws' in query_params:
            result['ws'] = query_params['ws']
        
        # Extract keyword topic
        if 'kt' in query_params:
            result['kt'] = query_params['kt'][0]
        
        return result
    
    except Exception as e:
        raise ValueError(f"Invalid magnet link: {e}")

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
        'created_by': None,
        'info_hash': None
    }
    
    info = torrent_data.get(b'info', {})
    
    # Calculate info hash
    analysis['info_hash'] = calculate_info_hash(info)
    
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

def generate_magnet_link(info_hash: str, name: str = None, trackers: list = None) -> str:
    """Generate a magnet link from torrent info."""
    magnet_parts = [f"magnet:?xt=urn:btih:{info_hash}"]
    
    if name:
        magnet_parts.append(f"dn={name}")
    
    if trackers:
        for tracker in trackers:
            magnet_parts.append(f"tr={tracker}")
    
    return "&".join(magnet_parts)

# --- Bot Command Handlers ---
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Sends a welcome message when the /start command is issued."""
    user = update.effective_user
    welcome_message = (
        f"Hi {user.first_name}! 👋\n\n"
        "I am your **Torrent & Magnet Analyzer**.\n\n"
        "🔍 **What I can do:**\n"
        "• Analyze .torrent file contents\n"
        "• Parse magnet links\n"
        "• Show file list with sizes\n"
        "• Display tracker information\n"
        "• Calculate info hash\n"
        "• Convert between torrent info and magnet links\n"
        "• Security checks\n\n"
        "📁 **Supported inputs:**\n"
        "• `.torrent` files (send as document)\n"
        "• Magnet links (paste as text)\n\n"
        "⚙️ **Commands:**\n"
        "/start - Show this welcome message\n"
        "/help - Get help and usage instructions\n"
        "/magnet INFO_HASH - Generate magnet link from info hash"
    )
    await update.message.reply_html(welcome_message)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Sends help information."""
    help_text = (
        "📖 **Torrent & Magnet Analyzer Help**\n\n"
        "**How to use:**\n"
        "1. Send me a .torrent file OR\n"
        "2. Paste a magnet link\n\n"
        "**For .torrent files I'll show:**\n"
        "• File list with sizes • Total size • Tracker information\n"
        "• Security analysis • Technical details • Magnet link\n\n"
        "**For magnet links I'll show:**\n"
        "• Info hash • Name • Trackers • Security analysis\n"
        "• Additional parameters\n\n"
        "**Commands:**\n"
        "/magnet INFO_HASH - Generate magnet link\n"
        "Example: `/magnet 1A2B3C4D5E6F7G8H9I0J`\n\n"
        "🔒 **Security Features:**\n"
        "• File size limits (10MB max)\n"
        "• Tracker domain validation\n"
        "• Safe filename handling\n"
        "• Automatic file cleanup"
    )
    await update.message.reply_text(help_text, parse_mode='Markdown')

async def magnet_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Generate a magnet link from info hash."""
    if not context.args:
        await update.message.reply_text(
            "❌ Please provide an info hash.\n\n"
            "Usage: `/magnet INFO_HASH`\n"
            "Example: `/magnet 1A2B3C4D5E6F7G8H9I0J`",
            parse_mode='Markdown'
        )
        return
    
    info_hash = context.args[0].strip()
    
    # Validate info hash (can be 40 char hex or 32 char base32)
    if len(info_hash) == 40 and all(c in '0123456789abcdefABCDEF' for c in info_hash):
        # Hex format
        magnet_link = generate_magnet_link(info_hash.lower())
    elif len(info_hash) == 32:
        # Base32 format
        magnet_link = generate_magnet_link(info_hash.upper())
    else:
        await update.message.reply_text(
            "❌ Invalid info hash format.\n\n"
            "Info hash should be:\n"
            "• 40 characters (hex) OR\n"
            "• 32 characters (base32)"
        )
        return
    
    response = (
        "🔗 **Generated Magnet Link**\n\n"
        f"**Info Hash:** `{info_hash}`\n"
        f"**Magnet Link:**\n`{magnet_link}`\n\n"
        "You can use this magnet link in your torrent client."
    )
    
    await update.message.reply_text(response, parse_mode='Markdown')

async def handle_torrent_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Processes an uploaded .torrent file."""
    document = update.message.document
    
    # Validate file type
    if not document.file_name or not document.file_name.lower().endswith('.torrent'):
        await update.message.reply_text("❌ This doesn't look like a .torrent file.")
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
        info_hash = analysis['info_hash']
        
        # Generate magnet link
        magnet_link = generate_magnet_link(info_hash, main_name, analysis['trackers'][:5])
        
        # Build response message
        response_parts = []
        
        # Header
        response_parts.append("✅ **Torrent Analysis Complete**")
        response_parts.append(f"**Name:** `{main_name}`")
        response_parts.append(f"**Info Hash:** `{info_hash}`")
        response_parts.append(f"**Magnet Link:** `{magnet_link}`")
        
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
            for i, tracker in enumerate(analysis['trackers'][:5], 1):
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
                if file_count >= 10:
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

async def handle_magnet_link(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Processes magnet links."""
    magnet_uri = update.message.text.strip()
    
    try:
        # Parse magnet link
        magnet_data = parse_magnet_link(magnet_uri)
        
        if not magnet_data['info_hash']:
            await update.message.reply_text("❌ Invalid magnet link: No info hash found.")
            return
        
        # Build response message
        response_parts = []
        response_parts.append("🔗 **Magnet Link Analysis**")
        
        # Info hash
        response_parts.append(f"**Info Hash:** `{magnet_data['info_hash']}`")
        
        # Name
        if magnet_data['name']:
            response_parts.append(f"**Name:** `{magnet_data['name']}`")
        else:
            response_parts.append("**Name:** *Not specified*")
        
        # Trackers
        if magnet_data['trackers']:
            response_parts.append("--- 🌐 Trackers ---")
            for i, tracker in enumerate(magnet_data['trackers'][:5], 1):
                safety = "🟢" if is_safe_tracker(tracker) else "🟡"
                response_parts.append(f"{safety} `{tracker}`")
            if len(magnet_data['trackers']) > 5:
                response_parts.append(f"*... and {len(magnet_data['trackers']) - 5} more trackers*")
        else:
            response_parts.append("--- 🌐 Trackers ---")
            response_parts.append("*No trackers specified*")
        
        # Additional parameters
        additional_params = []
        if magnet_data['exact_topic']:
            additional_params.append(f"Exact Topic: `{magnet_data['exact_topic']}`")
        if magnet_data['ws']:
            additional_params.append(f"Web Seeds: {len(magnet_data['ws'])}")
        if magnet_data['kt']:
            additional_params.append(f"Keywords: `{magnet_data['kt']}`")
        
        if additional_params:
            response_parts.append("--- 🔧 Additional Parameters ---")
            response_parts.extend(additional_params)
        
        # Security notes
        response_parts.append("--- 🔒 Security Notes ---")
        safe_trackers = sum(1 for tracker in magnet_data['trackers'] if is_safe_tracker(tracker))
        if safe_trackers > 0:
            response_parts.append(f"🟢 Found {safe_trackers} known tracker(s)")
        elif magnet_data['trackers']:
            response_parts.append("🟡 No known trackers found - use caution")
        else:
            response_parts.append("🟡 No trackers specified - DHT only")
        
        # Usage tip
        response_parts.append("--- 💡 Usage ---")
        response_parts.append("You can use this magnet link in any torrent client that supports magnet links.")
        
        full_response = "\n".join(response_parts)
        await update.message.reply_text(full_response, parse_mode='Markdown')
        
        logger.info(f"Successfully analyzed magnet link with hash: {magnet_data['info_hash']}")
        
    except ValueError as e:
        await update.message.reply_text(f"❌ {str(e)}")
    except Exception as e:
        logger.error(f"Error processing magnet link: {e}", exc_info=True)
        await update.message.reply_text("❌ Error processing magnet link. Please check the format.")

async def handle_unsupported_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles unsupported file types."""
    await update.message.reply_text(
        "❌ I only support .torrent files and magnet links.\n\n"
        "**Please send:**\n"
        "• A .torrent file OR\n"
        "• A magnet link (text starting with 'magnet:?')\n\n"
        "Use /help for more information."
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
    application.add_handler(CommandHandler("magnet", magnet_command))
    
    # Handle torrent files
    application.add_handler(MessageHandler(filters.Document.ALL & filters.Document.FileExtension("torrent"), handle_torrent_file))
    
    # Handle magnet links (text messages starting with magnet:?)
    application.add_handler(MessageHandler(filters.TEXT & filters.Regex(r'^magnet:\?'), handle_magnet_link))
    
    # Handle unsupported content
    application.add_handler(MessageHandler(filters.Document.ALL, handle_unsupported_file))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_unsupported_file))
    
    # Error handler
    application.add_error_handler(error_handler)

    # Run the bot
    logger.info("Bot is starting...")
    print("🤖 Torrent & Magnet Analyzer Bot is running...")
    print("Press Ctrl+C to stop the bot")
    
    application.run_polling()
    logger.info("Bot has stopped.")

if __name__ == '__main__':
    main()