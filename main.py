import os
import json
import logging
import requests
from flask import Flask

logger = logging.getLogger(__name__)

app = Flask(__name__)

# In-memory blueprint store (or hook up to a database/MongoDB)
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
        return True  # Bypass if secret key is not configured

    try:
        res = requests.post(
            "https://www.google.com/recaptcha/api/siteverify",
            data={
                "secret": RECAPTCHA_SECRET_KEY,
                "response": response_token
            },
            timeout=5
        )
        result = res.json()
        return result.get("success", False)
    except Exception as e:
        logger.error(f"reCAPTCHA verification error: {e}")
        return False


@app.route('/health')
def health_check():
    return "OK", 200
