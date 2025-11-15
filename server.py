import os
from flask import Flask, request, jsonify
from supabase import create_client, Client
from openai import OpenAI
from dotenv import load_dotenv
import uuid

# ==========================
# Load ENV
# ==========================
load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
client = OpenAI(api_key=OPENAI_API_KEY)

# ==========================
# Load System Prompt
# ==========================
with open("sitegyn_system_prompt.txt", "r") as f:
    SITEGYN_SYSTEM_PROMPT = f.read()

# ==========================
# Flask app
# ==========================
app = Flask(__name__)


# ==========================
# Helper: get project row
# ==========================
def get_project(project_id):
    response = supabase.table("projects").select("*").eq("project_id", project_id).execute()
    if response.data:
        return response.data[0]
    return None


# ==========================
# Helper: save chat message
# ==========================
def save_message(project_id, role, content):
    supabase.table("chat_messages").insert({
        "project_id": project_id,
        "role": role,
        "content": content
    }).execute()


# ==========================
# Helper: update project fields
# ==========================
def update_project(project_id, updates: dict):
    supabase.table("projects").update(updates).eq("project_id", project_id).execute()


# ==========================
# Detect if ready-to-build
# ==========================
def is_ready_to_build(project):
    required = ["business_type", "business_description", "business_name", "site_language"]
    return all(project.get(f) for f in required)


# ==========================
# POST /chat
# ==========================
@app.route("/chat", methods=["POST"])
def chat():
    data = request.get_json()
    project_id = data.get("project_id")
    user_message = data.get("message")

    if not project_id:
        return jsonify({"error": "missing project_id"}), 400

    # Save user message
    save_message(project_id, "user", user_message)

    # Fetch project
    project = get_project(project_id)

    if not project:
        return jsonify({"error": "project not found"}), 404

    # Fetch chat history for OpenAI
    history_response = supabase.table("chat_messages").select("*").eq("project_id", project_id).order("id").execute()
    history = history_response.data

    messages = [{"role": "system", "content": SITEGYN_SYSTEM_PROMPT}]
    for msg in history:
        messages.append({
            "role": msg["role"],
            "content": msg["content"]
        })

    # Call OpenAI
    ai_response = client.chat.completions.create(
        model="gpt-4.1",
        messages=messages
    )

    ai_text = ai_response.choices[0].message.content

    # Save assistant message
    save_message(project_id, "assistant", ai_text)

    # ========================
    # Extract fields (simple)
    # ========================
    # כאן בשלב מאוחר יותר נבנה NLP extraction חכם
    # בינתיים זיהוי בסיסי להדגמה:

    if "business type:" in ai_text.lower():
        pass  # אנחנו נבנה בהמשך extractor אמיתי

    # ========================
    # Ready to build?
    # ========================
    project = get_project(project_id)
    ready = is_ready_to_build(project)

    return jsonify({
        "reply": ai_text,
        "ready_to_build": ready
    })


# ==========================
# Run local
# ==========================
if __name__ == "__main__":
    app.run(port=5001, debug=True)
