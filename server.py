import os
from typing import List, Dict, Any

from dotenv import load_dotenv
from flask import Flask, request, jsonify
from flask_cors import CORS
from supabase import create_client, Client
from openai import OpenAI
import traceback

# ==========================================
# Load environment
# ==========================================
load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
    raise RuntimeError("Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY")

if not OPENAI_API_KEY:
    raise RuntimeError("Missing OPENAI_API_KEY")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
client = OpenAI(api_key=OPENAI_API_KEY)

# ==========================================
# Flask app + CORS
# ==========================================
app = Flask(__name__)

CORS(
    app,
    resources={
        r"/api/*": {
            "origins": [
                "https://sitegyn.com",
                "https://www.sitegyn.com",
                "http://localhost:8000",
                "http://127.0.0.1:8000",
            ],
            "supports_credentials": False,
        }
    },
)

# ==========================================
# Helper to update project fields
# ==========================================


def update_project(project_id: str, updates: Dict[str, Any]) -> None:
    supabase.table("projects").update(updates).eq("id", project_id).execute()


# ==========================================
# Routes
# ==========================================


@app.route("/", methods=["GET"])
def health():
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

        return jsonify({
            "project_id": project_id,
            "project": new_project,
        })
    except Exception as e:
        print("Error in /api/start_project:", e)
        traceback.print_exc()
        return jsonify({"error": "could_not_create_project"}), 500


@app.route("/api/chat", methods=["POST"])
def chat():
    """
    Receive a chat message, store it, call OpenAI with chat history,
    store assistant reply, and return the reply.
    Expected JSON body:
      {
        "project_id": "...",
        "message": "...",
        "chat_history": [ { "role": "user"|"assistant", "content": "..." }, ... ]
      }
    """
    try:
        data = request.get_json() or {}
        project_id = data.get("project_id")
        message = (data.get("message") or "").strip()
        chat_history = data.get("chat_history") or []

        if not project_id or not message:
            raise ValueError("Missing project_id or message")

        # 1. Save user message
        supabase.table("chat_messages").insert({
            "project_id": project_id,
            "role": "user",
            "content": message,
        }).execute()

        # 2. Pull full history from DB (source of truth)
        history_resp = (
            supabase.table("chat_messages")
            .select("role, content")
            .eq("project_id", project_id)
            .order("created_at", ascending=True)
            .execute()
        )
        history_rows = history_resp.data or []

        messages: List[Dict[str, str]] = [
            {"role": row["role"], "content": row["content"]} for row in history_rows
        ]

        # Safety: make sure ההודעה האחרונה היא זו ששלחנו עכשיו
        if not messages or messages[-1]["content"] != message:
            messages.append({"role": "user", "content": message})

        # 3. Call OpenAI
        completion = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=messages,
            temperature=0.4,
        )

        choice = completion.choices[0]

        # תומך גם במבנה dict וגם באובייקט
        msg_obj = getattr(choice, "message", None) or choice.get("message")
        if isinstance(msg_obj, dict):
            assistant_text = msg_obj.get("content", "")
        else:
            assistant_text = getattr(msg_obj, "content", "")

        usage = getattr(completion, "usage", None)
        total_tokens = getattr(usage, "total_tokens", None) if usage else None

        # 4. Save assistant reply
        supabase.table("chat_messages").insert({
            "project_id": project_id,
            "role": "assistant",
            "content": assistant_text,
            "tokens_used": total_tokens,
        }).execute()

        # 5. Return reply
        return jsonify({
            "reply": assistant_text,
            "project_id": project_id,
        })

    except Exception as e:
        print("Error in /api/chat:", e)
        traceback.print_exc()
        return jsonify({"error": "chat_failed"}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))
    app.run(host="0.0.0.0", port=port, debug=True)
