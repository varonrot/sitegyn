import os
import json
import re

from flask import Flask, request, jsonify
from dotenv import load_dotenv
from supabase import create_client
from openai import OpenAI

# =========================
# ENV
# =========================

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
OPENAI_KEY = os.getenv("OPENAI_API_KEY")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
client = OpenAI(api_key=OPENAI_KEY)

# =========================
# APP
# =========================

app = Flask(__name__)

# =========================
# LOAD PROMPT
# =========================

with open("editor_update_prompt.txt","r") as f:
    PROMPT_TEMPLATE = f.read()


# =========================
# HELPER
# =========================

def set_value_by_path(data, path, value):

    parts = path.split(".")
    obj = data

    for p in parts[:-1]:

        if p not in obj:
            obj[p] = {}

        obj = obj[p]

    obj[parts[-1]] = value


# =========================
# PARSE UPDATE BLOCK
# =========================

def extract_update_block(text):

    match = re.search(r"<update>(.*?)</update>", text, re.S)

    if not match:
        return None

    try:
        return json.loads(match.group(1))
    except:
        return None


# =========================
# API
# =========================

@app.route("/api/editor-update", methods=["POST"])
def editor_update():

    data = request.json

    project_id = data["project_id"]
    field_path = data["path"]
    user_message = data["message"]

    # =========================
    # LOAD PROJECT
    # =========================

    res = supabase.table("projects") \
        .select("content_json") \
        .eq("id", project_id) \
        .single() \
        .execute()

    content = res.data["content_json"]

    # =========================
    # CURRENT VALUE
    # =========================

    current = content

    for p in field_path.split("."):
        current = current.get(p, "")

    # =========================
    # BUILD PROMPT
    # =========================

    prompt = PROMPT_TEMPLATE \
        .replace("{{FIELD_PATH}}", field_path) \
        .replace("{{CURRENT_VALUE}}", str(current)) \
        .replace("{{USER_MESSAGE}}", user_message)

    # =========================
    # OPENAI
    # =========================

    completion = client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[{"role":"user","content":prompt}],
        temperature=0.2
    )

    text = completion.choices[0].message.content

    update = extract_update_block(text)

    if not update:
        return jsonify({"error":"Invalid AI response"}),500

    # =========================
    # APPLY CHANGES
    # =========================

    for change in update["changes"]:

        set_value_by_path(
            content,
            change["path"],
            change["value"]
        )

    # =========================
    # SAVE
    # =========================

    supabase.table("projects") \
        .update({"content_json":content}) \
        .eq("id", project_id) \
        .execute()

    return jsonify({
        "success":True,
        "changes":update["changes"]
    })


# =========================
# RUN
# =========================

if __name__ == "__main__":
    app.run(port=8082)
