import os
import json
import traceback
import random  # ← להוסיף
from typing import List, Dict, Any
from pathlib import Path

from flask import Flask, request, jsonify, send_from_directory, send_file
from dotenv import load_dotenv
from flask_cors import CORS
from supabase import create_client, Client
from openai import OpenAI
from config.templates_config import TEMPLATES


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
# Load Sitegyn system prompt from file
# ==========================================
PROMPT_PATH = os.path.join(os.path.dirname(__file__), "sitegyn_system_prompt.txt")

try:
    with open(PROMPT_PATH, "r", encoding="utf-8") as f:
        SITEGYN_SYSTEM_PROMPT = f.read()
except FileNotFoundError:
    raise RuntimeError(f"sitegyn_system_prompt.txt not found at {PROMPT_PATH}")

# ==========================================
# Flask app
# ==========================================
app = Flask(__name__)
CORS(app)


# ------------------------------------------
# Helpers
# ------------------------------------------
def parse_update_block(assistant_text: str) -> Dict[str, Any]:
    """
    Extract the JSON object from inside <update>...</update> in the assistant's reply.
    If nothing is found or parsing fails, return {}.
    """
    try:
        start_tag = "<update>"
        end_tag = "</update>"
        start_idx = assistant_text.find(start_tag)
        end_idx = assistant_text.find(end_tag)

        if start_idx == -1 or end_idx == -1:
            return {}

        json_str = assistant_text[start_idx + len(start_tag):end_idx].strip()
        if not json_str:
            return {}

        return json.loads(json_str)
    except Exception:
        # If anything goes wrong, we don't want to crash the whole chat.
        traceback.print_exc()
        return {}


# ==========================================
# Routes
# ==========================================

@app.route("/admin")
def admin_page():
    return send_from_directory(".", "admin.html")


@app.route("/api/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


@app.route("/api/start_project", methods=["POST"])
def start_project():
    """
    Create a new project row and return project_id.
    """
    try:
        # Create empty project row with generated UUID
        resp = supabase.table("projects").insert({}).execute()
        if not resp.data:
            return jsonify({"error": "insert_failed"}), 500

        project_id = resp.data[0]["id"]
        return jsonify({"project_id": project_id})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

# ============================
# Template selection by niche
# ============================

def pick_template_for_project(project: Dict[str, Any],
                              update_obj: Dict[str, Any]) -> str | None:
    """
    אם כבר יש selected_template_id – מחזיר אותו.
    אחרת בוחר טמפלט רנדומלי לפי הנישה שהצ'ט מחזיר (pizza / hair_salon / photography).
    """

    # אם כבר יש טמפלט מוגדר (בעדכון הנוכחי או בפרויקט) – לא נוגעים
    existing = (
        update_obj.get("selected_template_id")
        or project.get("selected_template_id")
    )
    if existing:
        return existing

    # הנישה מגיעה מהצ'ט כמילה אחת לפי ה-system_prompt (pizza, hair_salon, photography)
    niche = (update_obj.get("niche") or project.get("niche") or "").strip().lower()
    if not niche:
        return None

    # מחפשים כל טמפלט שה-id שלו מתחיל ב-template_<niche>_
    prefix = f"template_{niche}_"
    candidates = [tid for tid in TEMPLATES.keys() if tid.startswith(prefix)]

    if not candidates:
        return None

    # בחירה רנדומלית מתוך כל הטמפלטים שמתאימים לנישה
    return random.choice(candidates)

@app.route("/api/chat", methods=["POST"])
def chat():
    """
    Handle a chat turn:
    - Save user message to chat_messages
    - Load full history for this project
    - Send to OpenAI with several system prompts (main + project_id + user_turns logic)
    - Save assistant reply
    - Parse <update> JSON (if exists) and update projects row
    """
    try:
        data = request.get_json(force=True)
        project_id = data.get("project_id")
        user_message = data.get("message", "").strip()

        if not project_id:
            return jsonify({"error": "missing_project_id"}), 400
        if not user_message:
            return jsonify({"error": "empty_message"}), 400

        # 1) Save user message in chat_messages
        supabase.table("chat_messages").insert({
            "project_id": project_id,
            "role": "user",
            "content": user_message,
            "status": "complete"
        }).execute()

        # 2) Load full history for this project
        history_resp = (
            supabase.table("chat_messages")
            .select("role, content")
            .eq("project_id", project_id)
            .order("created_at", desc=False)
            .execute()
        )
        history_rows = history_resp.data or []

        # Count how many user turns we already have
        user_turns = sum(1 for row in history_rows if row.get("role") == "user")

        # 3) Build messages for OpenAI
        messages: List[Dict[str, str]] = []

        # Main system prompt from file
        messages.append({
            "role": "system",
            "content": SITEGYN_SYSTEM_PROMPT
        })

        # System: project_id
        messages.append({
            "role": "system",
            "content": f"The current project_id is {project_id}. Never mention this ID to the user."
        })

        # System: dynamic rule about offering a draft after 2 answers
        messages.append({
            "role": "system",
            "content": (
                f"For this project there have been {user_turns} user answers so far. "
                "After 2 or more user answers, if you have not yet explicitly asked the user "
                "whether they want to see an initial website demo or continue the interview, "
                "you MUST offer that choice in your next reply and you MUST NOT ask additional "
                "business questions in the same message. If they say they want to see a demo, "
                "respond conversationally as instructed, but still include the <update> JSON."
            ),
        })

        # Conversation history (user + assistant)
        for row in history_rows:
            role = row.get("role")
            content = row.get("content", "")
            if role not in ("user", "assistant"):
                continue
            messages.append({"role": role, "content": content})

        # Add the latest user message again as the last turn
        # (it is also in history, but we can ensure it's present; duplication is harmless
        # for short histories, but if you want, you can skip this line.)
        # messages.append({"role": "user", "content": user_message})

        # 4) Call OpenAI
        completion = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=messages,
            temperature=0.5,
        )

        # הטקסט המלא מהמודל (כולל <update> ... </update>)
        assistant_text = completion.choices[0].message.content or ""

        # גרסה נקייה למשתמש – בלי בלוק ה-<update> JSON
        assistant_visible = assistant_text
        start_tag = "<update>"
        end_tag = "</update>"
        start_idx = assistant_visible.find(start_tag)
        end_idx = assistant_visible.find(end_tag)
        if start_idx != -1 and end_idx != -1:
            assistant_visible = (
                    assistant_visible[:start_idx] +
                    assistant_visible[end_idx + len(end_tag):]
            ).strip()

        # 5) Save assistant message
        tokens_used = completion.usage.total_tokens if completion.usage else None

        supabase.table("chat_messages").insert({
            "project_id": project_id,
            "role": "assistant",
            "content": assistant_text,
            "tokens_used": tokens_used,
            "status": "complete"
        }).execute()

        # 6) Parse <update> JSON and update projects table (if present)
        update_obj = parse_update_block(assistant_text)
        if update_obj:
            try:
                # נטען את הפרויקט הקיים כדי לדעת מה ה-niche והאם כבר קיים טמפלט
                proj_resp = (
                    supabase.table("projects")
                    .select("*")
                    .eq("id", project_id)
                    .execute()
                )
                proj_rows = getattr(proj_resp, "data", []) or []
                project = proj_rows[0] if proj_rows else {}

                # אם עדיין אין selected_template_id – נבחר אחד לפי הנישה
                template_id = pick_template_for_project(project, update_obj)
                if template_id and not update_obj.get("selected_template_id"):
                    update_obj["selected_template_id"] = template_id

                # עדכון הפרויקט
                supabase.table("projects").update(update_obj).eq("id", project_id).execute()
            except Exception:
                traceback.print_exc()

        # 7) Return assistant reply
        return jsonify({
            "reply": assistant_visible,
            "project_id": project_id,
        })

    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": "chat_failed", "details": str(e)}), 500

from build_service import run_build_for_project  # שורה קיימת

# ---------- NEW: שרת שמחזיר את האתר שנבנה ----------

@app.route("/site/<project_id>")
def serve_site(project_id: str):
    """
    מחזיר את ה-HTML שנבנה עבור project_id מסוים,
    מתוך התיקייה output/<project_id>/index.html
    """
    output_path = Path("output") / project_id / "index.html"

    if not output_path.exists():
        return jsonify({
            "status": "error",
            "message": "site not built yet for this project_id"
        }), 404

    return send_file(str(output_path), mimetype="text/html")

# ---------- API לבנייה (כבר יש, משאירים כמו שהוא) ----------

@app.route("/api/build/<project_id>", methods=["GET", "POST"])
def api_build_project(project_id: str):
    output_path = run_build_for_project(project_id)

    if output_path is None:
        return jsonify({
            "status": "error",
            "message": "build failed",
        }), 400

    return jsonify({
        "status": "ok",
        "project_id": project_id,
        "output_path": str(output_path),
    })

@app.route("/api/projects", methods=["GET"])
def api_list_projects():
    """
    מחזיר רשימת פרויקטים לניהול (id + שם + subdomain).
    """
    try:
        resp = (
            supabase.table("projects")
            .select("id, business_name, business_type, subdomain, created_at")
            .order("created_at", desc=True)
            .limit(100)
            .execute()
        )
        data = getattr(resp, "data", []) or []
        return jsonify({"status": "ok", "projects": data})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/projects/<project_id>/subdomain", methods=["POST"])
def api_update_subdomain(project_id):
    """
    מעדכן subdomain לפרויקט מסוים, ואפשר אחר כך לקרוא גם ל־/api/build/<project_id>.
    """
    try:
        payload = request.get_json() or {}
        subdomain = (payload.get("subdomain") or "").strip().lower()

        if not subdomain:
            return jsonify({"status": "error", "message": "subdomain is required"}), 400

        # עדכון בטבלה
        resp = (
            supabase.table("projects")
            .update({"subdomain": subdomain})
            .eq("id", project_id)
            .execute()
        )
        updated = getattr(resp, "data", []) or []

        return jsonify({
            "status": "ok",
            "project_id": project_id,
            "subdomain": subdomain,
            "project": updated,
        })
    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8000")), debug=True)
