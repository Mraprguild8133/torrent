import os
import time
import math
import asyncio
import httpx
import shutil
import logging
import re
import json
from pyrogram import Client, filters
from pyrogram.types import Message
from config import *

# Setup logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
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

# --------------------------------- #
# --- GDTOT UPLOAD HANDLER --- #
# --------------------------------- #

async def upload_gdrive_to_gdtot(gdrive_url: str, message: Message) -> str:
    """
    Uploads Google Drive link to GDTOT and returns the GDTOT link.
    """
    await message.edit_text("🔗 **Processing Google Drive Link...**")
    
    try:
        # Prepare the request data EXACTLY as in the PHP example
        data = {
            "email": GDTOT_EMAIL,
            "api_token": API_KEY,
            "url": gdrive_url
        }
        
        logger.info(f"Attempting to upload GDrive link: {gdrive_url}")
        logger.info(f"Using email: {GDTOT_EMAIL}")
        logger.info(f"Using API token: {API_KEY[:8]}...")  # Log only first 8 chars for security
        
        headers = {
            'Content-Type': 'application/json',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'application/json'
        }
        
        async with httpx.AsyncClient(timeout=60.0) as client:
            logger.info(f"Sending POST request to: {GDTOT_API_URL}")
            logger.info(f"Request data: {json.dumps(data, indent=2)}")
            
            response = await client.post(
                GDTOT_API_URL,
                json=data,
                headers=headers
            )
            
            logger.info(f"Response Status Code: {response.status_code}")
            logger.info(f"Response Headers: {dict(response.headers)}")
            logger.info(f"Full Response Text: {response.text}")
            
            # Try to parse JSON response
            try:
                response_data = response.json()
                logger.info(f"Parsed JSON Response: {json.dumps(response_data, indent=2)}")
            except Exception as json_error:
                logger.error(f"JSON Parse Error: {json_error}")
                logger.error(f"Raw Response: {response.text}")
                await message.edit_text(f"**API Response Error:** Could not parse response as JSON")
                return None
            
            # Check for different success scenarios
            if response.status_code == 200:
                # Check various possible success indicators
                if response_data.get("status") == "success":
                    gdtot_link = (response_data.get("gdtot_link") or 
                                 response_data.get("url") or 
                                 response_data.get("download_url") or
                                 response_data.get("link"))
                    if gdtot_link:
                        logger.info(f"Success! GDTOT Link: {gdtot_link}")
                        return gdtot_link
                
                # Check for error messages
                error_msg = (response_data.get("message") or 
                           response_data.get("error") or 
                           response_data.get("msg") or
                           "Unknown error occurred")
                
                await message.edit_text(f"**API Error:** {error_msg}")
                return None
                
            else:
                error_msg = f"HTTP {response.status_code}"
                if response_data.get("message"):
                    error_msg += f" - {response_data.get('message')}"
                elif response_data.get("error"):
                    error_msg += f" - {response_data.get('error')}"
                
                await message.edit_text(f"**Server Error:** {error_msg}")
                return None
                
    except httpx.HTTPStatusError as e:
        logger.error(f"HTTPStatusError: {e}")
        logger.error(f"Response: {e.response.text if e.response else 'No response'}")
        
        error_msg = f"**HTTP Error {e.response.status_code if e.response else 'Unknown'}**"
        await message.edit_text(error_msg)
        return None
        
    except httpx.RequestError as e:
        logger.error(f"RequestError: {e}")
        await message.edit_text("**Network Error:** Cannot connect to GDTOT service. Please check your internet connection.")
        return None
        
    except Exception as e:
        logger.error(f"Unexpected Error: {e}", exc_info=True)
        await message.edit_text(f"**Unexpected Error:** {str(e)}")
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
        "**Commands:**\n"
        "/gdrive <link> - Convert Google Drive link\n"
        "/test - Check API status\n"
        "/debug - Show configuration\n"
        "/start - Show this help",
        quote=True
    )

@app.on_message(filters.command("debug"))
async def debug_handler(_, message: Message):
    """Show debug information"""
    debug_info = f"""
**🔧 Debug Information:**

**API Configuration:**
• Domain: `{GDTOT_DOMAIN}`
• API URL: `{GDTOT_API_URL}`
• Email: `{GDTOT_EMAIL}`
• API Key: `{API_KEY[:8]}...` (first 8 chars)

**Bot Status:**
• API ID: `{API_ID}`
• API Hash: `{API_HASH[:8]}...`
• Bot Token: `{BOT_TOKEN[:8]}...`

**To test your configuration:**
1. Use `/test` to check API connectivity
2. Use `/gdrive <link>` with a test Google Drive link
    """
    
    await message.reply_text(debug_info, quote=True)

@app.on_message(filters.command("test"))
async def test_handler(_, message: Message):
    """Test command to check API connectivity with detailed logging"""
    test_message = await message.reply_text("🔧 **Testing GDTOT API Connection...**", quote=True)
    
    try:
        # Test with a simple request to check if domain is accessible
        async with httpx.AsyncClient(timeout=30.0) as client:
            # First test if domain is reachable
            domain_test = await client.get(GDTOT_DOMAIN, follow_redirects=True)
            domain_status = "✅ Reachable" if domain_test.status_code == 200 else "❌ Unreachable"
            
            # Now test the API with minimal data
            test_data = {
                "email": GDTOT_EMAIL,
                "api_token": API_KEY,
                "url": "https://drive.google.com/file/d/test123/view"  # dummy link for testing
            }
            
            api_response = await client.post(
                GDTOT_API_URL,
                json=test_data,
                headers={'Content-Type': 'application/json'},
                timeout=30.0
            )
            
            api_status = "✅ Responding" if api_response.status_code in [200, 201, 400, 422] else "❌ Not Responding"
            
            result_text = f"""
**🧪 API Test Results:**

**Domain Test:**
• Status: {domain_status}
• Code: {domain_test.status_code}

**API Test:**
• Status: {api_status}
• Code: {api_response.status_code}

**Configuration:**
• Email: {'✅ Set' if GDTOT_EMAIL else '❌ Missing'}
• API Key: {'✅ Set' if API_KEY else '❌ Missing'}

**Next Steps:**
If domain is reachable but API fails, check:
1. Your API key is valid
2. Your email is registered with GDTOT
3. The API endpoint is correct
            """
            
            await test_message.edit_text(result_text)
            
    except Exception as e:
        await test_message.edit_text(f"""
**❌ Test Failed:**

**Error:** {str(e)}

**Possible Issues:**
1. Domain {GDTOT_DOMAIN} is not accessible
2. Network connectivity problem
3. SSL certificate issue
4. Server is down

Please check the domain manually in your browser.
        """)

@app.on_message(filters.command("gdrive"))
async def gdrive_handler(_, message: Message):
    """Handles Google Drive link conversion."""
    if len(message.command) < 2:
        await message.reply_text(
            "**Usage:** `/gdrive <google_drive_link>`\n\n"
            "**Example:**\n"
            "`/gdrive https://drive.google.com/file/d/1ABC123xyz/view`\n\n"
            "**Note:** The link must be a shareable Google Drive file link.",
            quote=True
        )
        return
    
    gdrive_url = message.command[1]
    
    if not is_google_drive_link(gdrive_url):
        await message.reply_text(
            "❌ **Invalid Google Drive Link**\n\n"
            "Please provide a valid Google Drive file link.\n"
            "**Accepted formats:**\n"
            "• `https://drive.google.com/file/d/FILE_ID/view`\n"
            "• `https://drive.google.com/open?id=FILE_ID`\n"
            "• `https://docs.google.com/uc?export=download&id=FILE_ID`",
            quote=True
        )
        return
    
    status_message = await message.reply_text(
        f"🔗 **Processing Google Drive Link...**\n\n"
        f"**URL:** `{gdrive_url}`",
        quote=True
    )
    
    # Upload to GDTOT
    gdtot_link = await upload_gdrive_to_gdtot(gdrive_url, status_message)
    
    if gdtot_link:
        await status_message.edit_text(
            f"**✅ Conversion Successful!**\n\n"
            f"**Original Link:**\n`{gdrive_url}`\n\n"
            f"**GDTOT Download Link:**\n{gdtot_link}\n\n"
            f"💡 *Share this GDTOT link for downloads*",
            disable_web_page_preview=True
        )
    else:
        await status_message.edit_text(
            "❌ **Conversion Failed**\n\n"
            "**Troubleshooting Steps:**\n"
            "1. Use `/test` to check API status\n"
            "2. Use `/debug` to verify configuration\n"
            "3. Ensure the Google Drive link is public\n"
            "4. Check if your API key is valid\n"
            "5. Try again in a few minutes"
        )

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
                "Use `/test` to diagnose the issue or try again later."
            )

# ----------------- #
# --- RUN BOT --- #
# ----------------- #
if __name__ == "__main__":
    print("🚀 GDTOT Bot is starting...")
    print(f"📧 Email: {GDTOT_EMAIL}")
    print(f"🔑 API Key: {API_KEY[:8]}...")
    print(f"🌐 Domain: {GDTOT_DOMAIN}")
    print("🤖 Bot is ready to convert Google Drive links")
    app.run()
