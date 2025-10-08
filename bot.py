import os
import time
import math
import asyncio
import httpx
import shutil
from pyrogram import Client, filters
from pyrogram.types import Message
from config import *

# Validate configuration on startup
validate_config()

# Ensure downloads directory exists
os.makedirs(DOWNLOAD_PATH, exist_ok=True)

# Initialize the Pyrogram Client
app = Client(
    "gdtot_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN
)

# ----------------- #
# --- HELPERS --- #
# ----------------- #

def humanbytes(size):
    """Converts bytes to a human-readable format."""
    if not size:
        return "0B"
    size = int(size)
    power = 2**10
    n = 0
    power_labels = {0: 'B', 1: 'KB', 2: 'MB', 3: 'GB', 4: 'TB'}
    while size > power and n < len(power_labels) - 1:
        size /= power
        n += 1
    return f"{size:.2f} {power_labels[n]}"


async def progress_callback(current, total, message: Message, start_time, action: str):
    """Updates the progress message."""
    now = time.time()
    diff = now - start_time
    if round(diff % 5.00) == 0 or current == total:
        percentage = current * 100 / total
        speed = current / diff
        elapsed_time = round(diff)
        eta = round((total - current) / speed) if speed > 0 else 0
        
        progress_bar = "[{0}{1}]".format(
            ''.join(["⬢" for _ in range(math.floor(percentage / 10))]),
            ''.join(["⬡" for _ in range(10 - math.floor(percentage / 10))])
        )

        try:
            await message.edit_text(
                f"**{action}**\n"
                f"{progress_bar} {percentage:.2f}%\n"
                f"➢ **Size:** {humanbytes(total)}\n"
                f"➢ **Downloaded:** {humanbytes(current)}\n"
                f"➢ **Speed:** {humanbytes(speed)}/s\n"
                f"➢ **ETA:** {time.strftime('%H:%M:%S', time.gmtime(eta))}"
            )
        except Exception:
            pass

# --------------------------------- #
# --- GDTOT UPLOAD HANDLER --- #
# --------------------------------- #

async def upload_to_gdtot(file_path: str, message: Message) -> str:
    """
    Uploads the file to new27.gdtot.dad and returns the shareable link.
    """
    await message.edit_text("☁️ **Uploading to GDTOT Storage...**")
    
    try:
        file_name = os.path.basename(file_path)
        file_size = os.path.getsize(file_path)
        
        headers = {
            'X-API-KEY': API_KEY,
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        
        # Read file in chunks for large files
        async def file_sender():
            with open(file_path, 'rb') as f:
                while True:
                    chunk = f.read(64 * 1024)  # 64KB chunks
                    if not chunk:
                        break
                    yield chunk
        
        # Prepare the upload data
        files = {
            'file': (file_name, file_sender(), 'application/octet-stream')
        }
        
        data = {
            'name': file_name,
            'size': str(file_size)
        }
        
        async with httpx.AsyncClient(timeout=300.0) as client:
            response = await client.post(
                GDTOT_API_URL,
                files=files,
                data=data,
                headers=headers
            )
            
            response.raise_for_status()
            response_data = response.json()
            
            # Adjust this based on the actual API response structure
            if response_data.get("status") == "success":
                return response_data.get("url") or response_data.get("download_url")
            else:
                error_msg = response_data.get("message", "Unknown error occurred")
                await message.edit_text(f"**Upload Failed:** {error_msg}")
                return None
                
    except httpx.HTTPStatusError as e:
        await message.edit_text(f"**API Error:** Server responded with {e.response.status_code}")
        print(f"HTTP Error: {e}")
        return None
    except httpx.RequestError as e:
        await message.edit_text("**Network Error:** Failed to connect to upload service")
        print(f"Network Error: {e}")
        return None
    except Exception as e:
        await message.edit_text(f"**Upload Error:** {str(e)}")
        print(f"Upload Error: {e}")
        return None

# --------------------- #
# --- BOT HANDLERS --- #
# --------------------- #

@app.on_message(filters.command("start"))
async def start_handler(_, message: Message):
    """Handles the /start command."""
    await message.reply_text(
        "**Welcome to GDTOT Uploader Bot!** 👋\n\n"
        "I can help you upload files to GDTOT storage.\n"
        "Just send me any file (document, video, audio) and I will generate a shareable link for you.\n\n"
        "**Supported:** Documents, Videos, Audio files\n"
        "**Max Size:** 4GB",
        quote=True
    )

@app.on_message(filters.command("help"))
async def help_handler(_, message: Message):
    """Handles the /help command."""
    await message.reply_text(
        "**How to use this bot:**\n\n"
        "1. Send any file (document, video, audio)\n"
        "2. Wait for the download to complete\n"
        "3. The bot will automatically upload to GDTOT\n"
        "4. You'll receive a shareable download link\n\n"
        "**Commands:**\n"
        "/start - Start the bot\n"
        "/help - Show this help message\n"
        "/status - Check bot status",
        quote=True
    )

@app.on_message(filters.command("status"))
async def status_handler(_, message: Message):
    """Handles the /status command."""
    await message.reply_text(
        "🤖 **Bot Status:** Online\n"
        "✅ **Service:** GDTOT Storage\n"
        "💾 **Max File Size:** 4GB\n"
        "🚀 **Ready to receive files!**",
        quote=True
    )

@app.on_message(filters.document | filters.video | filters.audio)
async def file_handler(_, message: Message):
    """Handles incoming files and processes them."""
    
    media = message.document or message.video or message.audio
    if not media:
        await message.reply_text("Please send a valid file.", quote=True)
        return

    file_name = media.file_name or "Unknown"
    file_size = media.file_size

    if file_size > MAX_FILE_SIZE:
        await message.reply_text("❌ **Error:** File size exceeds 4GB limit.", quote=True)
        return

    status_message = await message.reply_text(
        f"**Processing File:**\n"
        f"📄 **Name:** `{file_name}`\n"
        f"📦 **Size:** {humanbytes(file_size)}\n"
        f"⏳ **Starting download...**",
        quote=True
    )

    download_dir = os.path.join(DOWNLOAD_PATH, str(message.id))
    os.makedirs(download_dir, exist_ok=True)
    
    try:
        # Download from Telegram with progress
        start_time = time.time()
        file_path = await message.download(
            file_name=os.path.join(download_dir, file_name),
            progress=progress_callback,
            progress_args=(status_message, start_time, "📥 Downloading...")
        )

        if file_path and os.path.exists(file_path):
            await status_message.edit_text(
                f"✅ **Download Complete!**\n"
                f"📄 **File:** `{file_name}`\n"
                f"📦 **Size:** {humanbytes(file_size)}\n"
                f"☁️ **Starting upload...**"
            )
            
            # Upload to GDTOT service
            upload_link = await upload_to_gdtot(file_path, status_message)

            if upload_link:
                await status_message.edit_text(
                    f"**✅ Upload Successful!**\n\n"
                    f"📄 **File:** `{file_name}`\n"
                    f"📦 **Size:** {humanbytes(file_size)}\n"
                    f"🔗 **Download Link:** {upload_link}\n\n"
                    f"💡 *Link will expire based on GDTOT's policy*",
                    disable_web_page_preview=False
                )
            else:
                await status_message.edit_text("❌ **Upload Failed.** Please try again later.")

    except Exception as e:
        await status_message.edit_text(f"**❌ Error:** {str(e)}")
        print(f"Error processing file: {e}")

    finally:
        # Clean up downloaded files
        if os.path.exists(download_dir):
            try:
                shutil.rmtree(download_dir)
            except Exception as cleanup_error:
                print(f"Cleanup error: {cleanup_error}")

# ----------------- #
# --- RUN BOT --- #
# ----------------- #
if __name__ == "__main__":
    print("🚀 GDTOT Bot is starting...")
    print("✅ Configuration loaded successfully")
    print("🤖 Bot is ready to receive files")
    app.run()
    print("👋 Bot has stopped.")
