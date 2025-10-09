from webserver import keep_alive
from urllib.parse import quote
import json
import re
import requests
import telebot
import time
import sqlite3
from collections import defaultdict
from threading import Lock
from datetime import datetime
from config import Config, START, HELP

# This script is designed to work with a adlinkfly php link shortener website!
# For more info read the ' README.md ' file...!!
# This script is developed by @neo_subhamoy
# Website: https://neosubhamoy.com

# Initialize bot
bot = telebot.TeleBot(Config.BOT_TOKEN)
user_data = {}
user_data_lock = Lock()

# Rate limiting setup
class RateLimiter:
    def __init__(self, max_requests=Config.MAX_REQUESTS_PER_MINUTE, time_window=Config.TIME_WINDOW):
        self.user_requests = defaultdict(list)
        self.max_requests = max_requests
        self.time_window = time_window
        self.lock = Lock()
    
    def is_allowed(self, user_id):
        now = time.time()
        with self.lock:
            user_requests = self.user_requests[user_id]
            
            # Remove old requests
            user_requests[:] = [req_time for req_time in user_requests 
                              if now - req_time < self.time_window]
            
            if len(user_requests) >= self.max_requests:
                return False
            
            user_requests.append(now)
            return True
    
    def get_remaining_requests(self, user_id):
        now = time.time()
        with self.lock:
            user_requests = self.user_requests[user_id]
            user_requests[:] = [req_time for req_time in user_requests 
                              if now - req_time < self.time_window]
            return self.max_requests - len(user_requests)

rate_limiter = RateLimiter()

# Database setup for analytics (optional)
def init_db():
    conn = sqlite3.connect('url_shortener.db')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS shortened_urls (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            original_url TEXT,
            shortened_url TEXT,
            alias TEXT,
            has_ads BOOLEAN,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

init_db()

def log_shortened_url(user_id, original_url, shortened_url, alias=None, has_ads=False):
    try:
        conn = sqlite3.connect('url_shortener.db')
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO shortened_urls (user_id, original_url, shortened_url, alias, has_ads)
            VALUES (?, ?, ?, ?, ?)
        ''', (user_id, original_url, shortened_url, alias, has_ads))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Database error: {e}")

# Enhanced error handling
def handle_api_error(response, func_name):
    """Centralized error handling for API calls"""
    if response.status_code != 200:
        print(f'{func_name}: API request failed with status {response.status_code}')
        print(f'Response: {response.text}')
        return False
    
    try:
        data = response.json()
        if data.get('status') != 'success':
            print(f'{func_name}: API returned error: {data.get("message", "Unknown error")}')
            return False
        return True
    except json.JSONDecodeError:
        print(f'{func_name}: Invalid JSON response')
        return False

# URL validation functions
def is_valid_url(link):
    url_regex = re.compile(
        r'^(?:http|ftp)s?://'
        r'(?:(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+(?:[A-Z]{2,6}\.?|[A-Z0-9-]{2,}\.?)|'
        r'localhost|'
        r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})'
        r'(?::\d+)?'
        r'(?:/?|[/?]\S+)$',
        re.IGNORECASE)
    return url_regex.match(link)

def is_valid_alias(alias):
    alias_regex = re.compile(r'^[a-zA-Z0-9-]{3,20}$')
    return alias_regex.match(alias)

# API functions with improved error handling
def shorten_link_with_alias(link, alias):
    try:
        url = f'https://{Config.DOMAIN_NAME}/api?api={Config.ADLINKFLY_TOKEN}&url={link}&alias={alias}&type=0'
        r = requests.get(url, timeout=Config.API_TIMEOUT)

        if not handle_api_error(r, 'shorten_link_with_alias'):
            return None
            
        response = r.json()
        return response.get('shortenedUrl')
        
    except requests.exceptions.Timeout:
        print('Request timeout in shorten_link_with_alias')
        return None
    except Exception as e:
        print(f'An error occurred in shorten_link_with_alias: {str(e)}')
        return None

def shorten_link_withads_alias(link, alias):
    try:
        url = f'https://{Config.DOMAIN_NAME}/api?api={Config.ADLINKFLY_TOKEN}&url={link}&alias={alias}'
        r = requests.get(url, timeout=Config.API_TIMEOUT)

        if not handle_api_error(r, 'shorten_link_withads_alias'):
            return None
            
        response = r.json()
        return response.get('shortenedUrl')
        
    except requests.exceptions.Timeout:
        print('Request timeout in shorten_link_withads_alias')
        return None
    except Exception as e:
        print(f'An error occurred in shorten_link_withads_alias: {str(e)}')
        return None

def shorten_link(link):
    try:
        url = f'https://{Config.DOMAIN_NAME}/api?api={Config.ADLINKFLY_TOKEN}&url={link}&type=0'
        r = requests.get(url, timeout=Config.API_TIMEOUT)

        if not handle_api_error(r, 'shorten_link'):
            return None
            
        response = r.json()
        return response.get('shortenedUrl')
        
    except requests.exceptions.Timeout:
        print('Request timeout in shorten_link')
        return None
    except Exception as e:
        print(f'An error occurred in shorten_link: {str(e)}')
        return None

def shorten_link_withads(link):
    try:
        url = f'https://{Config.DOMAIN_NAME}/api?api={Config.ADLINKFLY_TOKEN}&url={link}'
        r = requests.get(url, timeout=Config.API_TIMEOUT)

        if not handle_api_error(r, 'shorten_link_withads'):
            return None
            
        response = r.json()
        return response.get('shortenedUrl')
        
    except requests.exceptions.Timeout:
        print('Request timeout in shorten_link_withads')
        return None
    except Exception as e:
        print(f'An error occurred in shorten_link_withads: {str(e)}')
        return None

# Rate limit check decorator
def check_rate_limit(func):
    def wrapper(message, *args, **kwargs):
        user_id = message.from_user.id
        
        if not rate_limiter.is_allowed(user_id):
            remaining = rate_limiter.get_remaining_requests(user_id)
            bot.reply_to(
                message,
                f"🚫 Rate limit exceeded! Please wait for {Config.TIME_WINDOW} seconds.\n"
                f"Remaining requests after cooldown: {remaining}"
            )
            return
        
        return func(message, *args, **kwargs)
    return wrapper

# Bot command handlers
@bot.message_handler(commands=['start'])
@check_rate_limit
def start(message):
    bot.reply_to(
        message,
        START,
        parse_mode='Markdown',
        disable_web_page_preview=True
    )

@bot.message_handler(commands=['help'])
@check_rate_limit
def help_command(message):
    bot.reply_to(
        message,
        HELP,
        parse_mode='Markdown',
        disable_web_page_preview=True
    )

@bot.message_handler(commands=['stats'])
@check_rate_limit
def stats_command(message):
    try:
        conn = sqlite3.connect('url_shortener.db')
        cursor = conn.cursor()
        
        # Get user's total shortened URLs
        cursor.execute(
            'SELECT COUNT(*) FROM shortened_urls WHERE user_id = ?',
            (message.from_user.id,)
        )
        user_count = cursor.fetchone()[0]
        
        # Get total URLs shortened by bot
        cursor.execute('SELECT COUNT(*) FROM shortened_urls')
        total_count = cursor.fetchone()[0]
        
        conn.close()
        
        stats_message = (
            f"📊 Your Shortening Stats:\n"
            f"• Your shortened URLs: {user_count}\n"
            f"• Total bot shortened URLs: {total_count}\n"
            f"• Rate limit: {rate_limiter.get_remaining_requests(message.from_user.id)}/{Config.MAX_REQUESTS_PER_MINUTE} remaining"
        )
        
        bot.reply_to(message, stats_message)
        
    except Exception as e:
        bot.reply_to(message, "❌ Could not retrieve stats at this time.")

@bot.message_handler(commands=['ads'])
@check_rate_limit
def handle_ads_command(message):
    bot.send_message(
        chat_id=message.chat.id,
        text="Please send the link to shorten (with ads):"
    )
    bot.register_next_step_handler(message, handle_link_with_ads)

def handle_link_with_ads(message):
    if not rate_limiter.is_allowed(message.from_user.id):
        bot.reply_to(message, "🚫 Rate limit exceeded. Please wait a minute.")
        return
        
    if is_valid_url(message.text):
        bot.send_message(message.chat.id, "⏳ Shortening! Please wait...")
        link = quote(message.text)
        shortened_link = shorten_link_withads(link)
        
        if shortened_link:
            log_shortened_url(
                message.from_user.id,
                message.text,
                shortened_link,
                has_ads=True
            )
            bot.reply_to(
                message,
                f"🔗 Link Shortened (with ads)!\n{shortened_link}",
                parse_mode='Markdown',
                disable_web_page_preview=True
            )
        else:
            bot.reply_to(message, '❌ Failed to shorten the link! Please try again...')
    else:
        bot.send_message(
            message.chat.id,
            "❌ Invalid URL!\nPlease reuse the command /ads to try again with a valid link..."
        )

@bot.message_handler(commands=['alias'])
@check_rate_limit
def handle_alias_command(message):
    bot.send_message(
        chat_id=message.chat.id,
        text="Please send the link to shorten with custom alias:"
    )
    bot.register_next_step_handler(message, handle_alias_url)

def handle_alias_url(message):
    if not rate_limiter.is_allowed(message.from_user.id):
        bot.reply_to(message, "🚫 Rate limit exceeded. Please wait a minute.")
        return
        
    if is_valid_url(message.text):
        with user_data_lock:
            user_data[message.chat.id] = {'url': message.text}
        bot.send_message(
            message.chat.id,
            "Now, please send your desired alias (3-20 characters, only letters, numbers, and hyphens allowed):"
        )
        bot.register_next_step_handler(message, handle_alias_creation)
    else:
        bot.send_message(
            message.chat.id,
            "❌ Invalid URL!\nPlease use /alias command again with a valid link..."
        )

def handle_alias_creation(message):
    if not rate_limiter.is_allowed(message.from_user.id):
        bot.reply_to(message, "🚫 Rate limit exceeded. Please wait a minute.")
        return
        
    if not is_valid_alias(message.text):
        bot.send_message(
            message.chat.id,
            "❌ Invalid alias! Only letters, numbers, and hyphens are allowed (3-20 characters).\nPlease use /alias command again..."
        )
        return

    chat_id = message.chat.id
    with user_data_lock:
        if chat_id not in user_data:
            bot.send_message(
                chat_id,
                "❌ Something went wrong. Please start over with /alias command."
            )
            return

        long_url = user_data[chat_id]['url']
        alias = message.text
    
    bot.send_message(message.chat.id, "⏳ Shortening! Please wait...")
    shortened_link = shorten_link_with_alias(quote(long_url), alias)
    
    if shortened_link:
        log_shortened_url(
            message.from_user.id,
            long_url,
            shortened_link,
            alias=alias,
            has_ads=False
        )
        bot.reply_to(
            message,
            f"🔗 Link Shortened With Custom Alias!\n{shortened_link}",
            parse_mode='Markdown',
            disable_web_page_preview=True
        )
    else:
        bot.reply_to(
            message,
            '❌ Failed to create custom short link! The alias might be taken or there was an error. Please try again with a different alias.'
        )
    
    # Clean up user data
    with user_data_lock:
        if chat_id in user_data:
            del user_data[chat_id]

@bot.message_handler(commands=['alias_ads'])
@check_rate_limit
def handle_alias_ads_command(message):
    bot.send_message(
        chat_id=message.chat.id,
        text="Please send the link to shorten with custom alias (with ads):"
    )
    bot.register_next_step_handler(message, handle_alias_ads_url)

def handle_alias_ads_url(message):
    if not rate_limiter.is_allowed(message.from_user.id):
        bot.reply_to(message, "🚫 Rate limit exceeded. Please wait a minute.")
        return
        
    if is_valid_url(message.text):
        with user_data_lock:
            user_data[message.chat.id] = {'url': message.text}
        bot.send_message(
            message.chat.id,
            "Now, please send your desired alias (3-20 characters, only letters, numbers, and hyphens allowed):"
        )
        bot.register_next_step_handler(message, handle_alias_ads_creation)
    else:
        bot.send_message(
            message.chat.id,
            "❌ Invalid URL!\nPlease use /alias_ads command again with a valid link..."
        )

def handle_alias_ads_creation(message):
    if not rate_limiter.is_allowed(message.from_user.id):
        bot.reply_to(message, "🚫 Rate limit exceeded. Please wait a minute.")
        return
        
    if not is_valid_alias(message.text):
        bot.send_message(
            message.chat.id,
            "❌ Invalid alias! Only letters, numbers, and hyphens are allowed (3-20 characters).\nPlease use /alias_ads command again..."
        )
        return

    chat_id = message.chat.id
    with user_data_lock:
        if chat_id not in user_data:
            bot.send_message(
                chat_id,
                "❌ Something went wrong. Please start over with /alias_ads command."
            )
            return

        long_url = user_data[chat_id]['url']
        alias = message.text
    
    bot.send_message(message.chat.id, "⏳ Shortening! Please wait...")
    shortened_link = shorten_link_withads_alias(quote(long_url), alias)
    
    if shortened_link:
        log_shortened_url(
            message.from_user.id,
            long_url,
            shortened_link,
            alias=alias,
            has_ads=True
        )
        bot.reply_to(
            message,
            f"🔗 Link Shortened With Custom Alias (with ads)!\n{shortened_link}",
            parse_mode='Markdown',
            disable_web_page_preview=True
        )
    else:
        bot.reply_to(
            message,
            '❌ Failed to create custom short link! The alias might be taken or there was an error. Please try again with a different alias.'
        )
    
    # Clean up user data
    with user_data_lock:
        if chat_id in user_data:
            del user_data[chat_id]

@bot.message_handler(content_types=['text'])
@check_rate_limit
def handle_text(message):
    if is_valid_url(message.text):
        bot.send_message(message.chat.id, "⏳ Shortening! Please wait...")
        link = quote(message.text)
        shortened_link = shorten_link(link)
        
        if shortened_link:
            log_shortened_url(
                message.from_user.id,
                message.text,
                shortened_link,
                has_ads=False
            )
            bot.reply_to(
                message,
                shortened_link,
                parse_mode='Markdown',
                disable_web_page_preview=True
            )
        else:
            bot.reply_to(message, '❌ Failed to shorten the link! Please try again...')
    else:
        bot.send_message(
            message.chat.id,
            "❌ Invalid URL!\nPlease send a valid link...!\n\nUse /help for more information."
        )

# Error handler
@bot.message_handler(func=lambda message: True)
def handle_other_messages(message):
    bot.reply_to(
        message,
        "🤖 I only understand URLs and commands. Use /help to see what I can do!"
    )

if __name__ == "__main__":
    print("🤖 URL Shortener Bot is starting...")
    keep_alive()
    
    try:
        bot.polling(none_stop=True)
    except Exception as e:
        print(f"Bot crashed with error: {e}")
        print("Restarting bot...")
        time.sleep(5)
