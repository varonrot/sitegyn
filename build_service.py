# build_service.py
import os
import re
import traceback
from pathlib import Path
from typing import Optional, Dict, Any

from supabase import create_client

# ========================================
#  בסיס הפרויקט
# ========================================
BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_ROOT = BASE_DIR / "sitegyn" / "templates"
OUTPUT_ROOT = BASE_DIR / "output"

# ========================================
#  חיבור ל-Supabase (אותם משתנים כמו ב-server.py)
# ========================================
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
    raise RuntimeError("Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY in environment")

supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)


# ========================================
#  עזר: מציאת קובץ הטמפלט לפי selected_template_id
# ========================================
def _resolve_template_path(template_id: str) -> Optional[Path]:
    """
    מקבל selected_template_id כמו 'template_pizza_02'
    ומחזיר:
    sitegyn/templates/template_pizza_02/template_pizza_02.html
    אם הקובץ לא קיים – מחזיר None.
    """
    if not template_id:
        return None

    folder = template_id
    html_name = f"{template_id}.html"
    candidate = TEMPLATES_ROOT / folder / html_name
    if candidate.exists():
        return candidate

    return None


# ========================================
#  עזר: flatten ל-content_json
# ========================================
def _flatten_content(prefix: str, value: Any, out: Dict[str, str]) -> None:
    """
    ממיר content_json מקונן למילון שטוח:
    {"home": {"hero": {"title": "X"}}}
    --> {"home_hero_title": "X"}
    """
    if isinstance(value, dict):
        for k, v in value.items():
            new_prefix = f"{prefix}_{k}" if prefix else k
            _flatten_content(new_prefix, v, out)
    else:
        if prefix:
            out[prefix] = str(value)


# ========================================
#  עזר: הזרקת תוכן לפי {{ placeholders }}
# ========================================
PLACEHOLDER_PATTERN = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}")

def _render_template(html_source: str, project: dict) -> str:
    """
    מקבל HTML גולמי + רשומת project,
    מאתר אוטומטית את כל ה-{{ key }} בטמפלט,
    ומחליף אותם בערכים מ-content_json ושדות בסיסיים של הפרויקט.
    """

    # 1) נבנה מילון ערכים מכל מה שיש לנו
    replacements: Dict[str, str] = {}

    # 1a) flatten של content_json
    content_json = project.get("content_json")
    if isinstance(content_json, dict):
        _flatten_content("", content_json, replacements)

    # 1b) שדות בסיסיים מה-project
    base_fields = [
        "business_name",
        "business_type",
        "niche",
        "city",
        "country",
        "main_goal",
        "primary_color",
        "subdomain",
    ]
    for key in base_fields:
        val = project.get(key)
        if val is not None:
            replacements[key] = str(val)

    # 2) פונקציית החלפה – מחליפה רק placeholders שיש לנו עבורם ערך
    def repl(match: re.Match) -> str:
        key = match.group(1).strip()
        if key in replacements:
            return replacements[key]
        # אם אין ערך – נשאיר את ה-placeholder כמו שהוא
        return match.group(0)

    # 3) נבצע החלפה בכל הטקסט
    rendered = PLACEHOLDER_PATTERN.sub(repl, html_source)
    return rendered


# ========================================
#  פונקציית build עיקרית
# ========================================
def run_build_for_project(project_id: str) -> Optional[Path]:
    """
    בונה אתר לפרויקט:
    1. מושך את הפרויקט מטבלת projects
    2. מוצא טמפלט לפי selected_template_id (או ברירת מחדל)
    3. קורא את ה-HTML של הטמפלט
    4. מזריק אליו תוכן מתוך content_json + שדות הפרויקט
    5. שומר ל-output/<project_id>/index.html
    """
    try:
        # 1) נביא את הפרויקט מ-Supabase
        resp = (
            supabase.table("projects")
            .select("*")
            .eq("id", project_id)
            .limit(1)
            .execute()
        )
        rows = getattr(resp, "data", []) or []
        if not rows:
            print(f"[build_service] project {project_id} not found")
            return None

        project = rows[0]

        # 2) נזהה template_id
        template_id = project.get("selected_template_id")

        # fallback: אם אין – נבחר תבנית פיצה בסיסית
        if not template_id:
            print(
                f"[build_service] project {project_id} has no selected_template_id, "
                "using default template_pizza_02"
            )
            template_id = "template_pizza_02"

        template_path = _resolve_template_path(template_id)
        if not template_path:
            print(f"[build_service] template file not found for template_id={template_id}")
            return None

        # 3) נכין תיקיית output/<project_id>
        out_dir = OUTPUT_ROOT / project_id
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / "index.html"

        # 4) נקרא HTML, נזריק תוכן, ונשמור
        html_source = template_path.read_text(encoding="utf-8")
        rendered_html = _render_template(html_source, project)
        out_path.write_text(rendered_html, encoding="utf-8")

        print(f"[build_service] built site for project {project_id} at {out_path}")
        return out_path

    except Exception:
        traceback.print_exc()
        return None
