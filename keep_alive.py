import os
import json
import logging
import requests
from flask import Flask

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# In-memory blueprint storage fallback
BLUEPRINT_STORE = {}

RECAPTCHA_SECRET_KEY = os.environ.get("RECAPTCHA_SECRET_KEY", "").strip()


def save_blueprint_data(guild_id: str, data: dict) -> None:
    """Saves blueprint JSON in memory indexed by target guild ID."""
    BLUEPRINT_STORE[str(guild_id)] = data
    logger.info(f"Saved blueprint for Guild ID: {guild_id}")


def get_blueprint_data(guild_id: str) -> dict:
    """Retrieves stored blueprint JSON by guild ID."""
    return BLUEPRINT_STORE.get(str(guild_id))


def verify_recaptcha(response_token: str) -> bool:
    """Verifies Google reCAPTCHA token if secret key is supplied."""
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


@app.route('/')
def health_check():
    return "OK — Bot and Web Server Operational", 200
