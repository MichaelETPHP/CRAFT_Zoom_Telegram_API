import json
import requests
import os
from google.oauth2 import service_account
from googleapiclient.discovery import build
import base64
from datetime import datetime, timedelta
import pytz
from flask import Flask, request, render_template, redirect, url_for, session
import webbrowser
import threading
import time
import logging  # Import the logging module
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Load credentials from environment variable
credentials_json = os.getenv('GOOGLE_CREDENTIALS_JSON')
credentials_info = json.loads(credentials_json)

credentials = service_account.Credentials.from_service_account_info(
    credentials_info, scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"]
)

service = build('sheets', 'v4', credentials=credentials)

# Configure logging
# logging.basicConfig(level=logging.DEBUG)

# Path to the credentials JSON file
CREDENTIALS_FILE = 'path/to/credentials.json'

# ID of the Google Sheet
SHEET_ID = '1_u-RdZ7cVssXJN-neafxCj04Pi_Xww_THx1AQnWk_Mo'

# Range of the sheet to read from (e.g., 'Sheet1!A2:B')
SHEET_RANGE = 'Sheet1!A2:B'

# Flask App for local callback
app = Flask(__name__)

app.secret_key = '!@HELLO@123#J'  # Replace with a strong secret key
application = app

# Telegram Bot API token
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '7859561595:AAEoCY3Dt5_eaqseHCwEWr54XEK5nwjJTfg')
TELEGRAM_GROUP_CHAT_ID = os.getenv('TELEGRAM_GROUP_CHAT_ID', '-1002339790106')

# Zoom API credentials
ZOOM_CLIENT_ID = os.getenv('ZOOM_CLIENT_ID', '0Uxt5PNBQQOle9SziFj35Q')
ZOOM_CLIENT_SECRET = os.getenv('ZOOM_CLIENT_SECRET', 'mvbN90eulvZ3kDXw1eIW1LhxS7Grh6z6')
ZOOM_REDIRECT_URI = os.getenv('ZOOM_REDIRECT_URI', 'http://localhost:5000/callback')

# Global variable to store access token
ZOOM_ACCESS_TOKEN = None

def get_registered_students():
    try:
        sheet = service.spreadsheets()
        result = sheet.values().get(spreadsheetId=SHEET_ID, range=SHEET_RANGE).execute()
        values = result.get('values', [])
        registered_students = [{'email': row[0], 'name': row[1]} for row in values if len(row) >= 2]
        return registered_students
    except Exception as e:
        print(f"Error fetching registered students: {e}")
        return []

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/start_meeting', methods=['POST'])
def start_meeting():
    topic = request.form.get('topic')
    if not topic:
        return "Meeting topic is required.", 400
    session['meeting_topic'] = topic
    authorization_url = (
        f"https://zoom.us/oauth/authorize?response_type=code&client_id={ZOOM_CLIENT_ID}&redirect_uri={ZOOM_REDIRECT_URI}"
    )
    return redirect(authorization_url)

@app.route('/callback')
def callback():
    """Handle Zoom OAuth callback."""
    global ZOOM_ACCESS_TOKEN

    try:
        authorization_code = request.args.get('code')
        if not authorization_code:
            return "Error: Authorization code not found.", 400

        token_url = 'https://zoom.us/oauth/token'
        auth_header = base64.b64encode(f"{ZOOM_CLIENT_ID}:{ZOOM_CLIENT_SECRET}".encode()).decode()
        payload = {
            'grant_type': 'authorization_code',
            'code': authorization_code,
            'redirect_uri': ZOOM_REDIRECT_URI
        }
        headers = {
            'Authorization': f'Basic {auth_header}',
            'Content-Type': 'application/x-www-form-urlencoded'
        }
        response = requests.post(token_url, data=payload, headers=headers)
        if response.status_code == 200:
            ZOOM_ACCESS_TOKEN = response.json().get('access_token')
            meeting_topic = session.get('meeting_topic', 'Scheduled Meeting')
            meeting_details = create_zoom_meeting(meeting_topic)
            if meeting_details:
                webbrowser.open(meeting_details.get('start_url'))
                send_meeting_details_to_telegram(meeting_details)
                return redirect(url_for('thank_you', zoom_link=meeting_details.get('join_url'), meeting_id=meeting_details.get('id'), meeting_topic=meeting_topic))
            return "Access token successfully obtained, but failed to create meeting.", 200
        else:
            return f"Failed to obtain access token. Status code: {response.status_code}. Response: {response.json()}", 400
    except Exception as e:
        logging.error(f"Error during callback: {e}")
        return "Internal Server Error", 500

@app.route('/thank_you')
def thank_you():
    try:
        zoom_link = request.args.get('zoom_link')
        meeting_id = request.args.get('meeting_id')
        meeting_topic = request.args.get('meeting_topic')
        registered_students = get_registered_students()
        logging.debug(f"Passing registered students to template: {registered_students}")
        return render_template('thank_you.html', zoom_link=zoom_link, meeting_id=meeting_id, meeting_topic=meeting_topic, registered_students=registered_students)
    except Exception as e:
        logging.error(f"Error rendering thank_you page: {e}")
        return "Internal Server Error", 500

def create_zoom_meeting(meeting_topic):
    global ZOOM_ACCESS_TOKEN
    if not ZOOM_ACCESS_TOKEN:
        logging.error("Access token not available. Please complete the OAuth flow first.")
        return None
    tz = pytz.timezone('UTC')
    current_time = datetime.now(tz)
    start_time = current_time + timedelta(minutes=1)
    start_time_str = start_time.strftime('%Y-%m-%dT%H:%M:%SZ')
    meeting_url = 'https://api.zoom.us/v2/users/me/meetings'
    payload = {
        "topic": meeting_topic,
        "type": 2,
        "start_time": start_time_str,
        "duration": 30,
        "timezone": "UTC",
        "settings": {
            "approval_type": 0,  # Automatically approve
            "waiting_room": False,  # Disable waiting room
            "meeting_authentication": True  # Require authentication to join
        }
    }
    headers = {
        'Authorization': f'Bearer {ZOOM_ACCESS_TOKEN}',
        'Content-Type': 'application/json'
    }
    response = requests.post(meeting_url, json=payload, headers=headers)
    if response.status_code == 201:
        meeting_details = response.json()
        logging.debug(f"Meeting created successfully. Meeting ID: {meeting_details.get('id')}")
        return meeting_details
    else:
        logging.error(f"Failed to create meeting. Status code: {response.status_code}")
        logging.error(f"Response: {response.json()}")
        return None

def send_meeting_details_to_telegram(meeting_details):
    try:
        meeting_link = meeting_details.get('join_url')
        meeting_topic = meeting_details.get('topic')
        meeting_id = meeting_details.get('id')
        countdown_seconds = 60  # Time until meeting starts

        # Prepare the message
        message = (
             f"**📢 New Zoom Meeting Created!**\n\n"
             f"**🔐 Meeting ID:** {meeting_id}\n"
             f"**💼 Topic:** {meeting_topic}\n"
             f"**🔗 Join Link:** [Click here to join the meeting]({meeting_link})\n"
             f"**⏳ Starting in:** {countdown_seconds} seconds\n\n"
        )

        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        data = {
            'chat_id': TELEGRAM_GROUP_CHAT_ID,
            'text': message,
            'parse_mode': 'Markdown'
        }
        response = requests.post(url, data=data)
        if response.status_code == 200:
            telegram_message_id = response.json().get('result', {}).get('message_id')
            # Start the countdown update thread
            threading.Thread(
                target=update_countdown_in_telegram,
                args=(telegram_message_id, countdown_seconds, meeting_link, meeting_topic, meeting_id),
                daemon=True
            ).start()
            # Start the deletion thread
            threading.Thread(
                target=delete_telegram_message,
                args=(telegram_message_id, 60),
                daemon=True
            ).start()
            # Redirect to thank_you route with zoom_link, meeting_id, and meeting_topic as query parameters
            return redirect(url_for('thank_you', zoom_link=meeting_link, meeting_id=meeting_id, meeting_topic=meeting_topic))
        else:
            logging.error(f"Failed to send message to Telegram. Status code: {response.status_code}")
            logging.error(f"Response: {response.json()}")
            return "Failed to send meeting details to Telegram.", 400
    except Exception as e:
        logging.error(f"Error sending meeting details to Telegram: {e}")
        return "Internal Server Error", 500

def update_countdown_in_telegram(message_id, countdown_seconds, meeting_link, meeting_topic, meeting_id):
    """Update the countdown timer in the Telegram message."""
    while countdown_seconds > 0:
        message = (
            f"**📢 New Zoom Meeting Created!**\n\n"
            f"**🔐 Meeting ID:** {meeting_id}\n"
            f"**📅 Topic:** {meeting_topic}\n"
            f"**🔗 Join Link:** [Click here to join the meeting]({meeting_link})\n"
            f"**⏳ Starting in:** {countdown_seconds} seconds"
        )
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/editMessageText"
        data = {
            'chat_id': TELEGRAM_GROUP_CHAT_ID,
            'message_id': message_id,
            'text': message,
            'parse_mode': 'Markdown'
        }
        requests.post(url, data=data)
        time.sleep(1)
        countdown_seconds -= 1

def delete_telegram_message(message_id, delay):
    """Delete a Telegram message after a specified delay."""
    time.sleep(delay)
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/deleteMessage"
    data = {'chat_id': TELEGRAM_GROUP_CHAT_ID, 'message_id': message_id}
    response = requests.post(url, data=data)
    if response.status_code == 200:
        logging.debug(f"Message ID {message_id} deleted successfully.")
        notify_telegram_admin()
    else:
        logging.error(f"Failed to delete message ID {message_id}. Status code: {response.status_code}")

def notify_telegram_admin():
    """Notify users to contact admin after the message is deleted, including the current date."""
    current_date = datetime.now().strftime('%Y-%m-%d')  # Format: YYYY-MM-DD
    message = (
        f"🚨 **Missed the Zoom meeting details?**\n\n"
        f"📅 **Date:** {current_date}\n"
        f"📩 Please contact the admin for assistance: @MikaET\n"
        f"🔒 Stay connected and never miss important updates!"
    )
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    data = {
        'chat_id': TELEGRAM_GROUP_CHAT_ID,
        'text': message,
        'parse_mode': 'Markdown'
    }
    requests.post(url, data=data)

if __name__ == '__main__':
    app.run(debug=True)