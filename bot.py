import os
import time
import math
import asyncio
import httpx
import shutil
import logging
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
        
        logger.info(f"Uploading file: {file_name} ({humanbytes(file_size)})")
        
        # First, let's test the API connection with a simple request
        async with httpx.AsyncClient(timeout=30.0) as client:
            # Test API connectivity
            test_response = await client.get("https://new27.gdtot.dad/api/status")
            if test_response.status_code != 200:
                await message.edit_text("❌ **API Service Unavailable**")
                return None
            
        # Prepare for file upload
        headers = {
            'X-API-KEY': API_KEY,
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        
        # Upload using multipart form data
        with open(file_path, 'rb') as file:
            files = {
                'file': (file_name, file, 'application/octet-stream')
            }
            
            data = {
                'key': API_KEY,
                'type': 'file'
            }
            
            async with httpx.AsyncClient(timeout=300.0) as client:
                response = await client.post(
                    GDTOT_API_URL,
                    files=files,
                    data=data,
                    headers=headers
                )
                
                logger.info(f"Upload Response Status: {response.status_code}")
                logger.info(f"Upload Response: {response.text}")
                
                response.raise_for_status()
                response_data = response.json()
                
                # Check different possible response formats
                if response_data.get("status") == "success":
                    return response_data.get("url") or response_data.get("download_url") or response_data.get("link")
                elif response_data.get("error"):
                    error_msg = response_data.get("message", response_data.get("error"))
                    await message.edit_text(f"**Upload Failed:** {error_msg}")
                    return None
                else:
                    # If no clear status, but response is 200, try to extract link
                    if response.status_code == 200:
                        # Try to find any URL in the response
                        for key, value in response_data.items():
                            if isinstance(value, str) and value.startswith('http'):
                                return value
                    
                    await message.edit_text(f"**Unexpected API Response:** {response_data}")
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
        await message.edit_text("**Network Error:** Failed to connect to upload service")
        logger.error(f"Network Error: {e}")
        return None
        
    except Exception as e:
        await message.edit_text(f"**Upload Error:** {str(e)}")
        logger.error(f"Upload Error: {e}")
        return None

# Alternative upload method for testing
async def test_upload_method(file_path: str, message: Message) -> str:
    """
    Alternative upload method with different approach
    """
    try:
        file_name = os.path.basename(file_path)
        
        await message.edit_text("🔧 **Testing alternative upload method...**")
        
        headers = {
            'Authorization': f'Bearer {API_KEY}',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        
        with open(file_path, 'rb') as file:
            files = {'file': (file_name, file)}
            
            async with httpx.AsyncClient(timeout=300.0) as client:
                response = await client.post(
                    GDTOT_API_URL,
                    files=files,
                    headers=headers
                )
                
                logger.info(f"Alt Method Response: {response.status_code} - {response.text}")
                
                if response.status_code == 200:
                    return f"https://new27.gdtot.dad/file/{file_name}"
                else:
                    return None
                    
    except Exception as e:
        logger.error(f"Alt upload error: {e}")
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
        "Just send me any file and I'll generate a shareable link for you.\n\n"
        "**Max Size:** 4GB\n"
        "**Supported:** Documents, Videos, Audio",
        quote=True
    )

@app.on_message(filters.command("test"))
async def test_handler(_, message: Message):
    """Test command to check API connectivity"""
    await message.reply_text("🔧 **Testing API Connection...**", quote=True)
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get("https://new27.gdtot.dad/", timeout=10)
            status = "✅ Online" if response.status_code == 200 else "❌ Offline"
            
            await message.reply_text(
                f"**API Status:** {status}\n"
                f"**Response Code:** {response.status_code}\n"
                f"**API Key Configured:** {'✅ Yes' if API_KEY else '❌ No'}",
                quote=True
            )
    except Exception as e:
        await message.reply_text(f"**Connection Test Failed:** {str(e)}", quote=True)

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
            actual_size = os.path.getsize(file_path)
            await status_message.edit_text(
                f"✅ **Download Complete!**\n"
                f"📄 **File:** `{file_name}`\n"
                f"📦 **Size:** {humanbytes(actual_size)}\n"
                f"☁️ **Starting upload...**"
            )
            
            # Try main upload method
            upload_link = await upload_to_gdtot(file_path, status_message)
            
            # If main method fails, try alternative
            if not upload_link:
                await status_message.edit_text("🔄 **Trying alternative upload method...**")
                upload_link = await test_upload_method(file_path, status_message)

            if upload_link:
                await status_message.edit_text(
                    f"**✅ Upload Successful!**\n\n"
                    f"📄 **File:** `{file_name}`\n"
                    f"📦 **Size:** {humanbytes(actual_size)}\n"
                    f"🔗 **Download Link:** {upload_link}\n\n"
                    f"💡 *Share this link with others*",
                    disable_web_page_preview=False
                )
            else:
                await status_message.edit_text(
                    "❌ **Upload Failed.**\n\n"
                    "**Possible reasons:**\n"
                    "• API service temporary unavailable\n"
                    "• Invalid API key\n"
                    "• File type not supported\n"
                    "• Network issues\n\n"
                    "Please try again later or check your API configuration."
                )

    except Exception as e:
        await status_message.edit_text(f"**❌ Error:** {str(e)}")
        logger.error(f"Error processing file: {e}")

    finally:
        # Clean up downloaded files
        if os.path.exists(download_dir):
            try:
                shutil.rmtree(download_dir)
                logger.info(f"Cleaned up directory: {download_dir}")
            except Exception as cleanup_error:
                logger.error(f"Cleanup error: {cleanup_error}")

# ----------------- #
# --- RUN BOT --- #
# ----------------- #
if __name__ == "__main__":
    print("🚀 GDTOT Bot is starting...")
    print(f"✅ API Key: {'Configured' if API_KEY else 'NOT CONFIGURED'}")
    print("🤖 Bot is ready to receive files")
    app.run()
