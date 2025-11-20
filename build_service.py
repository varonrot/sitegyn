import os
import re
import json
import traceback
from pathlib import Path
from typing import Optional, Dict, Any

from supabase import create_client

# בסיס הפרויקט
BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_ROOT = BASE_DIR / "sitegyn" / "templates"
OUTPUT_ROOT = BASE_DIR / "output"

# חיבור ל-Supabase (אותו כמו ב-server.py)
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
    raise RuntimeError("Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY in environment")

supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)

PLACEHOLDER_PATTERN = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}")


def _resolve_template_path(template_id: str) -> Optional[Path]:
    """
    לדוגמה: template_id = 'template_pizza_02'
    נתיב: sitegyn/templates/template_pizza_02/template_pizza_02.html
    """
    if not template_id:
        return None

    folder = template_id
    html_name = f"{template_id}.html"
    candidate = TEMPLATES_ROOT / folder / html_name
    if candidate.exists():
        return candidate

    return None


def _load_template_mapping(template_id: str) -> Dict[str, Any]:
    """
    קורא את template_pizza_02_mapping.json אם קיים.
    """
    folder = template_id
    mapping_name = f"{template_id}_mapping.json"
    path = TEMPLATES_ROOT / folder / mapping_name
    if not path.exists():
        return {}

    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        traceback.print_exc()
        return {}


def _safe_get(d: Dict[str, Any], key: str) -> Optional[str]:
    val = d.get(key)
    if isinstance(val, (str, int, float)):
        return str(val)
    return None


def _render_template(html_source: str, project: dict, mapping: Dict[str, Any]) -> str:
    """
    מאתר את כל ה-{{ key }} ב-HTML ומחליף:
    1. קודם מתוך content_json[key] אם קיים
    2. אחרת מתוך mapping[key] עם .format(**ctx)
    3. אחרת מנסה ctx[key] (business_name וכו')
    4. אחרת משאיר את ה-placeholder כמו שהוא
    """

    # content_json מהפרויקט
    content_json = project.get("content_json") or {}
    if not isinstance(content_json, dict):
        content_json = {}

    # קונטקסט להחלפת {business_name} וכו' בתוך mapping
    ctx: Dict[str, Any] = {
        "business_name": project.get("business_name") or "",
        "business_type": project.get("business_type") or "",
        "niche": project.get("niche") or "",
        "city": project.get("city") or "",
        "country": project.get("country") or "",
    }

    # opening_hours – אם יש ב-content_json נשתמש בו, אחרת ברירת מחדל
    oh = content_json.get("opening_hours")
    if isinstance(oh, str) and oh.strip():
        ctx["opening_hours"] = oh
    else:
        ctx["opening_hours"] = "Open daily: 11:00–23:00"

    # נשתמש בקאש כדי שלא נחשב אותו מפתח כמה פעמים
    cache: Dict[str, Optional[str]] = {}

    def resolve_key(key: str) -> Optional[str]:
        if key in cache:
            return cache[key]

        # 1) קודם מתוך content_json
        v = _safe_get(content_json, key)
        if v is not None:
            cache[key] = v
            return v

        # 2) מתוך mapping.json, כולל format על {business_name} וכו'
        if mapping and key in mapping:
            raw = mapping[key]
            if isinstance(raw, str):
                try:
                    formatted = raw.format(**ctx)
                except Exception:
                    formatted = raw  # אם חסר מפתח ב-ctx, נשאיר כמו שהוא
                cache[key] = formatted
                return formatted

        # 3) אם יש ctx עם אותו שם מפתח (למשל business_name)
        if key in ctx and ctx[key]:
            cache[key] = str(ctx[key])
            return cache[key]

        cache[key] = None
        return None

    def repl(match: re.Match) -> str:
        key = match.group(1).strip()
        value = resolve_key(key)
        if value is None:
            # אין ערך – נשאיר placeholder כדי שיהיה ברור שחסר
            return match.group(0)
        return value

    rendered = PLACEHOLDER_PATTERN.sub(repl, html_source)
    return rendered


def run_build_for_project(project_id: str) -> Optional[Path]:
    """
    בונה אתר לפרויקט:
      1. מושך את ה-project מ-Supabase
      2. מוצא template_id
      3. קורא HTML + mapping.json
      4. מזריק תוכן
      5. כותב ל-output/<project_id>/index.html
    """
    try:
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

        template_id = project.get("selected_template_id")
        if not template_id:
            # ברירת מחדל לפיצה אם אין כלום
            template_id = "template_pizza_02"

        template_path = _resolve_template_path(template_id)
        if not template_path:
            print(f"[build_service] template file not found for template_id={template_id}")
            return None

        mapping = _load_template_mapping(template_id)

        # ניצור תיקיית output/<project_id>
        out_dir = OUTPUT_ROOT / project_id
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / "index.html"

        # קריאה + רנדר
        html_source = template_path.read_text(encoding="utf-8")
        rendered_html = _render_template(html_source, project, mapping)
        out_path.write_text(rendered_html, encoding="utf-8")

        print(f"[build_service] built site for project {project_id} at {out_path}")
        return out_path

    except Exception:
        traceback.print_exc()
        return None


if __name__ == "__main__":
    # לדוגמה לבדיקה ידנית – תחליף ל-ID שקיים אצלך
    test_project_id = os.getenv("TEST_PROJECT_ID")
    if test_project_id:
        run_build_for_project(test_project_id)
