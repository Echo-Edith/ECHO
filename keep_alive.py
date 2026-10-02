import os
import json
import logging
import requests
from flask import Flask, render_template

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# In-memory blueprint store
BLUEPRINT_STORE = {}

RECAPTCHA_SECRET_KEY = os.environ.get("RECAPTCHA_SECRET_KEY", "").strip()


def save_blueprint_data(guild_id: str, data: dict) -> None:
    BLUEPRINT_STORE[str(guild_id)] = data
    logger.info(f"Saved blueprint for Guild ID: {guild_id}")


def get_blueprint_data(guild_id: str) -> dict:
    return BLUEPRINT_STORE.get(str(guild_id))


def verify_recaptcha(response_token: str) -> bool:
    if not RECAPTCHA_SECRET_KEY:
        return True

    try:
        res = requests.post(
            "https://www.google.com/recaptcha/api/siteverify",
            data={
                "secret": RECAPTCHA_SECRET_KEY,
                "response": response_token
            },
            timeout=5
        )
        return res.json().get("success", False)
    except Exception as e:
        logger.error(f"reCAPTCHA verification error: {e}")
        return False


# Serve the Web Builder HTML Dashboard
@app.route('/')
def index():
    return render_template('index.html')


# Separate endpoint for health checks
@app.route('/health')
def health_check():
    return "OK", 200
