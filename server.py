import os
import json
from flask import Flask, request, jsonify
from flask_cors import CORS
from supabase import create_client, Client
from openai import OpenAI
from dotenv import load_dotenv

# ==========================
# Load ENV
# ==========================
load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError("Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY in environment.")

if not OPENAI_API_KEY:
    raise RuntimeError("Missing OPENAI_API_KEY in environment.")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
client = OpenAI(api_key=OPENAI_API_KEY)

# ==========================
# Load System Prompt
# ==========================
SYSTEM_PROMPT_PATH = "sitegyn_system_prompt.txt"

if not os.path.exists(SYSTEM_PROMPT_PATH):
    raise RuntimeError(f"Missing system prompt file: {SYSTEM_PROMPT_PATH}")

with open(SYSTEM_PROMPT_PATH, "r", encoding="utf-8") as f:
    SITEGYN_SYSTEM_PROMPT = f.read()

# ==========================
# Flask app
# ==========================
app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})

# ==========================
# Helpers
# ==========================
def get_project(project_id: str):
    """Fetch a single project row by id (primary key)."""
    resp = supabase.table("projects").select("*").eq("id", project_id).execute()
    if resp.data:
        return resp.data[0]
    return None


def save_message(project_id: str, role: str, content: str, tokens_used: int | None = None):
    """Insert a chat message (user/assistant) to chat_messages table."""
    row = {
        "project_id": project_id,
        "role": role,
        "content": content,
    }
    if tokens_used is not None:
        row["tokens_used"] = tokens_used

    supabase.table("chat_messages").insert(row).execute()


def update_project(project_id: str, updates: dict):
    """Update fields in the projects table for a given id."""
    if not updates:
        return
    supabase.table("projects").update(updates).eq("id", project_id).execute()


def is_ready_to_build(project: dict) -> bool:
    """
    Check if all required fields for building a site are present.
    Required fields:
      - business_type
      - business_description
      - business_name
      - site_language
    """
    required = ["business_type", "business_description", "business_name", "site_language"]
    return all(project.get(field) for field in required)


def extract_and_update_fields(project_id: str, ai_text: str):
    """
    Extract the JSON block from the assistant's reply and update the 'projects' table
    with any non-null fields: business_type, business_description, business_name, site_language.

    We assume the model always ends the reply with:

    ```json
    {
      "business_type": ...,
      "business_description": ...,
      "business_name": ...,
      "site_language": ...
    }
    ```
    """
    if not ai_text:
        return

    start = ai_text.rfind("```json")
    if start == -1:
        return

    end = ai_text.find("```", start + 7)
    if end == -1:
        return

    json_str = ai_text[start + 7:end].strip()

    try:
        data = json.loads(json_str)
    except Exception:
        return

    updates = {}
    for field in ["business_type", "business_description", "business_name", "site_language"]:
        if field in data:
            val = data[field]
            if val is not None and str(val).strip() != "":
                updates[field] = val

    if updates:
        update_project(project_id, updates)


# ==========================
# Routes
# ==========================
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

        return jsonify({
            "project_id": project_id,
            "project": new_project
        })
    except Exception as e:
        print("Error in /api/start_project:", e)
        return jsonify({"error": "could_not_create_project"}), 500


@app.route("/api/chat", methods=["POST"])
def chat():
    """
    Main chat endpoint.

    Expected JSON body:
    {
      "project_id": "some-uuid",
      "message": "user message text"
    }

    Returns:
    {
      "reply": "assistant reply text",
      "ready_to_build": bool
    }
    """
    data = request.get_json() or {}
    project_id = (data.get("project_id") or "").strip()
    user_message = (data.get("message") or "").strip()

    if not project_id:
        return jsonify({"error": "missing project_id"}), 400
    if not user_message:
        return jsonify({"error": "missing message"}), 400

    # Make sure the project exists
    project = get_project(project_id)
    if not project:
        return jsonify({"error": "project_not_found"}), 404

    try:
        # 1) save user message
        save_message(project_id, "user", user_message)

        # 2) fetch history for this project (last ~30 messages)
        history_res = (
            supabase.table("chat_messages")
            .select("role, content, created_at")
            .eq("project_id", project_id)
            .order("created_at", desc=False)
            .limit(30)
            .execute()
        )
        history = history_res.data or []

        # 3) build messages for OpenAI
        messages = [{"role": "system", "content": SITEGYN_SYSTEM_PROMPT}]
        for m in history:
            messages.append({
                "role": m["role"],
                "content": m["content"],
            })

        # 4) call OpenAI
        completion = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=messages,
            temperature=0.4,
        )

        assistant_text = completion.choices[0].message.content
        total_tokens = completion.usage.total_tokens if completion.usage else None

        # 5) save assistant message
        save_message(project_id, "assistant", assistant_text, total_tokens)

        # 6) update project fields from structured JSON in the answer
        extract_and_update_fields(project_id, assistant_text)

        # 7) re-fetch project and check readiness
        updated_project = get_project(project_id)
        ready = is_ready_to_build(updated_project) if updated_project else False

        return jsonify({
            "reply": assistant_text,
            "ready_to_build": ready,
            "project_id": project_id,
        })

    except Exception as e:
        print("Error in /api/chat:", e)
        return jsonify({"error": "chat_failed"}), 500


# ==========================
# Local run
# ==========================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=True)
