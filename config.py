import os
from dotenv import load_dotenv

load_dotenv()

# Bot Configuration
class Config:
    # Telegram Bot Token
    BOT_TOKEN = os.environ.get('BOT_TOKEN')
    
    # GPLinks Configuration
    DOMAIN_NAME = 'mraprguilds.site'  # GPLinks API domain
    API_KEY = os.environ.get('ADLINKFLY_TOKEN')  # GPLinks API key
    
    # Bot Messages
    START_MESSAGE = os.environ.get('START') or 'Welcome to GPLinks Shortener Bot!\\n\\nSend me a link to shorten it.'
    HELP_MESSAGE = os.environ.get('HELP') or 'Help Guide:\\n\\n- Just send a link to shorten\\n- Use /alias for custom alias\\n- Use /help for more info'
    
    # Rate Limiting
    MAX_REQUESTS_PER_MINUTE = 15
    TIME_WINDOW = 60  # seconds
    
    # API Settings
    API_TIMEOUT = 10  # seconds

# Process messages
START = Config.START_MESSAGE.replace("\\n", "\n")
HELP = Config.HELP_MESSAGE.replace("\\n", "\n")
