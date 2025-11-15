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
# הרשה בקשות מ-frontend (אפשר להתחיל מ-* ואז להקשיח)
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


def save_message(project_id: str, role: str, content: str):
    """Insert a chat message (user/assistant) to chat_messages table."""
    supabase.table("chat_messages").insert({
        "project_id": project_id,
        "role": role,
        "content": content
    }).execute()


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

    # Find the last ```json block
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
        # If JSON is invalid, we silently skip
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


@app.route("/chat", methods=["POST"])
def chat():
    """
    Main chat endpoint.

    Expected JSON body:
    {
      "project_id": "some-uuid-or-id",
      "message": "user message text"
    }

    Returns:
    {
      "reply": "assistant reply text",
      "ready_to_build": bool
    }
    """
    data = request.get_json() or {}
    project_id = data.get("project_id")
    user_message = data.get("message")

    if not project_id:
        return jsonify({"error": "missing project_id"}), 400
    if not user_message:
        return jsonify({"error": "missing message"}), 400

    # Ensure project exists
    project = get_project(project_id)
    if not project:
        return jsonify({"error": "project not found"}), 404

    # Save user message
    save_message(project_id, "user", user_message)

    # Fetch chat history for this project
    history_resp = supabase.table("chat_messages").select("*").eq("project_id", project_id).order("id", ascending=True).execute()
    history = history_resp.data or []

    messages = [{"role": "system", "content": SITEGYN_SYSTEM_PROMPT}]

    # Add previous conversation
    for msg in history:
        messages.append({
            "role": msg["role"],
            "content": msg["content"]
        })

    # Call OpenAI Chat Completion
    ai_response = client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=messages
    )

    ai_text = ai_response.choices[0].message.content

    # Save assistant message
    save_message(project_id, "assistant", ai_text)

    # Extract structured fields from AI text & update project
    extract_and_update_fields(project_id, ai_text)

    # Re-fetch project after possible updates
    updated_project = get_project(project_id)
    ready = is_ready_to_build(updated_project) if updated_project else False

    # For now we only return reply + ready_to_build
    # Later you can add: "site_url": "...", once build_site() exists.
    return jsonify({
        "reply": ai_text,
        "ready_to_build": ready
    })


# ==========================
# Local run
# ==========================
if __name__ == "__main__":
    # For local development
    app.run(host="0.0.0.0", port=5001, debug=True)
