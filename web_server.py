from flask import Flask, render_template, request, redirect, url_for
import base64
import logging
import threading
from config import config

# Setup logging
logging.basicConfig(level=config.LOG_LEVEL)
logger = logging.getLogger(__name__)

app = Flask(__name__)

@app.route('/')
def index():
    """Main page with information about the bot"""
    return render_template('index.html', 
                         bot_url=f"https://t.me/{(config.BOT_TOKEN.split(':')[0])}",
                         port=config.WEB_PORT)

@app.route('/player/video/<encoded_url>')
def video_player(encoded_url):
    """Video player page for streaming videos"""
    try:
        # Decode the base64 URL
        padding = 4 - (len(encoded_url) % 4)
        if padding != 4:
            encoded_url += '=' * padding
        
        video_url = base64.urlsafe_b64decode(encoded_url).decode()
        
        # Get filename from URL for display
        filename = video_url.split('/')[-1].split('?')[0]
        
        return render_template('player.html', 
                             video_url=video_url,
                             filename=filename,
                             bot_url=f"https://t.me/{(config.BOT_TOKEN.split(':')[0])}")
    
    except Exception as e:
        logger.error(f"Error decoding video URL: {e}")
        return render_template('error.html', 
                             error="Invalid video URL",
                             bot_url=f"https://t.me/{(config.BOT_TOKEN.split(':')[0])}")

@app.route('/player/audio/<encoded_url>')
def audio_player(encoded_url):
    """Audio player page for streaming audio"""
    try:
        # Decode the base64 URL
        padding = 4 - (len(encoded_url) % 4)
        if padding != 4:
            encoded_url += '=' * padding
        
        audio_url = base64.urlsafe_b64decode(encoded_url).decode()
        filename = audio_url.split('/')[-1].split('?')[0]
        
        return render_template('audio_player.html', 
                             audio_url=audio_url,
                             filename=filename,
                             bot_url=f"https://t.me/{(config.BOT_TOKEN.split(':')[0])}")
    
    except Exception as e:
        logger.error(f"Error decoding audio URL: {e}")
        return render_template('error.html', 
                             error="Invalid audio URL",
                             bot_url=f"https://t.me/{(config.BOT_TOKEN.split(':')[0])}")

@app.route('/health')
def health_check():
    """Health check endpoint for monitoring"""
    return {'status': 'healthy', 'service': 'wasabi-storage-player'}

@app.errorhandler(404)
def not_found(error):
    return render_template('error.html', 
                         error="Page not found",
                         bot_url=f"https://t.me/{(config.BOT_TOKEN.split(':')[0])}"), 404

@app.errorhandler(500)
def internal_error(error):
    return render_template('error.html', 
                         error="Internal server error",
                         bot_url=f"https://t.me/{(config.BOT_TOKEN.split(':')[0])}"), 500

def run_web_server():
    """Run the Flask web server"""
    logger.info(f"🚀 Starting web server on {config.WEB_HOST}:{config.WEB_PORT}")
    app.run(
        host=config.WEB_HOST,
        port=config.WEB_PORT,
        debug=False,
        threaded=True
    )

if __name__ == '__main__':
    run_web_server()
