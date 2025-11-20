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

# חיבור ל-Supabase
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
    raise RuntimeError("Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY in environment")

supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)

PLACEHOLDER_PATTERN = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}")


def _resolve_template_path(template_id: str) -> Optional[Path]:
    if not template_id:
        return None

    folder = template_id
    html_name = f"{template_id}.html"
    candidate = TEMPLATES_ROOT / folder / html_name
    if candidate.exists():
        return candidate

    return None


def _load_template_mapping(template_id: str) -> Dict[str, Any]:
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


def _find_value_by_key_ci(data: Any, key: str) -> Optional[str]:
    """
    מחפש מפתח בשם key בכל עומק של dict / list, בצורה לא רגישה לאותיות.
    """
    target = key.lower()

    if isinstance(data, dict):
        # קודם בדיקה ישירה (case-insensitive)
        for k, v in data.items():
            if k.lower() == target and isinstance(v, (str, int, float)):
                return str(v)

        # ואז מעבר על כל הערכים
        for v in data.values():
            result = _find_value_by_key_ci(v, key)
            if result is not None:
                return result

    elif isinstance(data, list):
        for item in data:
            result = _find_value_by_key_ci(item, key)
            if result is not None:
                return result

    return None


def _render_template(html_source: str, project: dict, mapping: Dict[str, Any]) -> str:
    """
    מחליף את כל ה-{{ key }} ב-HTML:
    1. קודם מתוך content_json (בכל עומק, case-insensitive)
    2. אחרת מתוך mapping[key] או mapping[key.lower()] עם .format(**ctx)
    3. אחרת עסקים מיוחדים (למשל LOGO_TITLE -> business_name)
    4. אחרת מנסה ctx[key]
    5. אחרת משאיר את placeholder כמו שהוא
    """

    content_json = project.get("content_json") or {}
    if not isinstance(content_json, dict):
        content_json = {}

    ctx: Dict[str, Any] = {
        "business_name": project.get("business_name") or "",
        "business_type": project.get("business_type") or "",
        "niche": project.get("niche") or "",
        "city": project.get("city") or "",
        "country": project.get("country") or "",
    }

    # opening_hours
    oh = _find_value_by_key_ci(content_json, "opening_hours")
    if isinstance(oh, str) and oh.strip():
        ctx["opening_hours"] = oh
    else:
        ctx["opening_hours"] = "Open daily: 11:00–23:00"

    cache: Dict[str, Optional[str]] = {}

    def resolve_key(raw_key: str) -> Optional[str]:
        key = raw_key.strip()
        kl = key.lower()

        if key in cache:
            return cache[key]

        # 1) content_json (תוך כדי case-insensitive)
        v = _find_value_by_key_ci(content_json, key)
        if v is not None:
            cache[key] = v
            return v

        # 2) mapping.json
        if mapping:
            raw = None
            if key in mapping:
                raw = mapping[key]
            elif kl in mapping:
                raw = mapping[kl]

            if isinstance(raw, str):
                try:
                    formatted = raw.format(**ctx)
                except Exception:
                    formatted = raw
                cache[key] = formatted
                return formatted

        # 3) מיפוי מיוחד: LOGO_TITLE -> business_name
        if kl == "logo_title" and ctx.get("business_name"):
            cache[key] = str(ctx["business_name"])
            return cache[key]

        # 4) ctx[key]
        if key in ctx and ctx[key]:
            cache[key] = str(ctx[key])
            return cache[key]

        cache[key] = None
        return None

    def repl(match: re.Match) -> str:
        key = match.group(1)
        value = resolve_key(key)
        if value is None:
            return match.group(0)
        return value

    rendered = PLACEHOLDER_PATTERN.sub(repl, html_source)
    return rendered


def run_build_for_project(project_id: str) -> Optional[Path]:
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

        template_id = project.get("selected_template_id") or "template_pizza_02"
        template_path = _resolve_template_path(template_id)
        if not template_path:
            print(f"[build_service] template file not found for template_id={template_id}")
            return None

        mapping = _load_template_mapping(template_id)

        out_dir = OUTPUT_ROOT / project_id
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / "index.html"

        html_source = template_path.read_text(encoding="utf-8")
        rendered_html = _render_template(html_source, project, mapping)
        out_path.write_text(rendered_html, encoding="utf-8")

        print(f"[build_service] built site for project {project_id} at {out_path}")
        return out_path

    except Exception:
        traceback.print_exc()
        return None


if __name__ == "__main__":
    test_project_id = os.getenv("TEST_PROJECT_ID")
    if test_project_id:
        run_build_for_project(test_project_id)
