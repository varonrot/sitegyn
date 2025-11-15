import os
import json
from typing import List, Dict, Any, Tuple, Optional

from dotenv import load_dotenv
from flask import Flask, request, jsonify
from flask_cors import CORS
from supabase import create_client, Client
from openai import OpenAI, RateLimitError
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
    resources={r"/api/*": {"origins": "*"}}
)

# ==========================================
# Helper: update project
# ==========================================


def update_project(project_id: str, updates: Dict[str, Any]) -> None:
    """
    Update a project row in Supabase with the given fields.
    Only fields that exist in the table will be updated.
    """
    if not updates:
        return
    try:
        supabase.table("projects").update(updates).eq("id", project_id).execute()
        print(f"[update_project] Updated project {project_id} with keys: {list(updates.keys())}")
    except Exception as e:
        print("[update_project] Error:", e)
        traceback.print_exc()


# ==========================================
# Helper: extract <update> JSON block from assistant text
# ==========================================


def extract_update_block(text: str) -> Tuple[str, Optional[Dict[str, Any]]]:
    """
    Looks for a block of the form:
       <update> { ...json... } </update>
    Returns:
       (clean_text_without_block, parsed_json_or_None)
    If parsing fails, returns original text and None.
    """
    if not text:
        return text, None

    start_tag = "<update>"
    end_tag = "</update>"

    start_idx = text.find(start_tag)
    end_idx = text.find(end_tag)

    if start_idx == -1 or end_idx == -1 or end_idx < start_idx:
        # No update block
        return text, None

    json_str = text[start_idx + len(start_tag): end_idx].strip()
    clean_text = (text[:start_idx] + text[end_idx + len(end_tag):]).strip()

    try:
        updates = json.loads(json_str)
        if not isinstance(updates, dict):
            print("[extract_update_block] JSON is not an object, ignoring.")
            return clean_text or text, None
        return clean_text or text, updates
    except Exception as e:
        print("[extract_update_block] Failed to parse JSON:", e)
        traceback.print_exc()
        # אם ה־JSON לא תקין, עדיף להציג את הטקסט כמו שהוא בלי לנסות לעדכן
        return text, None


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
    store assistant reply, update project (if <update>), and return the reply.
    Expected JSON body:
      {
        "project_id": "...",
        "message": "..."
      }
    """
    try:
        data = request.get_json() or {}
        project_id = data.get("project_id")
        message = (data.get("message") or "").strip()

        if not project_id or not message:
            raise ValueError("Missing project_id or message")

        # 1. Save user message
        supabase.table("chat_messages").insert({
            "project_id": project_id,
            "role": "user",
            "content": message,
            "status": "complete"
        }).execute()

        # 2. Pull full history from DB (source of truth)
        history_resp = (
            supabase.table("chat_messages")
            .select("role, content")
            .eq("project_id", project_id)
            .order("created_at", desc=False)
            .execute()
        )
        history_rows = history_resp.data or []

        messages: List[Dict[str, str]] = []

        # System prompt – loaded from file or hardcoded
        # בראש הקובץ, אחרי ה-imports:
        SYSTEM_PROMPT_PATH = "sitegyn_system_prompt.txt"

        with open(SYSTEM_PROMPT_PATH, "r", encoding="utf-8") as f:
            SITEGYN_SYSTEM_PROMPT = f.read()

        # בתוך chat():
        messages: List[Dict[str, str]] = []
        messages.append({"role": "system", "content": SITEGYN_SYSTEM_PROMPT})
        messages.append({
            "role": "system",
            "content": f"The current project_id is {project_id}. Never mention this ID to the user."
        })

        # Optional second system message with project_id context
        messages.append({
            "role": "system",
            "content": f"The current project_id is {project_id}. Never mention this ID to the user."
        })

        # Add history
        for row in history_rows:
            r = row.get("role")
            c = row.get("content")
            if r and c:
                messages.append({"role": r, "content": c})

        # Safety: make sure ההודעה האחרונה היא זו ששלחנו עכשיו
        if not messages or messages[-1]["content"] != message:
            messages.append({"role": "user", "content": message})

        # 3. Call OpenAI
        try:
            completion = client.chat.completions.create(
                model="gpt-4.1-mini",
                messages=messages,
                temperature=0.4,
            )
        except RateLimitError as e:
            print("OpenAI quota error:", e)
            return jsonify({"error": "openai_quota_exceeded"}), 503

        choice = completion.choices[0]
        msg_obj = getattr(choice, "message", None) or getattr(choice, "delta", None)

        if msg_obj is None:
            # Fallback – לא אמור לקרות, אבל ליתר בטחון
            assistant_text = ""
        else:
            # אובייקט של openai מתנהג כמו dict/אובייקט
            try:
                assistant_text = msg_obj.get("content", "")
            except AttributeError:
                assistant_text = getattr(msg_obj, "content", "")

        usage = getattr(completion, "usage", None)
        total_tokens = getattr(usage, "total_tokens", None) if usage else None

        # 4. Extract <update> block (if exists) and update project
        clean_text, updates = extract_update_block(assistant_text)

        if updates:
            update_project(project_id, updates)

        visible_reply = clean_text or assistant_text

        # 5. Save assistant reply
        assistant_row = {
            "project_id": project_id,
            "role": "assistant",
            "content": visible_reply,
            "status": "complete"
        }
        if total_tokens is not None:
            assistant_row["tokens_used"] = total_tokens

        supabase.table("chat_messages").insert(assistant_row).execute()

        # 6. Return reply
        return jsonify({
            "reply": visible_reply,
            "project_id": project_id,
        })

    except Exception as e:
        print("Error in /api/chat:", e)
        traceback.print_exc()
        return jsonify({"error": "chat_failed"}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))
    app.run(host="0.0.0.0", port=port, debug=True)
