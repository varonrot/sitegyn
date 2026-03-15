import os
import json
import traceback
from flask import Flask, request, jsonify
from flask_cors import CORS
from dotenv import load_dotenv
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
    raise RuntimeError("Missing Supabase credentials")

if not OPENAI_API_KEY:
    raise RuntimeError("Missing OpenAI API key")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
client = OpenAI(api_key=OPENAI_API_KEY)

# ==========================================
# Load editor prompt
# ==========================================
PROMPT_PATH = os.path.join(os.path.dirname(__file__), "editor_update_prompt.txt")

with open(PROMPT_PATH, "r", encoding="utf-8") as f:
    EDITOR_PROMPT = f.read()

# ==========================================
# Flask
# ==========================================
app = Flask(__name__)
CORS(app)

# ==========================================
# Helpers
# ==========================================
def parse_update_block(text):
    try:
        start = text.find("<update>")
        end = text.find("</update>")
        if start == -1 or end == -1:
            return None

        raw = text[start + 8:end].strip()
        return json.loads(raw)
    except:
        traceback.print_exc()
        return None


def get_value_by_path(obj, path):
    if not path:
        return ""

    curr = obj
    for part in path.split("."):
        if not isinstance(curr, dict):
            return ""
        curr = curr.get(part)
        if curr is None:
            return ""
    return curr


def set_value_by_path(obj, path, value):

    keys = path.split(".")
    curr = obj

    for k in keys[:-1]:
        curr = curr.setdefault(k, {})

    curr[keys[-1]] = value


# ==========================================
# Health
# ==========================================
@app.route("/api/health")
def health():
    return jsonify({"status": "ok"})


# ==========================================
# Update Field
# ==========================================
@app.route("/api/update-field", methods=["POST"])
def update_field():
 
    try:

        data = request.get_json(force=True)

        project_id = data.get("project_id")
        field_path = data.get("field_path")
        instruction = data.get("instruction", "")

        if not project_id:
            return jsonify({"error": "missing_project_id"}), 400

        if not field_path:
            return jsonify({"error": "missing_field_path"}), 400

        # ==========================================
        # Load project
        # ==========================================

        project = (
            supabase.table("projects")
            .select("content_json")
            .eq("id", project_id)
            .single()
            .execute()
            .data
        )

        if not project:
            return jsonify({"error": "project_not_found"}), 404

        content_json = project.get("content_json")

        if not isinstance(content_json, dict):
            return jsonify({"error": "invalid_content_json"}), 500

        current_value = get_value_by_path(content_json, field_path)

        # ==========================================
        # Build prompt
        # ==========================================

        prompt = (
            EDITOR_PROMPT
            .replace("{{FIELD_PATH}}", field_path)
            .replace("{{CURRENT_VALUE}}", str(current_value))
            .replace("{{USER_MESSAGE}}", instruction)
        )

        # ==========================================
        # Call OpenAI
        # ==========================================

        completion = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[{
                "role": "user",
                "content": prompt
            }],
            temperature=0
        )

        assistant_text = completion.choices[0].message.content

        update_obj = parse_update_block(assistant_text)

        if not update_obj:
            return jsonify({"error": "invalid_update"}), 500

        changes = update_obj.get("changes", [])

        if not changes:
            return jsonify({"error": "no_changes"}), 500

        # ==========================================
        # Apply changes
        # ==========================================

        for change in changes:

            path = change.get("path")
            value = change.get("value")

            if not path:
                continue

            if value is None or value == "":
                continue

            set_value_by_path(content_json, path, value)

        # ==========================================
        # Save to Supabase
        # ==========================================

        supabase.table("projects") \
            .update({
                "content_json": content_json
            }) \
            .eq("id", project_id) \
            .execute()

        return jsonify({
            "status": "ok",
            "changes": changes,
            "value": changes[0]["value"] if changes else None
        })

    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


# ==========================================
# Run
# ==========================================
if __name__ == "__main__":
    port = int(os.getenv("PORT", "8001"))
    app.run(host="0.0.0.0", port=port)
