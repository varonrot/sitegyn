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

def _normalize_pizza_content(raw: Dict[str, Any]) -> Dict[str, Any]:
    """
    ממפה את המבנה המקונן של content_json (home/hero/menu/about/contact)
    למפתחות השטוחים שהטמפלט template_pizza_02 מצפה להם.
    אם כבר יש מפתחות שטוחים (home_hero_title וכו') – מחזיר כמו שהוא.
    """
    if not isinstance(raw, dict):
        return {}

    # אם כבר יש מפתח שטוח – לא נוגעים
    if "home_hero_title" in raw:
        return raw

    home = raw.get("home") or {}
    hero = home.get("hero") or {}
    menu = home.get("menu") or {}
    items = menu.get("items") or []
    about = home.get("about") or {}
    reasons = about.get("reasons_to_choose_us") or []
    contact = home.get("contact") or {}

    flat: Dict[str, Any] = {}

    # Hero
    flat["home_hero_title"] = hero.get("headline", "")
    flat["home_hero_paragraph"] = hero.get("subheadline", "")
    flat["home_hero_cta"] = hero.get("call_to_action", "")

    # Why us – ניקח עד 3 סיבות
    for i in range(3):
        if i < len(reasons):
            r = reasons[i] or {}
            flat[f"feature_{i+1}_title"] = r.get("title", "")
            flat[f"feature_{i+1}_text"] = r.get("description", "")

    # Menu – ניקח עד 3 פריטים ראשונים
    for i in range(3):
        if i < len(items):
            it = items[i] or {}
            flat[f"menu_item_{i+1}_name"] = it.get("name", "")
            flat[f"menu_item_{i+1}_price"] = it.get("price", "")
            flat[f"menu_item_{i+1}_desc"] = it.get("description", "")

    # Contact
    flat["contact_title"] = contact.get("title", "")
    flat["contact_subtitle"] = contact.get("description", "")
    flat["cta_whatsapp"] = contact.get("call_to_action", "")
    flat["opening_hours"] = contact.get("opening_hours", "")

    return flat

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
    # אם זה טמפלט פיצה – ננרמל את המבנה המקונן
    template_id = project.get("selected_template_id") or "template_pizza_02"
    if template_id == "template_pizza_02":
        content_json = _normalize_pizza_content(content_json)

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
