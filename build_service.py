import json
import re
from pathlib import Path
from typing import Any, Dict, Optional
from supabase import create_client
import os
from bs4 import BeautifulSoup  # make sure beautifulsoup4 is installed


# ==========================================
# Supabase client
# ==========================================
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)


# ==========================================
# Helpers – load project + resolve paths
# ==========================================
def _load_project_by_id(project_id: str) -> Optional[Dict[str, Any]]:
    """Load project row from Supabase."""
    result = (
        supabase.table("projects")
        .select("*")
        .eq("id", project_id)
        .execute()
    )
    data = result.data
    if not data:
        return None
    return data[0]


def _resolve_template_path(template_id: str) -> Path:
    """
    Resolve HTML file path for the given template.
    We rely on templates_config.json structure.
    """
    base_dir = Path(__file__).resolve().parent.parent
    from templates_config import TEMPLATES

    html_rel_path = TEMPLATES[template_id]["html"]
    return base_dir / html_rel_path


def _load_template_mapping(template_id: str) -> Dict[str, str]:
    """Loads mapping.json for template."""
    base_dir = Path(__file__).resolve().parent.parent
    from templates_config import TEMPLATES

    mapping_rel_path = TEMPLATES[template_id]["mapping"]
    mapping_path = base_dir / mapping_rel_path
    return json.loads(mapping_path.read_text(encoding="utf-8"))


# ==========================================
# NEW: get_value_by_path – handles paths like "menu.pizzas[1].name"
# ==========================================
def get_value_by_path(data: Dict[str, Any], path: str) -> Any:
    """
    Extracts nested value from dict using path like:
    "home.hero.headline"
    "offers.deals[2].price_text"
    """
    current = data
    tokens = re.findall(r"[a-zA-Z0-9_]+|\[\d+\]", path)

    for token in tokens:
        if token.startswith("[") and token.endswith("]"):
            index = int(token[1:-1])
            if isinstance(current, list) and 0 <= index < len(current):
                current = current[index]
            else:
                return None
        else:
            if isinstance(current, dict) and token in current:
                current = current[token]
            else:
                return None

    return current


# ==========================================
# NEW: inject_value_into_html – replaces innerText of an element by ID
# ==========================================
def inject_value_into_html(soup: BeautifulSoup, element_id: str, value: str):
    """
    Finds element with id="element_id" and replaces its inner text with value.
    """
    tag = soup.find(id=element_id)
    if tag:
        # Clear children and set text
        tag.clear()
        tag.append(str(value))


# ==========================================
# NEW: _render_template – the modern renderer
# ==========================================
def _render_template(html_source: str, project: Dict[str, Any], mapping: Dict[str, str]) -> str:
    """
    Renders final HTML by injecting values from project["content_json"]
    into HTML according to mapping (id -> schema.path).
    """
    content_json = project.get("content_json") or {}

    # Parse with BeautifulSoup for clean DOM manipulation
    soup = BeautifulSoup(html_source, "html.parser")

    # For each HTML id → find value in content_json
    for html_id, schema_path in mapping.items():
        value = get_value_by_path(content_json, schema_path)
        if value is not None:
            inject_value_into_html(soup, html_id, value)

    return str(soup)


# ==========================================
# OPTIONAL: build_site_for_project (for static output)
# ==========================================
def build_site_for_project(project_id: str, output_dir: Optional[str] = None) -> Optional[str]:
    """
    Build a static version of the site for a given project_id (optional).
    Only used if you want physical files.
    """
    project = _load_project_by_id(project_id)
    if not project:
        return None

    template_id = project.get("selected_template_id")
    if not template_id:
        return None

    template_path = _resolve_template_path(template_id)
    mapping = _load_template_mapping(template_id)

    html_source = template_path.read_text(encoding="utf-8")
    rendered_html = _render_template(html_source, project, mapping)

    if output_dir:
        output_path = Path(output_dir) / f"{project_id}.html"
        output_path.write_text(rendered_html, encoding="utf-8")

    return rendered_html
