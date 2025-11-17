import os
import json
from pathlib import Path
from typing import Any, Dict, Optional

from supabase import create_client, Client
from dotenv import load_dotenv

# ==========================================
# Supabase setup
# ==========================================
load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")


def get_supabase() -> Client:
    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        raise RuntimeError("Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY env vars")
    return create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)


# ==========================================
# Helpers
# ==========================================

BASE_DIR = Path(__file__).resolve().parent  # PyCharmMiscProject
TEMPLATES_DIR = BASE_DIR / "templates"      # בתוך הפרויקט
BUILDS_DIR = BASE_DIR / "output"           # נשתמש בתיקיית output הקיימת


def _get_from_content_json(content: Dict[str, Any], path: str) -> str:
    """
    מקבל dict של content_json ומסלול נקודות כמו 'home.hero.title'
    ומחזיר את הערך או מחרוזת ריקה אם חסר.
    """
    current: Any = content
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return ""
        current = current[part]
    return "" if current is None else str(current)


def _load_project(project_id: str) -> Optional[Dict[str, Any]]:
    """טוען פרויקט מסופבייס. מחזיר dict או None אם אין/יש שגיאה."""
    supabase = get_supabase()

    try:
        resp = (
            supabase.table("projects")
            .select("*")
            .eq("id", project_id)
            .single()   # אם אין רשומה זה יזרוק – אנחנו תופסים ב־except
            .execute()
        )
    except Exception as e:
        print(f"[build] error loading project {project_id}: {e}")
        return None

    data = getattr(resp, "data", None)
    if not data:
        print(f"[build] no project found for id {project_id}")
        return None

    return data


# ==========================================
# Public API
# ==========================================

def run_build_for_project(project_id: str) -> Optional[Path]:
    """
    בונה אתר עבור project_id נתון:
    1. טוען את הפרויקט מסופבייס
    2. טוען את קובץ ה-HTML של הטמפלט + mapping
    3. ממלא placeholders מתוך content_json
    4. שומר index.html ב-builds/{project_id}/index.html
    5. מעדכן website_status ל-'partial' אם היה initial/NULL

    מחזיר את הנתיב לקובץ ה-HTML שנוצר, או None אם הבנייה נכשלה.
    """
    project = _load_project(project_id)
    if project is None:
        print(f"[build] project not found: {project_id}")
        return None

    selected_template_id = project.get("selected_template_id")
    content_json = project.get("content_json") or {}

    if not selected_template_id:
        print(f"[build] project {project_id} has no selected_template_id")
        return None

    if not isinstance(content_json, dict):
        print(f"[build] project {project_id} has invalid content_json")
        return None

    # -------------------------------
    # מסלולי קבצים של הטמפלט
    # -------------------------------
    template_dir = TEMPLATES_DIR / selected_template_id
    html_path = template_dir / f"{selected_template_id}.html"
    mapping_path = template_dir / f"{selected_template_id}_mapping.json"

    if not html_path.exists():
        print(f"[build] template html not found: {html_path}")
        return None

    if not mapping_path.exists():
        print(f"[build] mapping file not found: {mapping_path}")
        return None

    # -------------------------------
    # טעינת mapping + html
    # -------------------------------
    try:
        with mapping_path.open("r", encoding="utf-8") as f:
            mapping: Dict[str, str] = json.load(f)
    except Exception as e:
        print(f"[build] failed to load mapping: {e}")
        return None

    try:
        html = html_path.read_text(encoding="utf-8")
    except Exception as e:
        print(f"[build] failed to read html template: {e}")
        return None

    # -------------------------------
    # בניית מילון החלפות מ-content_json
    # -------------------------------
    replacements: Dict[str, str] = {}
    for placeholder_key, json_path in mapping.items():
        value = _get_from_content_json(content_json, json_path)
        replacements[placeholder_key] = value

    # -------------------------------
    # החלפת placeholders
    # -------------------------------
    filled_html = html
    for key, value in replacements.items():
        # תבנית: {{ key }} – עם רווחים
        filled_html = filled_html.replace(f"{{{{ {key} }}}}", value)
        # למקרה שבטעות נשתמש ללא רווחים: {{key}}
        filled_html = filled_html.replace(f"{{{{{key}}}}}", value)

    # -------------------------------
    # שמירת הקובץ שנבנה
    # -------------------------------
    output_dir = BUILDS_DIR / project_id
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "index.html"

    try:
        output_path.write_text(filled_html, encoding="utf-8")
    except Exception as e:
        print(f"[build] failed to write output html: {e}")
        return None

    print(f"[build] built site for project {project_id}: {output_path}")

    # -------------------------------
    # עדכון סטטוס האתר (partial)
    # -------------------------------
    current_status = project.get("website_status")
    if current_status in (None, "", "initial"):
        try:
            supabase = get_supabase()
            supabase.table("projects").update(
                {"website_status": "partial"}
            ).eq("id", project_id).execute()
        except Exception as e:
            print(f"[build] failed to update website_status: {e}")

    return output_path


# אפשר להריץ מקומית לבדיקה:
if __name__ == "__main__":
    test_project_id = os.getenv("TEST_PROJECT_ID", "").strip()
    if not test_project_id:
        print("Set TEST_PROJECT_ID in env to test build.")
    else:
        run_build_for_project(test_project_id)
