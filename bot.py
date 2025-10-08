import os
import time
import math
import asyncio
import httpx
import shutil
import logging
import re
from pyrogram import Client, filters
from pyrogram.types import Message
from config import *

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

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

def is_google_drive_link(url: str) -> bool:
    """Check if the URL is a valid Google Drive link."""
    patterns = [
        r'https?://drive\.google\.com/file/d/([a-zA-Z0-9_-]+)',
        r'https?://drive\.google\.com/open\?id=([a-zA-Z0-9_-]+)',
        r'https?://docs\.google\.com/uc\?export=download&id=([a-zA-Z0-9_-]+)'
    ]
    return any(re.search(pattern, url) for pattern in patterns)

def extract_file_id(gdrive_url: str) -> str:
    """Extract file ID from Google Drive URL."""
    patterns = [
        r'/file/d/([a-zA-Z0-9_-]+)',
        r'/open\?id=([a-zA-Z0-9_-]+)',
        r'uc\?export=download&id=([a-zA-Z0-9_-]+)'
    ]
    
    for pattern in patterns:
        match = re.search(pattern, gdrive_url)
        if match:
            return match.group(1)
    return None

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
                f"➢ **Processed:** {humanbytes(current)}\n"
                f"➢ **Speed:** {humanbytes(speed)}/s\n"
                f"➢ **ETA:** {time.strftime('%H:%M:%S', time.gmtime(eta))}"
            )
        except Exception:
            pass

# --------------------------------- #
# --- GDTOT UPLOAD HANDLER --- #
# --------------------------------- #

async def upload_gdrive_to_gdtot(gdrive_url: str, message: Message) -> str:
    """
    Uploads Google Drive link to GDTOT and returns the GDTOT link.
    """
    await message.edit_text("🔗 **Processing Google Drive Link...**")
    
    try:
        # Prepare the request data
        data = {
            "email": GDTOT_EMAIL,
            "api_token": API_KEY,
            "url": gdrive_url
        }
        
        logger.info(f"Uploading GDrive link: {gdrive_url}")
        
        headers = {
            'Content-Type': 'application/json',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                GDTOT_API_URL,
                json=data,
                headers=headers
            )
            
            logger.info(f"API Response Status: {response.status_code}")
            logger.info(f"API Response: {response.text}")
            
            response.raise_for_status()
            response_data = response.json()
            
            # Parse the response based on expected format
            if response_data.get("status") == "success":
                gdtot_link = response_data.get("gdtot_link") or response_data.get("url") or response_data.get("download_url")
                if gdtot_link:
                    return gdtot_link
                else:
                    await message.edit_text("❌ **Upload successful but no link returned**")
                    return None
            else:
                error_msg = response_data.get("message", response_data.get("error", "Unknown error"))
                await message.edit_text(f"**Upload Failed:** {error_msg}")
                return None
                
    except httpx.HTTPStatusError as e:
        error_msg = f"**HTTP Error {e.response.status_code}**"
        try:
            error_data = e.response.json()
            error_msg += f"\n`{error_data}`"
        except:
            error_msg += f"\n`{e.response.text}`"
        
        await message.edit_text(error_msg)
        logger.error(f"HTTP Error: {e}")
        return None
        
    except httpx.RequestError as e:
        await message.edit_text("**Network Error:** Failed to connect to GDTOT service")
        logger.error(f"Network Error: {e}")
        return None
        
    except Exception as e:
        await message.edit_text(f"**Upload Error:** {str(e)}")
        logger.error(f"Upload Error: {e}")
        return None

# --------------------- #
# --- BOT HANDLERS --- #
# --------------------- #

@app.on_message(filters.command("start"))
async def start_handler(_, message: Message):
    """Handles the /start command."""
    await message.reply_text(
        "**Welcome to GDTOT Uploader Bot!** 👋\n\n"
        "I can convert Google Drive links to GDTOT links.\n\n"
        "**How to use:**\n"
        "1. Send a Google Drive share link\n"
        "2. Or use /gdrive command with your link\n"
        "3. I'll convert it to a GDTOT download link\n\n"
        "**Supported:** Google Drive file links",
        quote=True
    )

@app.on_message(filters.command("gdrive"))
async def gdrive_handler(_, message: Message):
    """Handles Google Drive link conversion."""
    if len(message.command) < 2:
        await message.reply_text(
            "**Usage:** `/gdrive <google_drive_link>`\n\n"
            "**Example:**\n"
            "`/gdrive https://drive.google.com/file/d/1ABC123xyz/view`",
            quote=True
        )
        return
    
    gdrive_url = message.command[1]
    
    if not is_google_drive_link(gdrive_url):
        await message.reply_text(
            "❌ **Invalid Google Drive Link**\n\n"
            "Please provide a valid Google Drive file link.\n"
            "**Format:** `https://drive.google.com/file/d/FILE_ID/view`",
            quote=True
        )
        return
    
    status_message = await message.reply_text(
        "🔗 **Validating Google Drive Link...**",
        quote=True
    )
    
    # Upload to GDTOT
    gdtot_link = await upload_gdrive_to_gdtot(gdrive_url, status_message)
    
    if gdtot_link:
        await status_message.edit_text(
            f"**✅ Conversion Successful!**\n\n"
            f"**Original Link:**\n`{gdrive_url}`\n\n"
            f"**GDTOT Link:**\n{gdtot_link}\n\n"
            f"💡 *Share this GDTOT link for downloads*",
            disable_web_page_preview=True
        )
    else:
        await status_message.edit_text(
            "❌ **Conversion Failed**\n\n"
            "Possible reasons:\n"
            "• Invalid Google Drive link\n"
            "• File not accessible\n"
            "• GDTOT service issue\n"
            "• API limit reached"
        )

@app.on_message(filters.command("test"))
async def test_handler(_, message: Message):
    """Test command to check API connectivity"""
    await message.reply_text("🔧 **Testing GDTOT API Connection...**", quote=True)
    
    try:
        # Test data
        test_data = {
            "email": GDTOT_EMAIL,
            "api_token": API_KEY,
            "url": "https://drive.google.com/file/d/test/view"
        }
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                GDTOT_API_URL,
                json=test_data,
                headers={'Content-Type': 'application/json'}
            )
            
            status = "✅ Online" if response.status_code in [200, 201] else "❌ Offline"
            
            await message.reply_text(
                f"**GDTOT API Status:** {status}\n"
                f"**Response Code:** {response.status_code}\n"
                f"**Email Configured:** {'✅ Yes' if GDTOT_EMAIL else '❌ No'}\n"
                f"**API Key Configured:** {'✅ Yes' if API_KEY else '❌ No'}",
                quote=True
            )
    except Exception as e:
        await message.reply_text(f"**Connection Test Failed:** {str(e)}", quote=True)

@app.on_message(filters.text & filters.private)
async def text_handler(_, message: Message):
    """Handle Google Drive links sent as text."""
    text = message.text.strip()
    
    if is_google_drive_link(text):
        status_message = await message.reply_text(
            "🔗 **Google Drive Link Detected!**\nConverting to GDTOT...",
            quote=True
        )
        
        # Upload to GDTOT
        gdtot_link = await upload_gdrive_to_gdtot(text, status_message)
        
        if gdtot_link:
            await status_message.edit_text(
                f"**✅ Conversion Successful!**\n\n"
                f"**GDTOT Download Link:**\n{gdtot_link}\n\n"
                f"💡 *Share this link for downloads*",
                disable_web_page_preview=True
            )
        else:
            await status_message.edit_text(
                "❌ **Conversion Failed**\n\n"
                "Please check:\n"
                "• The Google Drive link is valid and public\n"
                "• Your API credentials are correct\n"
                "• Try again later"
            )

# ----------------- #
# --- RUN BOT --- #
# ----------------- #
if __name__ == "__main__":
    print("🚀 GDTOT Bot is starting...")
    print(f"✅ Email: {GDTOT_EMAIL}")
    print(f"✅ API Key: {'Configured' if API_KEY else 'NOT CONFIGURED'}")
    print("🤖 Bot is ready to convert Google Drive links")
    app.run()
