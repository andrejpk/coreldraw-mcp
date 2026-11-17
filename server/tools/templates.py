"""Template registry (config/template_registry.json): list templates and look up size variants."""

import json
import os

from core.models import ToolResult

_REGISTRY_PATH = os.path.join(os.path.dirname(__file__), "../config/template_registry.json")


def _load_registry() -> dict:
    with open(_REGISTRY_PATH, encoding="utf-8") as f:
        return json.load(f)


def _templates():
    """(templates, None) or (None, failure)."""
    try:
        return _load_registry().get("templates", {}), None
    except (OSError, ValueError) as exc:
        return None, ToolResult.fail(f"Cannot read the template registry {os.path.normpath(_REGISTRY_PATH)}: {exc}")


def list_templates(filter_material: str = "", filter_keyword: str = "") -> ToolResult:
    """List registry templates, optionally filtered by material or a keyword in name or description: name,
    description, file, size in mm, material, placeholders and size variants."""
    templates, error = _templates()
    if error:
        return error
    result = []
    for name, cfg in templates.items():
        if filter_material and filter_material.lower() not in cfg.get("material", "").lower():
            continue
        if filter_keyword:
            keyword = filter_keyword.lower()
            if keyword not in name.lower() and keyword not in cfg.get("description", "").lower():
                continue
        entry = {
            "name": name,
            "description": cfg.get("description", ""),
            "file": cfg.get("file", ""),
            "width_mm": cfg.get("width_mm"),
            "height_mm": cfg.get("height_mm"),
            "material": cfg.get("material", ""),
            "placeholders": list(cfg.get("placeholders", {}).keys()),
            "has_size_variants": "size_variants" in cfg,
        }
        if "size_variants" in cfg:
            entry["size_variants"] = cfg["size_variants"]
        result.append(entry)
    return ToolResult.ok(f"{len(result)} templates", total=len(result), templates=result)


def get_template_info(template_name: str) -> ToolResult:
    """Return a registry template's full configuration: placeholders, layers, size variants, title block."""
    templates, error = _templates()
    if error:
        return error
    if template_name not in templates:
        return ToolResult.fail(f"No template {template_name!r}; templates: {list(templates)}")
    return ToolResult.ok(f"Template {template_name}", name=template_name, **templates[template_name])


def get_size_variant(template_name: str, room_number: str) -> ToolResult:
    """Look up a template's page width for a room number by its character count (e.g. door sign width)."""
    templates, error = _templates()
    if error:
        return error
    if template_name not in templates:
        return ToolResult.fail(f"No template {template_name!r}; templates: {list(templates)}")
    cfg = templates[template_name]
    if "size_variants" not in cfg:
        return ToolResult.ok("This template has no size variants; use its default size",
                             width_mm=cfg.get("width_mm"), height_mm=cfg.get("height_mm"), variant_matched=False)
    char_count = len(room_number.strip())
    for variant in cfg["size_variants"].get("variants", []):
        if variant.get("char_count") == char_count:
            return ToolResult.ok(
                f"Room number {room_number!r} ({char_count} characters): width {variant['width_mm']} mm",
                width_mm=variant["width_mm"], height_mm=cfg.get("height_mm"), width_in=variant.get("width_in"),
                char_count=char_count, variant_matched=True, rule=cfg["size_variants"].get("rule", ""))
    return ToolResult.ok(f"No variant for {char_count} characters; use the default size",
                         width_mm=cfg.get("width_mm"), height_mm=cfg.get("height_mm"), char_count=char_count,
                         variant_matched=False)
