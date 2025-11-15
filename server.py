import os
from typing import List, Dict, Any

from dotenv import load_dotenv
from flask import Flask, request, jsonify
from supabase import create_client, Client
from openai import OpenAI

# ==============================
# Environment & clients
# ==============================

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
    raise RuntimeError("SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY are not set in the environment")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
openai_client = OpenAI(api_key=OPENAI_API_KEY)

app = Flask(__name__)
# לאפשר CORS ל־sitegyn.com (ולוקאלית לפיתוח)
CORS(app, resources={
    r"/api/*": {
        "origins": [
            "https://sitegyn.com",
            "http://localhost:8000",
            "http://127.0.0.1:8000"
        ]
    }
})

# ==============================
# Load system prompt
# ==============================

def load_system_prompt() -> str:
    """
    Load the Sitegyn system prompt from file, or fall back to a short default.
    """
    try:
        base_dir = os.path.dirname(os.path.abspath(__file__))
        prompt_path = os.path.join(base_dir, "sitegyn_system_prompt.txt")
        with open(prompt_path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return (
            "You are Sitegyn, an assistant that interviews the user to create a website. "
            "Ask short, clear questions in English. Collect all details needed to design "
            "a small business website, including business type, services, target audience, "
            "location, language, and desired style."
        )


SITEGYN_SYSTEM_PROMPT = load_system_prompt()


# ==============================
# Helper functions
# ==============================

def save_message(project_id: str, role: str, content: str, tokens_used: int | None = None):
    """
    Insert a single chat message into the chat_messages table.
    """
    supabase.table("chat_messages").insert(
        {
            "project_id": project_id,
            "role": role,
            "content": content,
            "tokens_used": tokens_used,
        }
    ).execute()


def extract_and_update_fields(project_id: str, ai_text: str):
    """
    OPTIONAL: try to find a JSON block in the assistant text and update the projects row.
    If nothing is found, this function does nothing and does not raise.
    """
    import json
    import re

    try:
        match = re.search(r"\{.*\}", ai_text, re.DOTALL)
        if not match:
            return

        block = match.group(0)
        data = json.loads(block)

        allowed_fields = [
            "business_name",
            "business_type",
            "niche",
            "city",
            "country",
            "site_language",
            "main_goal",
            "primary_color",
            "style_keywords",
            "pages_json",
            "content_json",
            "selected_template_id",
        ]

        updates: Dict[str, Any] = {}
        for field in allowed_fields:
            if field in data and data[field] is not None:
                updates[field] = data[field]

        if updates:
            supabase.table("projects").update(updates).eq("id", project_id).execute()
    except Exception:
        # We never want JSON extraction to break the chat flow
        return


# ==============================
# Routes
# ==============================


@app.route("/", methods=["GET"])
def health():
    """Simple health-check endpoint."""
    return jsonify({"status": "ok", "service": "sitegyn-backend"})


@app.route("/api/start_project", methods=["POST"])
def start_project():
    """
    Create a new empty project row in Supabase and return its project_id.
    """
    try:
        result = supabase.table("projects").insert({}).execute()
        new_project = result.data[0]
        project_id = new_project["id"]

        return jsonify(
            {
                "project_id": project_id,
                "project": new_project,
            }
        )
    except Exception as e:
        print("Error in /api/start_project:", e)
        return jsonify({"error": "could_not_create_project"}), 500


@app.route("/api/chat", methods=["POST"])
def chat():
    """
    Main chat endpoint. Expects JSON:
    {
      "project_id": "...",
      "message": "user text"
    }
    Returns JSON:
    {
      "reply": "...",
      "project_id": "..."
    }
    """
    try:
        payload = request.get_json(force=True) or {}
        project_id = payload.get("project_id")
        user_message = payload.get("message")

        if not project_id or not user_message:
            return jsonify({"error": "missing_project_id_or_message"}), 400

        # 1) Save user message
        save_message(project_id, "user", user_message)

        # 2) Load full history for this project
        history_resp = (
            supabase.table("chat_messages")
            .select("role, content")
            .eq("project_id", project_id)
            .order("created_at", ascending=True)
            .execute()
        )
        history: List[Dict[str, Any]] = history_resp.data or []

        # 3) Build messages list for OpenAI
        messages: List[Dict[str, str]] = [
            {"role": "system", "content": SITEGYN_SYSTEM_PROMPT}
        ]

        for msg in history:
            role = "assistant" if msg["role"] == "assistant" else "user"
            messages.append({"role": role, "content": msg["content"]})

        # 4) Call OpenAI
        completion = openai_client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=messages,
            temperature=0.4,
        )

        assistant_text = completion.choices[0].message.content
        total_tokens = completion.usage.total_tokens if completion.usage else None

        # 5) Save assistant message
        save_message(project_id, "assistant", assistant_text, total_tokens)

        # 6) Try to extract structured data & update project
        extract_and_update_fields(project_id, assistant_text)

        # 7) Return reply to frontend
        return jsonify({"reply": assistant_text, "project_id": project_id})
    except Exception as e:
        print("Error in /api/chat:", e)
        return jsonify({"error": "chat_failed"}), 500


# ==============================
# Local dev
# ==============================

if __name__ == "__main__":
    # For local testing only. On Render, the 'Start Command' runs this file.
    port = int(os.getenv("PORT", "5001"))
    app.run(host="0.0.0.0", port=port, debug=True)
