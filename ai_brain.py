import os
import re
import json
import asyncio
import urllib.request
import urllib.error

# Official Gemini API Endpoint
GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-preview-09-2025:generateContent"


def get_api_key():
    return os.getenv("GEMINI_API_KEY", "").strip()


async def call_gemini_api(user_prompt: str, system_prompt: str = "") -> str:
    """
    Calls Gemini API with exponential backoff:
    Retries up to 5 times with delays of 1s, 2s, 4s, 8s, 16s.
    """
    api_key = get_api_key()
    if not api_key:
        return ""

    url = f"{GEMINI_API_URL}?key={api_key}"
    payload = {
        "contents": [{
            "parts": [{"text": user_prompt}]
        }]
    }
    if system_prompt:
        payload["systemInstruction"] = {
            "parts": [{"text": system_prompt}]
        }

    data_bytes = json.dumps(payload).encode('utf-8')
    delays = [1, 2, 4, 8, 16]

    for attempt, delay in enumerate(delays):
        try:
            req = urllib.request.Request(
                url,
                data=data_bytes,
                headers={"Content-Type": "application/json"},
                method="POST"
            )

            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: urllib.request.urlopen(req, timeout=20)
            )

            res_body = response.read().decode('utf-8')
            res_json = json.loads(res_body)

            text = res_json.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "")
            if text:
                return text.strip()

        except urllib.error.HTTPError as e:
            if e.code == 429:
                await asyncio.sleep(delay)
                continue
            else:
                break
        except Exception:
            await asyncio.sleep(delay)
            continue

    return ""


class AIBrain:
    """
    Autonomous AI Architect for ORCA Studio:
    Handles automated website generation, custom Vercel landing pages,
    and technical site specification building.
    """

    @staticmethod
    def _clean_code_block(raw_code: str) -> str:
        """Strips markdown code fences (```html ... ```) from output."""
        cleaned = re.sub(r"^```[a-zA-Z]*\n", "", raw_code.strip())
        cleaned = re.sub(r"\n```$", "", cleaned.strip())
        return cleaned.strip()

    @staticmethod
    async def generate_vercel_site(site_title: str, description: str, theme_style: str = "Deep Sea Ocean", features: list = None) -> str:
        """
        Generates a complete standalone index.html page tailored for Vercel deployment.
        """
        features_str = ", ".join(features) if features else "Interactive UI, Modern Responsive Layout, Smooth Animations"
        
        prompt = (
            f"Site Title: {site_title}\n"
            f"Description/Purpose: {description}\n"
            f"Visual Theme Style: {theme_style}\n"
            f"Required Features: {features_str}"
        )

        system_prompt = (
            "You are the Lead Web Architect at ORCA Studio. Output ONLY raw, production-ready HTML code for a complete index.html file. "
            "Requirements:\n"
            "1. Use Tailwind CSS via CDN (<script src=\"https://cdn.tailwindcss.com\"></script>).\n"
            "2. Include FontAwesome for icons (<link rel=\"stylesheet\" href=\"https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css\">).\n"
            "3. Ensure full mobile responsiveness and dark mode styling matched to the requested theme.\n"
            "4. Include interactive client-side JavaScript for animations, modal toggles, or dynamic features.\n"
            "5. Do NOT include any markdown formatting or explanatory prose outside the raw code."
        )

        raw_res = await call_gemini_api(prompt, system_prompt)
        if raw_res:
            return AIBrain._clean_code_block(raw_res)

        # Emergency Fallback HTML Page
        return f"""<!DOCTYPE html>
<html lang="en" class="dark">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{site_title}</title>
  <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-slate-950 text-white min-h-screen flex flex-col items-center justify-center p-6 text-center">
  <div class="max-w-md p-8 rounded-2xl bg-slate-900 border border-cyan-500/30 shadow-2xl space-y-4">
    <h1 class="text-3xl font-black text-cyan-400">{site_title}</h1>
    <p class="text-sm text-slate-300">{description}</p>
    <div class="pt-4">
      <a href="#" class="px-6 py-2.5 rounded-xl bg-cyan-500 hover:bg-cyan-400 text-slate-950 font-bold transition-colors">
        Get Started
      </a>
    </div>
  </div>
</body>
</html>"""

    @staticmethod
    async def generate_site_config(prompt_details: str) -> dict:
        """
        Parses raw user ticket requirements into a structured JSON payload for site setup.
        """
        prompt = f"User Request: {prompt_details}"
        system_prompt = (
            "Analyze the site requirements and output ONLY a valid JSON object with keys:\n"
            "- 'title': (String)\n"
            "- 'theme': (String - e.g., 'Deep Sea', 'Cyberpunk', 'Minimalist Dark')\n"
            "- 'sections': (Array of Strings - e.g., ['Hero', 'Features', 'Pricing', 'Discord Invite'])\n"
            "- 'has_lockdown_gate': (Boolean)"
        )

        res = await call_gemini_api(prompt, system_prompt)
        try:
            cleaned_json = AIBrain._clean_code_block(res)
            return json.loads(cleaned_json)
        except Exception:
            return {
                "title": "ORCA Custom Web Project",
                "theme": "Deep Sea Ocean",
                "sections": ["Hero", "Features", "Contact"],
                "has_lockdown_gate": True
            }

    @staticmethod
    async def answer_dev_query(client_query: str, project_context: str = "") -> str:
        """
        Answers client questions regarding their custom Vercel deployment and specs.
        """
        prompt = f"Project Context:\n{project_context}\n\nClient Question: {client_query}"
        system_prompt = (
            "You are ORCA Studio's Web Deployment AI. Answer client queries regarding Vercel deployments, "
            "site features, domain setups, or custom UI modifications. Keep responses clear, helpful, and under 150 words."
        )

        res = await call_gemini_api(prompt, system_prompt)
        if res:
            return res

        return "Thank you for reaching out! Our web development team will review your project configuration and answer your questions shortly."
