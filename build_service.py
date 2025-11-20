import os
import re
import traceback
from pathlib import Path
from typing import Optional, Dict, Any

from supabase import create_client

# בסיס הפרויקט
BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_ROOT = BASE_DIR / "sitegyn" / "templates"
OUTPUT_ROOT = BASE_DIR / "output"

# חיבור ל-Supabase (אותם משתנים כמו ב-server.py)
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
    raise RuntimeError("Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY in environment")

supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)


def _resolve_template_path(template_id: str) -> Optional[Path]:
    """
    לוקח selected_template_id כמו 'template_pizza_02'
    ומחפש את הקובץ:
    sitegyn/templates/template_pizza_02/template_pizza_02.html
    """
    if not template_id:
        return None

    folder = template_id
    html_name = f"{template_id}.html"
    candidate = TEMPLATES_ROOT / folder / html_name
    if candidate.exists():
        return candidate

    # אם לא קיים – נחזיר None
    return None


# =========  flatten ל-content_json =========
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


PLACEHOLDER_PATTERN = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}")


def _render_template(html_source: str, project: dict) -> str:
    """
    מאתר אוטומטית את כל ה-{{ key }} בטמפלט,
    ומחליף אותם בערכים מ-content_json ושדות בסיסיים של הפרויקט.
    """

    replacements: Dict[str, str] = {}

    # 1) flatten של content_json (אם יש)
    content_json = project.get("content_json")
    if isinstance(content_json, dict):
        _flatten_content("", content_json, replacements)

    # 2) שדות בסיסיים מה-project
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

    # 3) פונקציית החלפה – רק אם יש ערך למפתח
    def repl(match: re.Match) -> str:
        key = match.group(1).strip()
        if key in replacements:
            return replacements[key]
        return match.group(0)  # משאיר {{ key }} אם אין ערך

    rendered = PLACEHOLDER_PATTERN.sub(repl, html_source)
    return rendered


def run_build_for_project(project_id: str) -> Optional[Path]:
    """
    בונה אתר לפרויקט:
    1. מביא את השורה מטבלת projects
    2. מוצא את הטמפלט לפי selected_template_id (או ברירת מחדל)
    3. קורא את ה-HTML
    4. מזריק תוכן מתוך content_json + שדות הפרויקט
    5. כותב ל-output/<project_id>/index.html
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

        # 2) נזהה את ה-template_id
        template_id = project.get("selected_template_id")

        # fallback: אם אין template_id, ננסה ברירת מחדל לפיצה
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

        # 3) ניצור תיקיית output/<project_id>
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
