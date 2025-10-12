import os
import base64
import logging
from urllib.parse import unquote
from flask import Flask, request, render_template, redirect, url_for, send_file, Response
from config import config

# Configure logging
logging.basicConfig(level=getattr(logging, config.LOG_LEVEL))
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Supported formats for web player
SUPPORTED_VIDEO_FORMATS = {'.mp4', '.mkv', '.avi', '.mov', '.wmv', '.flv', '.webm', '.m4v', '.3gp', '.mpeg', '.mpg', '.ts'}
SUPPORTED_AUDIO_FORMATS = {'.mp3', '.m4a', '.flac', '.wav', '.aac', '.ogg', '.wma'}

def get_file_extension(filename: str) -> str:
    """Extract file extension in lowercase."""
    return os.path.splitext(filename)[1].lower()

def is_video_file(filename: str) -> bool:
    """Check if file is a supported video format."""
    return get_file_extension(filename) in SUPPORTED_VIDEO_FORMATS

def is_audio_file(filename: str) -> bool:
    """Check if file is a supported audio format."""
    return get_file_extension(filename) in SUPPORTED_AUDIO_FORMATS

def decode_url(encoded_url: str) -> str:
    """Decode base64 encoded URL."""
    try:
        # Add padding if needed
        padding = 4 - (len(encoded_url) % 4)
        if padding != 4:
            encoded_url += '=' * padding
        
        decoded_bytes = base64.urlsafe_b64decode(encoded_url)
        return decoded_bytes.decode('utf-8')
    except Exception as e:
        logger.error(f"Error decoding URL: {e}")
        return None

@app.route('/')
def index():
    """Main page with information about the web player."""
    return render_template('index.html', render_url=config.RENDER_URL)

@app.route('/player/video/<encoded_url>')
def video_player(encoded_url):
    """Video player page."""
    try:
        presigned_url = decode_url(encoded_url)
        if not presigned_url:
            return "Invalid URL", 400
        
        # Extract filename from URL for display
        filename = "Video File"
        if '/' in presigned_url:
            filename = unquote(presigned_url.split('/')[-1].split('?')[0])
        
        return render_template('video_player.html', 
                             video_url=presigned_url, 
                             filename=filename,
                             render_url=config.RENDER_URL)
    except Exception as e:
        logger.error(f"Video player error: {e}")
        return "Error loading video player", 500

@app.route('/player/audio/<encoded_url>')
def audio_player(encoded_url):
    """Audio player page."""
    try:
        presigned_url = decode_url(encoded_url)
        if not presigned_url:
            return "Invalid URL", 400
        
        # Extract filename from URL for display
        filename = "Audio File"
        if '/' in presigned_url:
            filename = unquote(presigned_url.split('/')[-1].split('?')[0])
        
        return render_template('audio_player.html', 
                             audio_url=presigned_url, 
                             filename=filename,
                             render_url=config.RENDER_URL)
    except Exception as e:
        logger.error(f"Audio player error: {e}")
        return "Error loading audio player", 500

@app.route('/health')
def health_check():
    """Health check endpoint."""
    return {'status': 'healthy', 'service': 'wasabi-web-player'}

# Error handlers
@app.errorhandler(404)
def not_found(error):
    return render_template('error.html', error_code=404, error_message="Page not found"), 404

@app.errorhandler(500)
def internal_error(error):
    return render_template('error.html', error_code=500, error_message="Internal server error"), 500

if __name__ == '__main__':
    logger.info(f"🚀 Starting Wasabi Web Player on port {config.WEB_PORT}")
    app.run(host='0.0.0.0', port=config.WEB_PORT, debug=config.LOG_LEVEL == 'DEBUG')
