import os
import json
import traceback

from flask import Flask, request, jsonify
from dotenv import load_dotenv
from flask_cors import CORS
from supabase import create_client
from openai import OpenAI

# ==========================================
# ENV
# ==========================================

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
client = OpenAI(api_key=OPENAI_API_KEY)

# ==========================================
# APP
# ==========================================

app = Flask(__name__)
CORS(app)

# ==========================================
# HELPERS
# ==========================================

def get_value_by_path(obj, path):

    if not path:
        return ""

    for p in path.split("."):
        obj = obj.get(p)

        if obj is None:
            return ""

    return obj


def set_value_by_path(obj, path, value):

    keys = path.split(".")
    curr = obj

    for k in keys[:-1]:
        curr = curr.setdefault(k, {})

    curr[keys[-1]] = value


# ==========================================
# UPDATE FIELD
# ==========================================

@app.route("/api/update-field", methods=["POST"])
def update_field():

    try:

        data = request.json

        project_id = data.get("project_id")
        field_path = data.get("field_path")
        instruction = data.get("instruction")

        if not project_id:
            return {"error": "missing_project_id"}, 400

        if not field_path:
            return {"error": "missing_field_path"}, 400

        # ==========================
        # Load project
        # ==========================

        project = supabase.table("projects") \
            .select("content_json") \
            .eq("id", project_id) \
            .single() \
            .execute() \
            .data

        content = project.get("content_json") or {}

        current_value = get_value_by_path(content, field_path)

        # ==========================
        # GPT rewrite
        # ==========================

        prompt = f"""
Rewrite the following website text.

Instruction:
{instruction}

Text:
{current_value}

Return only the rewritten text.
"""

        completion = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.4
        )

        new_value = completion.choices[0].message.content.strip()

        # ==========================
        # Update JSON
        # ==========================

        set_value_by_path(content, field_path, new_value)

        supabase.table("projects") \
            .update({"content_json": content}) \
            .eq("id", project_id) \
            .execute()

        return {
            "status": "ok",
            "value": new_value
        }

    except Exception as e:

        traceback.print_exc()

        return {
            "error": str(e)
        }, 500


# ==========================================
# HEALTH
# ==========================================

@app.route("/api/health")
def health():
    return {"status": "ok"}


# ==========================================
# RUN
# ==========================================

if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=9000,
        debug=True
    )
