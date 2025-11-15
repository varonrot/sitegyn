import os
import json
import traceback
from typing import List, Dict, Any

from dotenv import load_dotenv
from flask import Flask, request, jsonify
from flask_cors import CORS
from supabase import create_client, Client
from openai import OpenAI

# ==========================================
# Load environment
# ==========================================
load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
    raise RuntimeError("Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY in environment")

if not OPENAI_API_KEY:
    raise RuntimeError("Missing OPENAI_API_KEY in environment")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
client = OpenAI(api_key=OPENAI_API_KEY)

# ==========================================
# Load system prompt from file
# ==========================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROMPT_PATH = os.path.join(BASE_DIR, "sitegyn_system_prompt.txt")

try:
    with open(PROMPT_PATH, "r", encoding="utf-8") as f:
        SITEGYN_SYSTEM_PROMPT = f.read()
except Exception:
    # Fallback minimal prompt so the app still runs
    SITEGYN_SYSTEM_PROMPT = (
        "You are the Sitegyn Assistant. You interview the user to design a website for their business. "
        "Ask one question at a time and keep the conversation short and friendly."
    )

# ==========================================
# Flask app
# ==========================================
app = Flask(__name__)

# Allow the frontend to call our API
# IMPORTANT: enable CORS for ALL routes (/*), not just /api/*,
# because the frontend also pings the root URL "/".
CORS(
    app,
    resources={r"/*": {"origins": "*"}},
    supports_credentials=True,
)

# Simple health check so GET / won't return 404 (and will include CORS headers)
@app.route("/", methods=["GET"])
def health():
    return jsonify({"status": "ok"})

# ==========================================
# Helper: extract <update> JSON block
# ==========================================
def extract_update_block(text: str) -> Dict[str, Any]:
    """
    Look for the last <update>...</update> block in the text
    and return it as a dict. If none or invalid, return {}.
    """
    if not text:
        return {}

    start = text.rfind("<update>")
    end = text.rfind("</update>")
    if start == -1 or end == -1 or end <= start:
        return {}

    json_str = text[start + len("<update>"):end].strip()

    # Remove ```json ... ``` wrappers if the model added them
    if json_str.startswith("```"):
        # e.g. ```json\n{...}\n```
        first_newline = json_str.find("\n")
        last_tick = json_str.rfind("```")
        if first_newline != -1 and last_tick != -1 and last_tick > first_newline:
            json_str = json_str[first_newline + 1:last_tick].strip()

    try:
        parsed = json.loads(json_str)
        if isinstance(parsed, dict):
            return parsed
        else:
            return {}
    except Exception:
        print("Failed to parse <update> JSON:", json_str)
        traceback.print_exc()
        return {}

# ==========================================
# Helper: strip <update> block from text for frontend
# ==========================================
def strip_update_block(text: str) -> str:
    if not text:
        return ""
    start = text.rfind("<update>")
    end = text.rfind("</update>")
    if start == -1 or end == -1 or end <= start:
        return text
    visible = text[:start].rstrip()
    tail = text[end + len("</update>"):].strip()
    if tail:
        visible = visible + "\n\n" + tail
    return visible

# ==========================================
# Helper: update project row in Supabase
# ==========================================
def update_project(project_id: str, updates: Dict[str, Any]) -> None:
    if not updates:
        return
    try:
        # Never allow id override
        if "id" in updates:
            updates.pop("id")

        resp = (
            supabase.table("projects")
            .update(updates)
            .eq("id", project_id)
            .execute()
        )
        print("Updated project", project_id, "with", updates, "resp count:", len(resp.data or []))
    except Exception:
        print("Error updating project", project_id)
        traceback.print_exc()

# ==========================================
# /api/start_project — create new project row
# ==========================================
@app.route("/api/start_project", methods=["POST"])
def start_project():
    try:
        # Create an empty project row; defaults (created_at, etc.) are handled in DB
        insert_resp = supabase.table("projects").insert({}).execute()
        if not insert_resp.data:
            raise RuntimeError("Insert into projects returned no data")
        project_id = insert_resp.data[0]["id"]
        return jsonify({"project_id": project_id})
    except Exception as e:
        print("Error in /api/start_project:", e)
        traceback.print_exc()
        return jsonify({"error": "start_project_failed"}), 500

# ==========================================
# /api/chat — main chat endpoint
# ==========================================
@app.route("/api/chat", methods=["POST"])
def chat():
    try:
        data = request.get_json(force=True) or {}
        project_id = data.get("project_id")
        message = data.get("message", "").strip()

        if not project_id:
            return jsonify({"error": "missing_project_id"}), 400
        if not message:
            return jsonify({"error": "empty_message"}), 400

        # 1) Save the user message in chat_messages
        supabase.table("chat_messages").insert(
            {
                "project_id": project_id,
                "role": "user",
                "content": message,
                "status": "complete",
            }
        ).execute()

        # 2) Load full chat history for this project
        history_resp = (
            supabase.table("chat_messages")
            .select("role, content")
            .eq("project_id", project_id)
            .order("created_at", desc=False)  # ascending order
            .execute()
        )
        history_rows = history_resp.data or []

        # Count how many user answers so far (for speed-mode)
        user_turns = sum(1 for row in history_rows if row.get("role") == "user")

        # 3) Build the OpenAI messages array
        messages: List[Dict[str, str]] = []

        # Main system prompt from file
        messages.append({"role": "system", "content": SITEGYN_SYSTEM_PROMPT})

        # Hidden context: current project_id
        messages.append(
            {
                "role": "system",
                "content": (
                    f"The current project_id is {project_id}. "
                    "Never mention this ID to the user."
                ),
            }
        )

        # Speed-mode instruction, based on user_turns
        messages.append(
            {
                "role": "system",
                "content": (
                    f"For this project there have been {user_turns} user answers so far. "
                    "After 3 or more user answers, if you have not yet explicitly asked the user "
                    "whether they want to see an initial website demo or continue the interview, "
                    "you MUST offer that choice in your next reply and you MUST NOT ask additional "
                    "business questions in the same message."
                ),
            }
        )

        # Append full chat history (user + assistant)
        for row in history_rows:
            role = row.get("role") or "user"
            content = row.get("content") or ""
            messages.append({"role": role, "content": content})

        # Safety: ensure the last message is the current user message
        if not history_rows or (history_rows and history_rows[-1].get("content") != message):
            messages.append({"role": "user", "content": message})

        # 4) Call OpenAI
        completion = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=messages,
            temperature=0.4,
        )

        assistant_text = completion.choices[0].message.content or ""
        usage = getattr(completion, "usage", None)
        total_tokens = None
        if usage is not None:
            total_tokens = getattr(usage, "total_tokens", None) or getattr(
                usage, "output_tokens", None
            )

        # 5) Extract and apply <update> block if present
        updates = extract_update_block(assistant_text)
        if updates:
            update_project(project_id, updates)

        # 6) Store the assistant reply in chat_messages
        supabase.table("chat_messages").insert(
            {
                "project_id": project_id,
                "role": "assistant",
                "content": assistant_text,
                "tokens_used": total_tokens,
                "status": "complete",
            }
        ).execute()

        # 7) Strip the <update> block before sending back to the frontend
        visible_reply = strip_update_block(assistant_text)

        return jsonify(
            {
                "reply": visible_reply,
                "project_id": project_id,
            }
        )

    except Exception as e:
        print("Error in /api/chat:", e)
        traceback.print_exc()
        return jsonify({"error": "chat_failed"}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))
    app.run(host="0.0.0.0", port=port, debug=True)
