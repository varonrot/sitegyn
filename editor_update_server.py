import os
import json
import re
from flask import Flask, request, jsonify
from dotenv import load_dotenv
from supabase import create_client
from openai import OpenAI

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
OPENAI_KEY = os.getenv("OPENAI_API_KEY")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
client = OpenAI(api_key=OPENAI_KEY)

app = Flask(__name__)

with open("editor_update_prompt.txt","r") as f:
    PROMPT = f.read()


# ===============================
# JSON helpers
# ===============================

def get_value_by_path(obj, path):
    cur = obj
    for p in path.split("."):
        if isinstance(cur, dict) and p in cur:
            cur = cur[p]
        else:
            return ""
    return cur


def set_value_by_path(obj, path, value):
    keys = path.split(".")
    cur = obj

    for k in keys[:-1]:
        if k not in cur or not isinstance(cur[k], dict):
            cur[k] = {}
        cur = cur[k]

    cur[keys[-1]] = value


def extract_update(text):
    m = re.search(r"<update>(.*?)</update>", text, re.S)
    if not m:
        return None

    try:
        return json.loads(m.group(1))
    except:
        return None


# ===============================
# API
# ===============================

@app.route("/api/editor-update", methods=["POST"])
def editor_update():

    body = request.json

    project_id = body.get("project_id")
    path = body.get("path")
    message = body.get("message")

    if not project_id or not path or not message:
        return jsonify({"error":"missing parameters"}),400

    # ---- load project content
    res = supabase.table("projects") \
        .select("content_json") \
        .eq("id", project_id) \
        .single() \
        .execute()

    content = res.data.get("content_json")

    if not content:
        content = {}

    current_value = get_value_by_path(content, path)

    # ---- build prompt
    prompt = PROMPT \
        .replace("{{FIELD_PATH}}", path) \
        .replace("{{CURRENT_VALUE}}", str(current_value)) \
        .replace("{{USER_MESSAGE}}", message)

    # ---- call AI
    completion = client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[{"role":"user","content":prompt}],
        temperature=0.2
    )

    ai_text = completion.choices[0].message.content

    update = extract_update(ai_text)

    if not update:
        return jsonify({
            "success": True,
            "reply": ai_text,
            "changes": []
        })

    for change in update.get("changes", []):
        set_value_by_path(content, change["path"], change["value"])

    supabase.table("projects") \
        .update({"content_json": content}) \
        .eq("id", project_id) \
        .execute()

    return jsonify({
        "success": True,
        "reply": ai_text,
        "changes": update["changes"]
    })


# ===============================
# RUN
# ===============================

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8082)
