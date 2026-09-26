from flask import Flask, render_template, request, jsonify
from threading import Thread
import os

app = Flask(__name__, template_folder='templates')

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/health')
def health():
    return jsonify({"status": "online"}), 200

def run():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    server_thread = Thread(target=run)
    server_thread.daemon = True
    server_thread.start()
