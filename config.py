import os
from dotenv import load_dotenv

load_dotenv()

# Bot Configuration
class Config:
    # Telegram Bot Token
    BOT_TOKEN = os.environ.get('BOT_TOKEN')
    
    # AdLinkFly Configuration - Hardcoded domain
    DOMAIN_NAME = 'api.gplinks.com'  # Hardcoded domain
    ADLINKFLY_TOKEN = os.environ.get('ADLINKFLY_TOKEN') 
    
    # Bot Messages
    START_MESSAGE = os.environ.get('START') or 'Welcome to URL Shortener Bot!\\n\\nSend me a link to shorten it.'
    HELP_MESSAGE = os.environ.get('HELP') or 'Help Guide:\\n\\n- Just send a link to shorten without ads\\n- Use /ads for links with ads\\n- Use /alias for custom alias without ads\\n- Use /alias_ads for custom alias with ads'
    
    # Rate Limiting
    MAX_REQUESTS_PER_MINUTE = 15
    TIME_WINDOW = 60  # seconds
    
    # API Settings
    API_TIMEOUT = 10  # seconds

# Process messages
START = Config.START_MESSAGE.replace("\\n", "\n")
HELP = Config.HELP_MESSAGE.replace("\\n", "\n")
