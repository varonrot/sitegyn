# render_service.py
#
# שירות רינדור "און דה פליי" – מייצר HTML ישירות מהנתונים ב-Supabase
# בלי לכתוב לקבצים ב-output.
#
# רעיון:
#   /p/<subdomain> → server.py קורא ל-render_project_html_by_subdomain()
#   הפונקציה:
#     1. מוצאת את ה-project לפי subdomain
#     2. טוענת את ה-template + ה-mapping
#     3. משתמשת ב-_render_template מתוך build_service
#     4. מחזירה מחרוזת HTML מוכנה לדפדפן

from __future__ import annotations

import traceback
from typing import Optional, Dict, Any

# אנחנו ממחזרים את כל הלוגיקה של הבילדר:
#   - supabase (חיבור)
#   - _resolve_template_path
#   - _load_template_mapping
#   - _render_template (כולל הנרמול של content_json לפיצה)
from build_service import (
    supabase,
    _resolve_template_path,
    _load_template_mapping,
    _render_template,
)


def _load_project_by_id(project_id: str) -> Optional[Dict[str, Any]]:
    """
    מחזיר רשומת project מלאה לפי id, או None אם לא נמצא / שגיאה.
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
            print(f"[render_service] project {project_id} not found")
            return None
        return rows[0]
    except Exception:
        traceback.print_exc()
        return None


def _load_project_id_by_subdomain(subdomain: str) -> Optional[str]:
    """
    מאתר project_id לפי subdomain מתוך טבלת projects.
    """
    try:
        resp = (
            supabase.table("projects")
            .select("id")
            .eq("subdomain", subdomain)
            .limit(1)
            .execute()
        )
        rows = getattr(resp, "data", []) or []
        if not rows:
            print(f"[render_service] project with subdomain={subdomain} not found")
            return None
        return rows[0]["id"]
    except Exception:
        traceback.print_exc()
        return None


def render_project_html(project_id: str) -> Optional[str]:
    """
    רנדר מלא של אתר לפי project_id:
      1. טוען את ה-project מ-Supabase
      2. בוחר template_id (עם ברירת מחדל template_pizza_02)
      3. טוען HTML + mapping.json מהתיקייה sitegyn/templates/...
      4. מריץ _render_template (אותו כמו בבילדר – כולל נרמול של content_json)
      5. מחזיר מחרוזת HTML מוכנה. אם יש שגיאה – מחזיר None.
    """
    try:
        project = _load_project_by_id(project_id)
        if not project:
            return None

        template_id = project.get("selected_template_id") or "template_pizza_02"

        template_path = _resolve_template_path(template_id)
        if not template_path:
            print(f"[render_service] template file not found for template_id={template_id}")
            return None

        mapping = _load_template_mapping(template_id)

        html_source = template_path.read_text(encoding="utf-8")
        rendered_html = _render_template(html_source, project, mapping)
        return rendered_html

    except Exception:
        traceback.print_exc()
        return None


def render_project_html_by_subdomain(subdomain: str) -> Optional[str]:
    """
    רנדר מלא של אתר לפי subdomain (לשימוש בנתיב /p/<subdomain>).

    שימוש טיפוסי ב-server.py:
        from render_service import render_project_html_by_subdomain

        @app.route("/p/<subdomain>")
        def public_page_by_subdomain(subdomain: str):
            html = render_project_html_by_subdomain(subdomain)
            if html is None:
                return "Project not found or failed to render", 404
            return html
    """
    try:
        project_id = _load_project_id_by_subdomain(subdomain)
        if not project_id:
            return None
        return render_project_html(project_id)
    except Exception:
        traceback.print_exc()
        return None


if __name__ == "__main__":
    # בדיקה ידנית קטנה:
    #   export TEST_PROJECT_SUBDOMAIN=rotempizza
    #   python render_service.py
    import os

    test_sub = os.getenv("TEST_PROJECT_SUBDOMAIN")
    if test_sub:
        html = render_project_html_by_subdomain(test_sub) or ""
        print(f"Rendered length for subdomain={test_sub}: {len(html)} chars")
