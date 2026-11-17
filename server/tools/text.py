"""Text tools: replace content, style, fit and overflow of text shapes, create paragraph text, read fill runs."""

from itertools import groupby

from core.connection import error_text, get_connection
from core.drawing import CDR_TEXT_SHAPE
from core.models import ToolResult
from core.results import respond
from core.targets import call_on_active
from core.units import MILLIMETER, convert
from tools.inspection import (
    _CDR_NO_FILL,
    _CDR_UNIFORM_FILL,
    _color_query,
    _decode_queried_color,
    _fill_details,
    _on_shape,
    _query,
)

_CDR_PARAGRAPH_TEXT = 1
_ALIGNMENT_MAP = {"left": 0, "center": 3, "right": 1, "none": 0}


def _find_text_shape(shape_id: str):
    """The text shape named or numbered shape_id on the active page of the active document (top level)."""
    doc = get_connection().app.ActiveDocument
    if doc is None:
        raise RuntimeError("CorelDRAW has no active document; open one with open_document")
    target = str(shape_id)
    match = next((shape for shape in doc.ActivePage.Shapes if str(shape.StaticID) == target or shape.Name == target),
                 None)
    if match is None:
        raise ValueError(f"No text shape {target!r} (StaticID or name) at the top level of the active page; "
                         "use set_text_content_by_id for exact IDs, or find_shapes")
    if match.Type != CDR_TEXT_SHAPE:
        raise ValueError(f"Shape {target!r} is not text")
    return match


def _first_working(label: str, attempts) -> None:
    """Run the first accessor that works (COM text APIs differ between CorelDRAW versions); raise if none does."""
    errors = []
    for attempt in attempts:
        try:
            attempt()
            return
        except Exception as exc:
            errors.append(error_text(exc))
    raise RuntimeError(f"Could not set {label}: {'; '.join(errors)}")


def _get_text_content(text) -> str:
    try:
        return text.Story.Text
    except Exception:
        return text.Contents  # older versions


def _set_text_content(text, content: str) -> None:
    _first_working("the text content", (
        lambda: setattr(text.Story, "Text", content),
        lambda: setattr(text, "Contents", content),
        lambda: text.SetContents(2, content),
    ))


def set_text_content_by_id(document_path: str, page_index: int, shape_id: str, content: str) -> ToolResult:
    """Replace an exact editable text story and return the actual text read back from CorelDRAW."""
    def replace(app, doc, shape):
        if shape.Type != CDR_TEXT_SHAPE:
            raise ValueError("The specified shape is not editable text")
        shape.Text.Story.Text = content
        actual = shape.Text.Story.Text
        data = {"content": actual}
        if actual != content:
            data["verification_error"] = "CorelDRAW text differs from the requested content"
        return data

    return _on_shape(document_path, page_index, shape_id, replace, write=True)


_TEXT_QUERY_BATCH = 100
_MAX_TEXT_CHARACTERS = 5000


def _character_fills(shape, story):
    """Return (text, fill) for every character, using batched queries with a direct-read fallback.

    The queries run on the text shape: TextRange.Evaluate has no @com, so queries on the story always failed.
    """
    count = shape.Evaluate("@com.Text.Story.Characters.Count")
    if count > _MAX_TEXT_CHARACTERS:
        raise ValueError(f"Text has {count} characters; the limit is {_MAX_TEXT_CHARACTERS}")
    # convert binds each character to $item, so its path is resolved once, not once per property.
    leaf = (f"array($item.Text, array($item.Fill.Type, iif($item.Fill.Type = {_CDR_UNIFORM_FILL}, "
            f"{_color_query('$item.Fill.UniformColor')}, 0)))")
    characters = []
    for start in range(1, count + 1, _TEXT_QUERY_BATCH):
        indices = range(start, min(count, start + _TEXT_QUERY_BATCH - 1) + 1)
        try:
            values = _query(shape, [f"array(@com.Text.Story.Characters.Item({index})).convert({leaf})"
                                    for index in indices])
            for text, (fill_type, color) in (value[0] for value in values):
                fill = {"type": fill_type}
                if fill_type == _CDR_UNIFORM_FILL:
                    fill["color"] = _decode_queried_color(color)
                elif fill_type != _CDR_NO_FILL:
                    fill["color"] = {"kind": "unknown"}
                characters.append((text, fill))
        except Exception:
            for index in indices:
                character = story.Characters.Item(index)
                characters.append((character.Text, _fill_details(character)))
    return characters


def _fill_runs(characters):
    # Positions count CorelDRAW characters, which Story.Range uses, not Python code points.
    runs, position = [], 0
    for fill, group in groupby(characters, key=lambda character: character[1]):
        texts = [text for text, _ in group]
        text = "".join(texts)
        runs.append({"start": position, "end": position + len(texts), "text": text,
                     "whitespace_only": text.isspace(), "fill": fill})
        position += len(texts)
    return runs


def _text_story(shape):
    if shape.Type != CDR_TEXT_SHAPE:
        raise ValueError("The specified shape is not editable text")
    return shape.Text.Story


def inspect_text_fill_runs(document_path: str, page_index: int, shape_id: str) -> ToolResult:
    """List the character fill runs of one exact text shape, including whitespace.

    Each run is a zero-based, end-exclusive character range [start, end) sharing one native fill
    (spot name and tint, process components, registration, none, or unknown). The shape-level
    fill reports only part of mixed text, so check these runs before copying fills or separating.
    """
    def inspect(app, doc, shape):
        characters = _character_fills(shape, _text_story(shape))
        return {"text": "".join(text for text, _ in characters), "runs": _fill_runs(characters)}

    return _on_shape(document_path, page_index, shape_id, inspect)


def _set_text_font(text, font: str) -> None:
    _first_working("the font", (lambda: setattr(text.Story, "Font", font),
                                lambda: setattr(text.FontProperties, "Name", font)))


def _set_text_size(text, size: float) -> None:
    _first_working("the size", (lambda: setattr(text.Story, "Size", size),
                                lambda: setattr(text.FontProperties, "Size", size)))


def _set_text_bold(text, bold: bool) -> None:
    _first_working("bold", (lambda: setattr(text.Story, "Bold", bold),
                            lambda: setattr(text.FontProperties, "Bold", bold)))


def set_text_content(document_path: str, shape_id: str, content: str) -> ToolResult:
    """Replace the content of a text shape on the active page, found by StaticID or name. document_path must be
    active; for exact pages use set_text_content_by_id."""
    conn = get_connection()

    def _set():
        shape = _find_text_shape(shape_id)
        old_content = _get_text_content(shape.Text)
        _set_text_content(shape.Text, content)
        text_type = "paragraph" if shape.Text.Type == _CDR_PARAGRAPH_TEXT else "artistic"
        return {"shape_id": shape_id, "old_content": old_content, "new_content": content, "text_type": text_type}

    result = call_on_active(conn, document_path, _set)
    if result["success"]:
        return ToolResult.ok(f"Replaced the text of {shape_id}", **result["result"])
    return respond(result, document_path=document_path)


def set_text_style(
    document_path: str,
    shape_id: str,
    font: str = "",
    size: float = 0,
    bold: bool = False,
    italic: bool = False,
    alignment: str = "",
) -> ToolResult:
    """Set the font, size (pt), bold, italic and/or alignment (left, center, right) of a text shape on the active
    page, found by StaticID or name. document_path must be active. Returns changes and any that failed."""
    conn = get_connection()

    def _style():
        shape = _find_text_shape(shape_id)
        text = shape.Text
        steps = []
        if font:
            steps.append(("font", font, lambda: _set_text_font(text, font)))
        if size > 0:
            steps.append(("size", size, lambda: _set_text_size(text, size)))
        if bold:
            steps.append(("bold", True, lambda: _set_text_bold(text, True)))
        if italic:
            steps.append(("italic", True, lambda: setattr(text.FontProperties, "Italic", True)))
        if alignment:
            if alignment not in _ALIGNMENT_MAP:
                raise ValueError("alignment must be left, center, right or none")
            steps.append(("alignment", alignment, lambda: setattr(text, "Alignment", _ALIGNMENT_MAP[alignment])))
        if not steps:
            raise ValueError("Pass at least one of font, size, bold, italic or alignment")
        changes, failed = {}, {}
        for name, value, apply in steps:
            try:
                apply()
                changes[name] = value
            except Exception as exc:
                failed[name] = error_text(exc)
        data = {"shape_id": shape_id, "changes": changes}
        if failed:
            data["failed"] = failed
            if not changes:
                data["verification_error"] = f"No style change was applied: {failed}"
        return data

    result = call_on_active(conn, document_path, _style)
    if result["success"] and "verification_error" not in result["result"]:
        data = result["result"]
        message = f"Text style updated: {', '.join(data['changes'])}"
        if data.get("failed"):
            message += f"; failed: {', '.join(data['failed'])}"
        return ToolResult.ok(message, **data)
    return respond(result, document_path=document_path)


def fit_text_to_frame(document_path: str, shape_id: str) -> ToolResult:
    """Fit paragraph text to its frame (found by StaticID or name on the active page). document_path must be
    active."""
    conn = get_connection()

    def _fit():
        shape = _find_text_shape(shape_id)
        if shape.Text.Type != _CDR_PARAGRAPH_TEXT:
            raise ValueError("Only paragraph text fits a frame; this shape is artistic text")
        shape.Text.FitTextToFrame = True
        return {"shape_id": shape_id, "fitted": True}

    return respond(call_on_active(conn, document_path, _fit), "Fitted the text to its frame",
                   document_path=document_path)


def check_text_overflow(shape_id: str, document_path: str = "") -> ToolResult:
    """Check whether paragraph text (by StaticID or name, active page) overflows its frame. Reads the active
    document (document_path, if given, must be active)."""
    conn = get_connection()

    def _check():
        shape = _find_text_shape(shape_id)
        text_type = shape.Text.Type
        data = {"shape_id": shape_id, "overflowing": False,
                "text_type": "paragraph" if text_type == _CDR_PARAGRAPH_TEXT else "artistic",
                "content_length": len(_get_text_content(shape.Text))}
        if text_type == _CDR_PARAGRAPH_TEXT:
            # The property name differs between versions.
            try:
                data["overflowing"] = bool(shape.Text.Overflows)
            except Exception:
                data["overflowing"] = bool(shape.Text.IsOverflowing)
        return data

    result = call_on_active(conn, document_path, _check, read_only=True)
    if result["success"]:
        status = "overflows" if result["result"]["overflowing"] else "fits"
        return ToolResult.ok(f"The text {status}", **result["result"])
    return respond(result, document_path=document_path)


def create_text_frame(document_path: str, x: float, y: float, width: float, height: float, text: str) -> ToolResult:
    """Create paragraph text in a frame on the active layer of the active page; x, y, width, height in mm.
    document_path must be active. Returns shape_id."""
    conn = get_connection()

    def _create():
        doc = conn.app.ActiveDocument
        layer = doc.ActivePage.ActiveLayer
        left, top, right, bottom = (convert(value, MILLIMETER, doc.Unit, conn.app)
                                    for value in (x, y, x + width, y + height))
        shape = layer.CreateParagraphText(left, top, right, bottom, text)
        warnings = []
        # Default style: a size that fits the frame height, black text.
        size = max(8.0, min(18.0, height * 0.55))
        for label, apply in (("size", lambda: _set_text_size(shape.Text, size)),
                             ("black fill", lambda: shape.Fill.UniformColor.CMYKAssign(0, 0, 0, 100))):
            try:
                apply()
            except Exception as exc:
                warnings.append(f"default {label} not applied: {error_text(exc)}")
        shape.Name = f"text_{shape.StaticID}"
        data = {"shape_id": str(shape.StaticID), "name": shape.Name, "width": width, "height": height, "x": x,
                "y": y, "unit": "mm", "content": text}
        return {**data, "warnings": warnings} if warnings else data

    return respond(call_on_active(conn, document_path, _create), f"Created a {width} x {height} mm text frame",
                   document_path=document_path)
