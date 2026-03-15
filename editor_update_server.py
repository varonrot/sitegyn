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


def get_value_by_path(obj, path):
    for p in path.split("."):
        obj = obj.get(p, "")
    return obj


def set_value_by_path(obj, path, value):
    keys = path.split(".")
    cur = obj
    for k in keys[:-1]:
        if k not in cur:
            cur[k] = {}
        cur = cur[k]
    cur[keys[-1]] = value


def extract_update(text):
    m = re.search(r"<update>(.*?)</update>", text, re.S)
    if not m:
        return None
    return json.loads(m.group(1))


@app.route("/api/editor-update", methods=["POST"])
def editor_update():

    body = request.json

    project_id = body["project_id"]
    path = body["path"]
    message = body["message"]

    res = supabase.table("projects") \
        .select("content_json") \
        .eq("id", project_id) \
        .single() \
        .execute()

    content = res.data["content_json"]

    current_value = get_value_by_path(content, path)

    prompt = PROMPT \
        .replace("{{FIELD_PATH}}", path) \
        .replace("{{CURRENT_VALUE}}", str(current_value)) \
        .replace("{{USER_MESSAGE}}", message)

    completion = client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[{"role":"user","content":prompt}],
        temperature=0.2
    )

    text = completion.choices[0].message.content

    update = extract_update(text)

    if not update:
        return jsonify({"error":"invalid AI response"}),500

    for change in update["changes"]:
        set_value_by_path(content, change["path"], change["value"])

    supabase.table("projects") \
        .update({"content_json":content}) \
        .eq("id", project_id) \
        .execute()

    return jsonify({
        "success":True,
        "changes":update["changes"]
    })


if __name__ == "__main__":
    app.run(port=8082)
