"""Engineering drawings: fill title blocks and assemble a design into a drawing frame template."""

import os

from core.connection import error_text, get_connection
from core.drawing import CDR_TEXT_SHAPE
from core.models import ToolResult
from core.results import respond
from core.targets import private_document, save_as

_FIELDS = ("project_name", "order_no", "part_no", "designer", "material", "finish", "scale", "sheet_no", "revision")


def _fill_fields(pages, fields: dict, replaced: dict, failed: dict) -> None:
    """Set each top-level text shape named like a field key ("title_project_name"...) to the field's value."""
    for page in pages:
        for shape in page.Shapes:
            if shape.Type != CDR_TEXT_SHAPE or shape.Name not in fields or shape.Name in replaced:
                continue
            try:
                shape.Text.Story.Text = str(fields[shape.Name])
                replaced[shape.Name] = str(fields[shape.Name])
            except Exception as exc:
                failed[shape.Name] = error_text(exc)


def populate_title_block(
    template_path: str,
    project_name: str = "",
    order_no: str = "",
    part_no: str = "",
    designer: str = "",
    material: str = "",
    finish: str = "",
    scale: str = "1:1",
    sheet_no: str = "",
    revision: str = "A",
    output_path: str = "",
) -> ToolResult:
    """Fill the title block of an engineering drawing template: text shapes named title_<field> (e.g.
    title_project_name) on any page get the non-empty values. Saves to output_path (default: overwrite the
    template). Returns replaced, not_found and failed fields."""
    conn = get_connection()
    if not os.path.isfile(template_path):
        return ToolResult.fail(f"Template not found: {template_path}")
    save_path = output_path or template_path
    values = dict(zip(_FIELDS, (project_name, order_no, part_no, designer, material, finish, scale, sheet_no,
                                revision), strict=True))
    fields = {f"title_{key}": value for key, value in values.items() if value}

    def _populate():
        with private_document(conn.app, template_path) as doc:
            replaced, failed = {}, {}
            # The active page first, then the other pages (a drawing may have several).
            pages = [doc.ActivePage] + [page for page in doc.Pages if page.Index != doc.ActivePage.Index]
            _fill_fields(pages, fields, replaced, failed)
            dirpath = os.path.dirname(save_path)
            if dirpath:
                os.makedirs(dirpath, exist_ok=True)
            save_as(conn.app, doc, save_path)
        return {"output": save_path, "replaced": replaced,
                "not_found": [key for key in fields if key not in replaced and key not in failed], "failed": failed}

    result = conn.safe_call(_populate)
    if result["success"]:
        r = result["result"]
        message = f"Filled {len(r['replaced'])} title block fields in {save_path}"
        if r["not_found"]:
            message += f"; no shape for {r['not_found']}"
        return ToolResult.ok(message, **r)
    return respond(result)


def assemble_engineering_drawing(
    design_path: str,
    frame_template_path: str,
    output_path: str,
    title_block_fields: dict = None,
) -> ToolResult:
    """Make an engineering drawing: open a frame template privately, fill its title block (title_block_fields
    keys as populate_title_block parameters), import the design file and save as output_path."""
    conn = get_connection()
    if not os.path.isfile(design_path):
        return ToolResult.fail(f"Design file not found: {design_path}")
    if not os.path.isfile(frame_template_path):
        return ToolResult.fail(f"Frame template not found: {frame_template_path}")
    given = title_block_fields or {}
    defaults = {"scale": "1:1", "revision": "A"}
    fields = {f"title_{key}": given.get(key, defaults.get(key, "")) for key in _FIELDS}
    fields = {key: value for key, value in fields.items() if value}

    def _assemble():
        with private_document(conn.app, frame_template_path) as doc:
            replaced, failed = {}, {}
            _fill_fields([doc.ActivePage], fields, replaced, failed)
            doc.Import(design_path)
            dirpath = os.path.dirname(output_path)
            if dirpath:
                os.makedirs(dirpath, exist_ok=True)
            save_as(conn.app, doc, output_path)
        data = {"output": output_path, "design_imported": design_path, "frame_used": frame_template_path,
                "title_block_filled": replaced}
        return {**data, "title_block_failed": failed} if failed else data

    result = conn.safe_call(_assemble)
    if result["success"]:
        return ToolResult.ok(f"Assembled the drawing: {output_path}", **result["result"])
    return respond(result)
