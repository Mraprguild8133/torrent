# ------------------ config.py ------------------
# Configuration file for the Telegram Torrent Analyzer Bot

# Your Telegram Bot Token from @BotFather
TELEGRAM_BOT_TOKEN = "TELEGRAM_BOT_TOKEN"

# Optional: Additional configuration options
BOT_CONFIG = {
    "admin_ids": [],  # Add admin user IDs if needed
    "max_file_size": 10 * 1024 * 1024,  # 10MB in bytes
    "allowed_trackers": [
        "tracker.openbittorrent.com",
        "tracker.leechers-paradise.org", 
        "open.nyaatorrents.info",
        "exodus.desync.com",
        "tracker.publicbt.com"
    ]
}
