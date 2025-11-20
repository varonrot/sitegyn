# build_service.py
import os
import traceback
from pathlib import Path
from typing import Optional

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


def run_build_for_project(project_id: str) -> Optional[Path]:
    """
    בונה אתר לפרויקט:
    1. מביא את השורה מטבלת projects
    2. מוצא את הטמפלט לפי selected_template_id
    3. מעתיק את ה-HTML לתיקיית output/<project_id>/index.html
    4. מחזיר את הנתיב המלא אם הצליח, אחרת None
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
            print(f"[build_service] project {project_id} has no selected_template_id, using default template_pizza_02")
            template_id = "template_pizza_02"

        template_path = _resolve_template_path(template_id)
        if not template_path:
            print(f"[build_service] template file not found for template_id={template_id}")
            return None

        # 3) ניצור תיקיית output/<project_id>
        out_dir = OUTPUT_ROOT / project_id
        out_dir.mkdir(parents=True, exist_ok=True)

        out_path = out_dir / "index.html"

        # 4) לעת עתה – פשוט מעתיקים את ה-HTML כמו שהוא
        html_source = template_path.read_text(encoding="utf-8")
        out_path.write_text(html_source, encoding="utf-8")

        print(f"[build_service] built site for project {project_id} at {out_path}")
        return out_path

    except Exception:
        traceback.print_exc()
        return None
