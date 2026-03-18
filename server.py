import os
import json
import traceback
import random
from typing import List, Dict, Any
from pathlib import Path

from flask import Flask, request, jsonify, send_from_directory, Response
from dotenv import load_dotenv
from flask_cors import CORS
from supabase import create_client, Client
from openai import OpenAI
from templates_config import TEMPLATES

# === Render On-The-Fly ===
from render_service import render_project_html_by_subdomain

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
# Load Sitegyn system prompt
# ==========================================
PROMPT_PATH = os.path.join(os.path.dirname(__file__), "sitegyn_system_prompt.txt")

with open(PROMPT_PATH, "r", encoding="utf-8") as f:
    SITEGYN_SYSTEM_PROMPT = f.read()

EDITOR_PROMPT_PATH = os.path.join(
    os.path.dirname(__file__),
    "editor_update_prompt.txt"
)

with open(EDITOR_PROMPT_PATH, "r", encoding="utf-8") as f:
    EDITOR_UPDATE_PROMPT = f.read()

IMPROVE_PROMPT_PATH = os.path.join(os.path.dirname(__file__), "content_improve_prompt.txt")

with open(IMPROVE_PROMPT_PATH, "r", encoding="utf-8") as f:
    CONTENT_IMPROVE_PROMPT = f.read()

# ==========================================
# Flask app
# ==========================================
app = Flask(
    __name__,
    static_folder=".",
    static_url_path=""
)
CORS(app)


# ==========================================
# Helpers
# ==========================================
def parse_update_block(assistant_text: str) -> Dict[str, Any]:
    """Extract JSON inside <update>...</update>."""
    try:
        start = assistant_text.find("<update>")
        end = assistant_text.find("</update>")
        if start == -1 or end == -1:
            return {}
        raw = assistant_text[start + len("<update>"): end].strip()
        return json.loads(raw) if raw else {}
    except:
        traceback.print_exc()
        return {}

def get_value_by_path(obj: Dict[str, Any], path: str):
    try:
        curr = obj
        for key in path.split("."):
            curr = curr[key]
        return curr
    except Exception:
        return ""

# ============================
# Template selection
# ============================
def pick_template_for_project(project: Dict[str, Any],
                              update_obj: Dict[str, Any]) -> str | None:

    existing = update_obj.get("selected_template_id") or project.get("selected_template_id")
    if existing:
        return existing

    niche = (update_obj.get("niche") or project.get("niche") or "").strip().lower()
    if not niche:
        return None

    prefix = f"template_{niche}_"
    candidates = [tid for tid in TEMPLATES.keys() if tid.startswith(prefix)]
    if not candidates:
        return None

    return random.choice(candidates)


# ⬇️ כאן להדביק את generate_content_for_project ⬇️
def generate_content_for_project(
    client: OpenAI,
    project_row: Dict[str, Any],
    update_obj: Dict[str, Any],
    template_id: str,
) -> Dict[str, Any] | None:
    """
    Use the generic content_fill_prompt + template schema
    to generate content_json for this project & template.
    אם משהו נכשל בדרך (קובץ חסר / GPT נופל) – נחזיר None
    ולא נשבור את זרימת העדכון ל-projects.
    """
    try:
        template_conf = TEMPLATES.get(template_id)
        if not template_conf:
            return None

        base_dir = Path(__file__).resolve().parent

        # 1) schema של הטמפלט
        schema_path = base_dir / template_conf["schema"]
        schema_str = schema_path.read_text(encoding="utf-8")

        # 2) הפרומפט הכללי (או מה שמוגדר ב-content_prompt)
        content_prompt_path = base_dir / "content_fill_prompt.txt"
        if template_conf.get("content_prompt"):
            content_prompt_path = base_dir / template_conf["content_prompt"]

        prompt_template = content_prompt_path.read_text(encoding="utf-8")

        # 3) BUSINESS_DATA_JSON – מה שיש לנו על הפרויקט + העדכון האחרון
        business_data = {
            "project": project_row,
            "update": update_obj,
        }
        business_data_str = json.dumps(business_data, ensure_ascii=False)

        # 4) מכניסים את ה-schema ואת BUSINESS_DATA לתוך הפרומפט
        final_prompt = (
            prompt_template
            .replace("{{SCHEMA_JSON}}", schema_str)
            .replace("{{BUSINESS_DATA_JSON}}", business_data_str)
        )

        # 5) קריאה שנייה ל-GPT שמחזירה JSON טהור בלבד
        completion = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[{"role": "user", "content": final_prompt}],
            temperature=0.0,
        )
        text = completion.choices[0].message.content.strip()
        content_json = json.loads(text)
        return content_json

    except Exception:
        traceback.print_exc()
        return None

# ==========================================
# ROUTES
# ==========================================

@app.route("/")
def homepage():
    return send_from_directory(".", "index.html")


@app.route("/api/health")
def health():
    return jsonify({"status": "ok"})


@app.route("/api/start_project", methods=["POST"])
def start_project():
    resp = supabase.table("projects").insert({}).execute()
    if not resp.data:
        return jsonify({"error": "insert_failed"}), 500
    return jsonify({"project_id": resp.data[0]["id"]})


# ==========================================
# CHAT — stores history + updates DB
# ==========================================
@app.route("/api/chat", methods=["POST"])
def chat():
    try:

        data = request.get_json(force=True)

        project_id = data.get("project_id")
        user_message = (data.get("message") or "").strip()

        source = data.get("source", "default")  # "editor" | "default"
        field_path = data.get("field_path")  # NEW

        is_editor = source == "editor"

        if not project_id:
            return jsonify({"error": "missing_project_id"}), 400
        if not user_message:
            return jsonify({"error": "empty_message"}), 400

        # Save user message
        if not is_editor:
            supabase.table("chat_messages").insert({
                "project_id": project_id,
                "role": "user",
                "content": user_message,
            }).execute()

        # Load entire history
        history = supabase.table("chat_messages") \
            .select("role, content") \
            .eq("project_id", project_id) \
            .order("created_at", desc=False) \
            .execute().data or []

        # Build messages
        messages = []

        if is_editor:
            project_row = (
                              supabase.table("projects")
                              .select("content_json")
                              .eq("id", project_id)
                              .single()
                              .execute()
                              .data
                          ) or {}

            content_json = project_row.get("content_json") or {}
            current_value = get_value_by_path(content_json, field_path) if field_path else ""

            editor_prompt = (
                EDITOR_UPDATE_PROMPT
                .replace("{{FIELD_PATH}}", field_path or "")
                .replace("{{CURRENT_VALUE}}", json.dumps(current_value, ensure_ascii=False))
                .replace("{{USER_MESSAGE}}", user_message)
            )

            messages.append({
                "role": "system",
                "content": editor_prompt
            })
        else:
            messages.append({
                "role": "system",
                "content": SITEGYN_SYSTEM_PROMPT
            })

        if not is_editor:
            for row in history:
                messages.append({"role": row["role"], "content": row["content"]})

        messages.append({"role": "user", "content": user_message})

        # OpenAI call
        completion = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=messages,
            temperature=0.0 if is_editor else 0.5,
        )

        assistant_text = completion.choices[0].message.content or ""
        print("FIELD PATH:", field_path)
        print("USER MESSAGE:", user_message)
        print("ASSISTANT TEXT:", assistant_text)
        print("====== AI RESPONSE ======")
        print(assistant_text)
        print("=========================")
        editor_payload = None

        if is_editor:
            editor_payload = parse_update_block(assistant_text)

            if editor_payload:
                visible_text = assistant_text
            else:
                visible_text = "⚠️ Failed to update content."
        else:
            # save assistant message only for non-editor chat
            supabase.table("chat_messages").insert({
                "project_id": project_id,
                "role": "assistant",
                "content": assistant_text,
            }).execute()

            # show assistant text (without <update>)
            visible_text = assistant_text
            if "<update>" in assistant_text and "</update>" in assistant_text:
                before = assistant_text.split("<update>")[0]
                after = assistant_text.split("</update>")[-1]
                visible_text = (before + after).strip()



        # Parse <update> block מהתשובה הראשונה
        update_obj = parse_update_block(assistant_text)
        # ==========================================
        # INITIAL BUILD (missing piece)
        # ==========================================
        project_row = (
            supabase.table("projects")
            .select("*")
            .eq("id", project_id)
            .single()
            .execute()
            .data
        )

        # אם אין עדיין תוכן – נבנה
        if update_obj:

            # ===============================
            # 1. Template selection
            # ===============================
            template_id = pick_template_for_project(project_row, update_obj)

            if template_id and not update_obj.get("selected_template_id"):
                update_obj["selected_template_id"] = template_id

            # ===============================
            # 2. Generate content_json
            # ===============================
            if template_id and not (project_row.get("content_json") or update_obj.get("content_json")):

                content_json = generate_content_for_project(
                    client,
                    project_row,
                    update_obj,
                    template_id
                )

                if content_json:
                    update_obj["content_json"] = content_json

            # ===============================
            # 3. Create subdomain (🔥 חשוב)
            # ===============================
            if not project_row.get("subdomain"):
                update_obj["subdomain"] = f"site-{project_id[:6]}"

            # ===============================
            # 4. Save everything together
            # ===============================
            supabase.table("projects").update(update_obj).eq("id", project_id).execute()


        # אם המודל לא החזיר בכלל <update>...</update> – נעשה קריאה שנייה "נסתרת"
        if not update_obj:
            try:
                backend_messages = messages + [
                    {
                        "role": "system",
                        "content": (
                            "Your previous reply did not follow the instructions. "
                            "Now respond ONLY with a single <update>{...}</update> block "
                            "containing valid JSON for the current project. "
                            "Do not add any natural language or explanation."
                        ),
                    }
                ]
                completion2 = client.chat.completions.create(
                    model="gpt-4.1-mini",
                    messages=backend_messages,
                    temperature=0.0,
                )
                backend_text = completion2.choices[0].message.content or ""
                update_obj = parse_update_block(backend_text)

                if is_editor:
                    editor_payload = update_obj
            except Exception:
                traceback.print_exc()
                update_obj = {}
        # ===== Editor content patch =====
        if is_editor and editor_payload:
            project_row = (
                supabase.table("projects")
                .select("content_json")
                .eq("id", project_id)
                .single()
                .execute()
                .data
            )

            content = project_row.get("content_json") or {}

            updates = {}

            if "content_json" in editor_payload:
                updates = editor_payload["content_json"]

            elif "changes" in editor_payload:
                for change in editor_payload["changes"]:
                    updates[change["path"]] = change["value"]

            for path, value in updates.items():

                keys = path.split(".")
                curr = content

                for k in keys[:-1]:
                    curr = curr.setdefault(k, {})

                curr[keys[-1]] = value

            supabase.table("projects").update({
                "content_json": content
            }).eq("id", project_id).execute()

            # ensure subdomain exists
            if not project_row.get("subdomain"):
                sub = f"site-{project_id[:6]}"

                supabase.table("projects").update({
                    "subdomain": sub
                }).eq("id", project_id).execute()

        final_message = visible_text

        # אם זה עדכון (יש update או editor)
        if update_obj or editor_payload:
            final_message = "Content updated"

        return jsonify({
            "reply": final_message,
            "project_id": project_id
        })
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


# ==========================================
# PUBLIC SITE — on-the-fly render (NEW)
# ==========================================
@app.route("/p/<subdomain>")
def public_page_by_subdomain(subdomain: str):
    html = render_project_html_by_subdomain(subdomain)
    if html is None:
        return "Project not found or failed to render", 404
    return Response(html, mimetype="text/html")

@app.route("/p/<subdomain>/wow")
def public_page_wow(subdomain: str):
    project = (
        supabase.table("projects")
        .select("id, wow_seen")
        .eq("subdomain", subdomain)
        .single()
        .execute()
        .data
    )

    if not project:
        return "Project not found", 404

    # אם כבר נצפה – מעבר לאתר הרגיל
    if project.get("wow_seen"):
        return Response(
            "", status=302,
            headers={"Location": f"/p/{subdomain}"}
        )

    # 🔥 כאן הסימון
    supabase.table("projects") \
        .update({"wow_seen": True}) \
        .eq("subdomain", subdomain) \
        .execute()

    html = render_project_html_by_subdomain(subdomain)
    if html is None:
        return "Project not found or failed to render", 404

    return Response(html, mimetype="text/html")


# ==========================================
# Admin
# ==========================================
@app.route("/api/projects", methods=["GET"])
def api_list_projects():
    try:
        rows = supabase.table("projects") \
            .select("id, business_name, business_type, subdomain, created_at") \
            .order("created_at", desc=True) \
            .limit(100) \
            .execute().data or []

        return jsonify({"status": "ok", "projects": rows})
    except:
        traceback.print_exc()
        return jsonify({"status": "error"}), 500

@app.route("/api/projects/<project_id>/wow_seen", methods=["POST"])
def mark_wow_seen(project_id):
    supabase.table("projects") \
        .update({"wow_seen": True}) \
        .eq("id", project_id) \
        .execute()

    return jsonify({"ok": True})

@app.route("/api/projects/<project_id>/subdomain", methods=["POST"])
def api_update_subdomain(project_id):
    try:
        payload = request.get_json() or {}
        sub = (payload.get("subdomain") or "").strip().lower()

        if not sub:
            return jsonify({"status": "error", "message": "subdomain required"}), 400

        # --- Auto-resolve subdomain conflicts ---
        base = sub
        candidate = base
        counter = 1

        while True:
            conflict = supabase.table("projects") \
                .select("id") \
                .eq("subdomain", candidate) \
                .neq("id", project_id) \
                .execute().data

            if not conflict:
                break  # פנוי

            candidate = f"{base}-{counter}"
            counter += 1

        # candidate = subdomain הסופי והפנוי

        updated = supabase.table("projects") \
            .update({"subdomain": candidate}) \
            .eq("id", project_id) \
            .execute().data

        return jsonify({"status": "ok", "subdomain": candidate, "project": updated})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# ==========================================
# Run server
# ==========================================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8000")), debug=True)
