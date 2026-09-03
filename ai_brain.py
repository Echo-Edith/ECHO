import os
import asyncio
import json
import urllib.request
import urllib.error

# Official Gemini API Endpoint
GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-preview-09-2025:generateContent"

def get_api_key():
    return os.getenv("GEMINI_API_KEY", "").strip()


async def call_gemini_api(user_prompt: str, system_prompt: str = "") -> str:
    """
    Calls Gemini 2.5 Flash API with mandatory exponential backoff:
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
                lambda: urllib.request.urlopen(req, timeout=10)
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
    Autonomous AI Brain for ORCA Studio: Handles ticket triaging, concierge responses,
    public @ORCA questions, spec drafting, and frustration sentiment detection.
    """

    @staticmethod
    async def generate_ticket_summary(category: str, answers: list, knowledge_base: str = "") -> str:
        prompt = f"Ticket Category: {category}\nClient Form Answers:\n"
        for ans in answers:
            prompt += f"- {ans.get('label', 'Question')}: {ans.get('value', 'N/A')}\n"

        system_prompt = (
            "You are the ORCA Studio AI Command Assistant. Summarize this new ticket inquiry in 3-4 bullet points. "
            "Identify the key deliverable, estimated scope, and recommended staff lead. Keep it professional and concise."
        )

        res = await call_gemini_api(prompt, system_prompt)
        if res:
            return res

        # Fallback 1
        summary_lines = [f"• **Category:** {category}"]
        for a in answers[:3]:
            summary_lines.append(f"• **{a.get('label')}:** {a.get('value')}")
        return "\n".join(summary_lines)

    @staticmethod
    async def answer_concierge_question(client_msg: str, ticket_context: str, knowledge_base: str = "") -> str:
        prompt = f"Knowledge Base:\n{knowledge_base}\n\nTicket Context:\n{ticket_context}\n\nClient Message: {client_msg}"
        system_prompt = (
            "You are ORCA Concierge, a helpful assistant for ORCA Studio. Answer the client's question politely and accurately "
            "based on the knowledge base. If unsure, let them know staff will review shortly. Keep answers under 150 words."
        )

        res = await call_gemini_api(prompt, system_prompt)
        if res:
            return res

        # Fallback 2
        return "Thank you for your message! Our development staff will review your request and reply shortly."

    @staticmethod
    async def answer_public_ping(user_name: str, message_text: str, knowledge_base: str = "") -> str:
        prompt = f"User: {user_name}\nQuestion: {message_text}\n\nStudio Knowledge Base:\n{knowledge_base}"
        system_prompt = (
            "You are ORCA Bot AI. Answer the server member's question concisely using ORCA Studio's knowledge base. "
            "Be friendly, professional, and invite them to open a ticket if they need custom development or Roblox assets."
        )

        res = await call_gemini_api(prompt, system_prompt)
        if res:
            return res

        # Fallback 3
        return f"Hello @{user_name}! Thanks for reaching out to ORCA Studio. Please check our channels or open a ticket for custom bot/Roblox asset inquiries!"

    @staticmethod
    async def generate_spec_sheet(category: str, answers: list) -> str:
        prompt = f"Category: {category}\nClient Requirements:\n"
        for a in answers:
            prompt += f"- {a.get('label')}: {a.get('value')}\n"

        system_prompt = (
            "Generate a structured Technical Specification Sheet for developers. Include:\n"
            "1. Core Features & Commands\n2. Database/Storage Requirements\n3. External API/Roblox Integrations\n4. Estimated Complexity (Low/Medium/High)."
        )

        res = await call_gemini_api(prompt, system_prompt)
        if res:
            return res

        return f"**Technical Spec Sheet ({category})**\n• Requirements gathered from submission form.\n• Staff review required."

    @staticmethod
    async def analyze_sentiment_and_frustration(message_text: str) -> bool:
        prompt = f"Message: \"{message_text}\""
        system_prompt = (
            "Analyze if this client message shows severe frustration, anger, impatience, or harassment. "
            "Reply with EXACTLY 'TRUE' if high frustration/anger is detected, or 'FALSE' otherwise."
        )

        res = await call_gemini_api(prompt, system_prompt)
        return "TRUE" in res.upper()

