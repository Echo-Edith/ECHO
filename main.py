import os
from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from google import genai
from google.genai import types

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "super-secret-key")

# Initialize GenAI Client
genai_client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

@app.route('/')
def index():
    user = session.get('user', None)
    site_key = os.environ.get("RECAPTCHA_SITE_KEY", "your-recaptcha-site-key")
    return render_template('index.html', user=user, site_key=site_key)

@app.route('/api/generate', methods=['POST'])
def generate_layout():
    data = request.get_json() or {}
    prompt = data.get('prompt', '')
    separator = data.get('separator', '│')
    
    system_instruction = f"""
    You are an expert Discord architect. Output ONLY raw JSON matching this structure:
    {{
      "server_name": "Server Title",
      "roles": [
        {{"name": "Owner", "color": "#eab308"}},
        {{"name": "Member", "color": "#22c55e"}}
      ],
      "categories": [
        {{
          "name": "GENERAL",
          "emoji": "💬",
          "is_private": false,
          "channels": [
            {{"name": "welcome", "type": "text", "emoji": "👋", "topic": "Welcome channel", "is_private": false}}
          ]
        }}
      ]
    }}
    Use the separator '{separator}' between emojis and names when formatting if applicable.
    """

    try:
        # Use Chat instance to avoid automatic function calling deprecation warning
        chat = genai_client.chats.create(
            model="gemini-2.5-flash",
            config=types.GenerateContentConfig(
                system_instruction=system_instruction,
                response_mime_type="application/json"
            )
        )
        
        response = chat.send_message(f"Create a Discord layout for: {prompt}")
        import json
        parsed_layout = json.loads(response.text)
        
        return jsonify({"success": True, "data": parsed_layout})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/submit', methods=['POST'])
def submit_layout():
    layout_data = request.get_json()
    
    # Process layout, post to webhook, or persist to MongoDB
    print("Received layout submission:", layout_data)
    
    return jsonify({"success": True, "status": "Layout dispatched successfully"})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000)), debug=True)
