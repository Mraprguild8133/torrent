# ------------------ bot.py ------------------
#
# This is the main script that runs the Telegram bot.
# It contains all the logic for handling commands and processing files.

import logging
import os
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# The bencode library is specifically for decoding .torrent files
import bencode

# --- Step 1: Import the token from the separate config.py file ---
try:
    from config import TELEGRAM_BOT_TOKEN
except ImportError:
    print("FATAL ERROR: The config.py file was not found.")
    print("Please make sure you have a config.py file in the same directory with your TELEGRAM_BOT_TOKEN.")
    exit()

# --- Step 2: Set up logging ---
# This helps you see what the bot is doing and diagnose any errors.
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Helper Function ---
def format_size(size_bytes: int) -> str:
    """Converts a size in bytes to a human-readable string (KB, MB, GB)."""
    if not isinstance(size_bytes, int) or size_bytes < 0:
        return "0 B"
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 ** 2:
        return f"{size_bytes / 1024:.2f} KB"
    elif size_bytes < 1024 ** 3:
        return f"{size_bytes / (1024 ** 2):.2f} MB"
    else:
        return f"{size_bytes / (1024 ** 3):.2f} GB"

# --- Bot Command and Message Handlers ---

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Sends a welcome message when the /start command is issued."""
    user = update.effective_user
    welcome_message = (
        f"Hi {user.first_name}! 👋\n\n"
        "I am your friendly **Torrent File Analyzer**.\n\n"
        "Just send or forward any `.torrent` file to me, and I'll tell you what's inside it."
    )
    await update.message.reply_html(welcome_message)

async def handle_torrent_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Processes an uploaded .torrent file."""
    document = update.message.document
    if not document.file_name.lower().endswith('.torrent'):
        await update.message.reply_text("This doesn't look like a .torrent file. Please upload a valid file.")
        return

    file_path = "" # Define outside the try block for access in 'finally'
    try:
        # Download the file from Telegram to a temporary local path
        torrent_file = await context.bot.get_file(document.file_id)
        file_path = f"temp_{document.file_id}.torrent"
        await torrent_file.download_to_drive(custom_path=file_path)
        logger.info(f"Downloaded torrent file to {file_path}")

        # Open and decode the torrent file using the bencode library
        with open(file_path, 'rb') as f:
            torrent_data = bencode.decode(f.read())

        # Extract info from the decoded data
        info = torrent_data.get(b'info', {})
        main_name = info.get(b'name', b'N/A').decode('utf-8', 'ignore')
        announce = torrent_data.get(b'announce', b'N/A').decode('utf-8', 'ignore')

        # Build the response message with Markdown for nice formatting
        response_text = f"✅ **Torrent Details for:** `{main_name}`\n\n"
        response_text += f"**Tracker:** `{announce}`\n"
        response_text += "--- 🗂️ Files ---\n"

        total_size = 0
        # Check if it's a single file or multi-file torrent
        if b'files' in info:
            # Multi-file torrent
            for file_info in info[b'files']:
                path_parts = [p.decode('utf-8', 'ignore') for p in file_info[b'path']]
                file_path_str = os.path.join(*path_parts)
                size = file_info[b'length']
                total_size += size
                response_text += f"- `{file_path_str}` ({format_size(size)})\n"
        else:
            # Single-file torrent
            size = info.get(b'length', 0)
            total_size = size
            response_text += f"- `{main_name}` ({format_size(size)})\n"

        response_text += f"\n**Total Size:** {format_size(total_size)}"

        await update.message.reply_text(response_text, parse_mode='Markdown')

    except Exception as e:
        logger.error(f"Error processing torrent file: {e}", exc_info=True)
        await update.message.reply_text(f"❌ Sorry, I couldn't process that file. It might be invalid or corrupted.\nError: {e}")

    finally:
        # Clean up by deleting the downloaded file to save space
        if os.path.exists(file_path):
            os.remove(file_path)
            logger.info(f"Cleaned up and removed {file_path}")


def main() -> None:
    """The main function to start the bot."""
    if TELEGRAM_BOT_TOKEN == "YOUR_TELEGRAM_BOT_TOKEN_HERE" or not TELEGRAM_BOT_TOKEN:
        logger.error("Bot token is not set! Please update it in config.py")
        return

    # Create the Application and pass it your bot's token.
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # --- Register Handlers ---
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(MessageHandler(filters.Document.ALL, handle_torrent_file))

    # Run the bot until you press Ctrl-C
    logger.info("Bot is starting...")
    application.run_polling()
    logger.info("Bot has stopped.")


if __name__ == '__main__':
    main()
