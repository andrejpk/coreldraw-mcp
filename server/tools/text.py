"""Text on exact IDs: inspect content, font and fill runs, outlines, frames and fonts; edit content and style.

Positions are CorelDRAW character positions, zero-based and end-exclusive ([start, end)), as ``Story.Range`` uses
them. A line or paragraph break is one character ("\\r"). Characters are read in batched CQL queries through each
text shape (``$item.Text.Story`` bound with ``convert``; ``TextRange.Evaluate`` has no ``@com``).

CorelDRAW 2022 findings (E14, measured):

* Replacing a range (``TextRange.Text = ...``) gives the new characters the formatting of the range's last
  character; ``Story.Text = ...`` therefore gives the whole new text the formatting of the old text's last
  character. Inserting at an empty range takes the neighbouring formatting. ``edit_text`` replaces only the
  changed middle of the text, so prefix and suffix keep their own fill and font.
* Characters outside the Basic Multilingual Plane (emoji and other surrogate pairs) cannot be written through
  automation: every text setter and ``Create*TextWide`` turns the high surrogate into a space, so ``edit_text`` and
  ``create_shape`` refuse them.
* A story with mixed fonts reports ``Font`` "" and ``Size`` 1.18e-38; ``Bold``/``Italic`` read False when mixed.
  Per-character reads are the only reliable source.
* Setting a font that is not installed silently keeps the old font, so fonts are checked against
  ``Application.FontList`` first.
"""

from __future__ import annotations

import difflib
import time
from typing import Literal

from core.connection import get_connection
from core.models import ToolResult
from core.results import respond
from core.structure import StaleStructureError, invalidate, locate_entries, page_layers, read_entries
from core.targets import resolve_document, resolve_page
from core.units import unit_name
from tools import inks
from tools.colors import (
    _check,
    _com_shape,
    _command_group,
    _edit,
    _fill_state,
    _outline_state,
    _read,
    _RefusedError,
)

_TEXT = 6
_KINDS = {0: "artistic", 1: "paragraph", 2: "artistic_on_path", 3: "paragraph_on_path"}
_ALIGNMENTS = {"none": 0, "left": 1, "right": 2, "center": 3, "justify": 4, "force_justify": 5}
_ALIGNMENT_NAMES = {code: name for name, code in _ALIGNMENTS.items()} | {6: "mixed"}
_MAX_CHARACTERS = 4000  # per text shape; longer texts report story-level values only
_CHUNK = 200  # characters per CQL array
_PREVIEW = 120  # characters of text and run text returned unless full_text
_RESERVE_SECONDS = 15.0
_FONT_CACHE_SECONDS = 120.0
_SIZE_TOLERANCE = 0.01
Part = Literal["fonts", "fills", "outline", "frame"]
_PARTS = ("fonts", "fills", "outline", "frame")

# One read per text shape: kind, character count, text, lines, paragraphs, and the frame of paragraph text.
_STORY = [
    "$item.Text.Type", "$item.Text.Story.Characters.Count", "$item.Text.Story.Text",
    "$item.Text.Story.Lines.Count", "$item.Text.Story.Paragraphs.Count",
    "iif($item.Text.Type = 1, array($item.Text.Overflow, $item.Text.Frame.Range.End, $item.Text.Frames.Count), 0)",
]
# Per character: text, fill type, fill, outline type, outline (inks._CHARACTER), then font, size, bold, italic.
_CHARACTER = f"array({inks._CHARACTER}, $item.Font, $item.Size, $item.Bold, $item.Italic)"

_fonts: dict = {"names": {}, "read_at": 0.0}


# --- reading -------------------------------------------------------------------------------------------------

def _installed_fonts(app) -> dict[str, str]:
    """{lowercase name: name} of installed fonts (Application.FontList), cached for a while."""
    if not _fonts["names"] or time.monotonic() - _fonts["read_at"] > _FONT_CACHE_SECONDS:
        fonts = app.FontList
        _fonts["names"] = {name.lower(): name for name in (fonts.Item(i) for i in range(1, fonts.Count + 1))}
        _fonts["read_at"] = time.monotonic()
    return _fonts["names"]


def font_name(app, font: str) -> str:
    """The installed font's exact name, or a ValueError with near matches (shared with create_shape)."""
    names = _installed_fonts(app)
    if font.lower() in names:
        return names[font.lower()]
    near = difflib.get_close_matches(font.lower(), list(names), n=6, cutoff=0.6)
    hint = f"; near matches: {', '.join(names[n] for n in near)}" if near else ""
    raise ValueError(f"Font {font!r} is not installed (CorelDRAW would silently keep the old font){hint}")


def _clean(text: str) -> str:
    """Text safe for JSON: a lone surrogate (half of a non-BMP character) becomes U+FFFD."""
    return text.encode("utf-16", "surrogatepass").decode("utf-16", "replace")


def _characters(view, entry, count):
    """Per character: (text, fill type, fill, outline type, outline, font, size, bold, italic)."""
    expressions = []
    for start in range(1, count + 1, _CHUNK):
        items = ", ".join(f"$item.Characters.Item({index})"
                          for index in range(start, min(count, start + _CHUNK - 1) + 1))
        expressions.append(f"array($item.Text.Story).convert(array({items}).convert({_CHARACTER}))")
    row = read_entries(view, [entry], expressions)[0] if expressions else ()
    return [(*paint, *font) for chunk in row for paint, *font in chunk[0]]


def _attributes(character, unit):
    """Comparable formatting of one character: fill, outline, font, size, bold, italic."""
    _, fill_type, fill, outline_type, outline, font, size, bold, italic = character
    return (str(_fill_state((fill_type, fill))), str(_outline_state((outline_type, outline), unit)), font,
            round(size, 3), bool(bold), bool(italic))


def _preview(text, limit):
    return text if limit is None or len(text) <= limit else text[:limit] + "…"


def _runs(characters, key, limit):
    """[(run, value, whole text)] for consecutive characters with equal key(character); [start, end) positions."""
    runs = []
    for position, character in enumerate(characters):
        value = key(character)
        if runs and runs[-1][2] == value:
            runs[-1][1] = position + 1
            runs[-1][3].append(character[0])
        else:
            runs.append([position, position + 1, value, [character[0]]])
    out = []
    for start, end, value, texts in runs:
        text = _clean("".join(texts))
        out.append(({"start": start, "end": end, "text": _preview(text, limit)}, value, text))
    return out


def _record(shape_id, view, story, characters, parts, unit, installed, full_text):
    """The inspect_text record of one text shape from its story row and characters (None: not read)."""
    kind, count, text, lines, paragraphs, frame = story
    text = _clean(text or "")
    limit = None if full_text else _PREVIEW
    record = {"id": shape_id, "layer": view.name, "kind": _KINDS.get(kind, f"text type {kind}"),
              "characters": count, "lines": lines, "paragraphs": paragraphs, "text": _preview(text, limit)}
    if limit is not None and len(text) > limit:
        record["text_truncated"] = True
    if characters is None and any(part in parts for part in ("fonts", "fills", "outline")):
        record["runs_not_read"] = f"more than {_MAX_CHARACTERS} characters"
    elif characters is not None:
        if "fonts" in parts:
            record["font_runs"] = [
                {**run, "font": font, "size": size, "bold": bold, "italic": italic}
                for run, (font, size, bold, italic), _ in _runs(
                    characters, lambda c: (c[5], round(c[6], 3), bool(c[7]), bool(c[8])), limit)]
            missing = sorted({c[5] for c in characters if c[5].lower() not in installed})
            if missing:
                record["missing_fonts"] = missing
        if "fills" in parts:
            record["fill_runs"] = [{**run, "whitespace_only": whole.isspace(), "fill": fill} for run, fill, whole
                                   in _runs(characters, lambda c: _fill_state((c[1], c[2])), limit)]
        if "outline" in parts:
            outline_runs = _runs(characters, lambda c: _outline_state((c[3], c[4]), unit), limit)
            if len(outline_runs) == 1:
                record["outline"] = outline_runs[0][1]
            else:
                record["outline_runs"] = [{**run, "outline": outline} for run, outline, _ in outline_runs]
    if "frame" in parts and kind == 1 and frame:
        overflowing, visible_end, frames = frame
        record["frame"] = {"overflowing": bool(overflowing), "visible_characters": visible_end,
                           "linked_frames": frames}
    return record


def _story_rows(view, entries):
    """Story rows of text entries of one layer; an exception in place of a row CorelDRAW cannot read."""
    try:
        return read_entries(view, entries, _STORY)
    except StaleStructureError:
        raise
    except Exception:
        rows = []
        for entry in entries:
            try:
                rows.extend(read_entries(view, [entry], _STORY))
            except StaleStructureError:
                raise
            except Exception as exc:
                rows.append(exc)
        return rows


def _scope(doc, page_index, shape_ids):
    """[(page index, LayerView, entry)] of the text shapes to read, and requested IDs that are not text."""
    if shape_ids is not None:
        located = locate_entries(doc, resolve_page(doc, page_index), shape_ids)
        flat = [(page_index, view, entry) for view, entry in located.values() if entry.type == _TEXT]
        return flat, [shape_id for shape_id, (_, entry) in located.items() if entry.type != _TEXT]
    pages = range(1, doc.Pages.Count + 1) if page_index is None else [page_index]
    flat, seen = [], set()
    for number in pages:
        for view in page_layers(doc, resolve_page(doc, number)):
            for entry in view.structure.entries:
                if entry.type != _TEXT:
                    continue
                if view.master:  # a master layer's text appears on every page: list it once
                    if (view.name, entry.shape_id) in seen:
                        continue
                    seen.add((view.name, entry.shape_id))
                flat.append((number, view, entry))
    return flat, []


def text_state(doc, page, shape_id) -> dict:
    """Text and font runs of one text shape, freshly located (create_shape's read-back)."""
    view, entry = locate_entries(doc, page, [shape_id])[shape_id]
    story = read_entries(view, [entry], _STORY)[0]
    record = _record(shape_id, view, story, _characters(view, entry, story[1]), ("fonts",), doc.Unit, {}, True)
    return {"text": record["text"], "font_runs": record["font_runs"]}


def inspect_text(
    document_path: str,
    page_index: int | None = None,
    shape_ids: list[str] | None = None,
    include: list[Part] | None = None,
    full_text: bool = False,
    offset: int = 0,
) -> ToolResult:
    """Read text shapes: content, font runs, fill runs, outline, paragraph frame overflow and missing fonts.

    Scope: shape_ids on page_index (non-text IDs are listed in not_text), else every text shape of page_index,
    else of the whole document (master-layer text once). Per shape: id, page_index, layer, kind (artistic,
    paragraph, *_on_path), characters, lines, paragraphs, text (first 120 characters unless full_text), and per
    include (default all): "fonts" font_runs {font, size (pt), bold, italic} and missing_fonts (not installed);
    "fills" fill_runs {fill, whitespace_only}; "outline" one outline or outline_runs; "frame" (paragraph text)
    {overflowing, visible_characters, linked_frames}. Runs are [start, end) character positions (zero-based,
    a line break is one character), as edit_text and set_fill text_range take them. Summary: fonts (installed,
    shape_ids), missing_fonts, overflowing. A large scope may stop early: continue from next_offset until null.
    """
    parts = _PARTS if include is None else tuple(include)
    if unknown := set(parts) - set(_PARTS):
        return ToolResult.fail(f"Unknown include values {sorted(unknown)}; use {', '.join(_PARTS)}")
    if shape_ids is not None and (page_index is None or not isinstance(shape_ids, list) or not shape_ids):
        return ToolResult.fail("shape_ids needs page_index and at least one StaticID")
    if not isinstance(offset, int) or offset < 0:
        return ToolResult.fail("offset must be 0 or more")
    per_character = any(part in parts for part in ("fonts", "fills", "outline"))
    conn = get_connection()

    def read(doc, unit, installed, flat):
        records, position = [], offset
        while position < len(flat):
            view = flat[position][1]
            end = position
            while end < len(flat) and flat[end][1] is view:
                end += 1
            batch = flat[position:end]
            for (number, _, entry), story in zip(batch, _story_rows(view, [e for _, _, e in batch]), strict=True):
                if records and conn.time_left() < _RESERVE_SECONDS:
                    return records, position
                if isinstance(story, Exception):
                    records.append({"id": entry.shape_id, "page_index": number, "layer": view.name,
                                    "unreadable": str(story)})
                else:
                    characters = (_characters(view, entry, story[1])
                                  if per_character and story[1] <= _MAX_CHARACTERS else None)
                    records.append({"page_index": number, **_record(entry.shape_id, view, story, characters, parts,
                                                                    unit, installed, full_text)})
                position += 1
        return records, None

    def run():
        doc, actual_path = resolve_document(conn.app, document_path)
        unit = doc.Unit
        installed = _installed_fonts(conn.app) if "fonts" in parts else {}
        for fresh in (False, True):
            if fresh:
                invalidate(actual_path)
            try:
                flat, not_text = _scope(doc, page_index, shape_ids)
                records, next_offset = read(doc, unit, installed, flat)
                break
            except StaleStructureError:
                if fresh:
                    raise RuntimeError("The page changed while it was being read; retry") from None
        data = {"document_path": actual_path, "document_unit": unit_name(unit), "shapes": records,
                "next_offset": next_offset}
        if page_index is not None:
            data["page_index"] = page_index
        if not_text:
            data["not_text"] = not_text
        if "fonts" in parts:
            fonts = {}
            for record in records:
                for run in record.get("font_runs", []):
                    ids = fonts.setdefault(run["font"], [])
                    if record["id"] not in ids:
                        ids.append(record["id"])
            data["fonts"] = [{"font": name, "installed": name.lower() in installed, "shape_ids": ids[:50]}
                             for name, ids in sorted(fonts.items())]
            data["missing_fonts"] = [entry["font"] for entry in data["fonts"] if not entry["installed"]]
        if "frame" in parts:
            data["overflowing"] = [r["id"] for r in records if r.get("frame", {}).get("overflowing")]
        return data

    result = conn.read_call(run)
    if not result["success"]:
        return respond(result, document_path=document_path, page_index=page_index)
    data = result["result"]
    message = f"{len(data['shapes'])} text shape(s)"
    if data.get("missing_fonts"):
        message += f"; missing fonts: {', '.join(data['missing_fonts'])}"
    if data.get("overflowing"):
        message += f"; {len(data['overflowing'])} overflowing frame(s)"
    if data["next_offset"] is not None:
        message += f"; more: call again with offset={data['next_offset']}"
    return ToolResult.ok(message, **data)


# --- editing -------------------------------------------------------------------------------------------------

def check_text(text) -> str | None:
    """Why CorelDRAW cannot take this text through automation, or None (shared with create_shape)."""
    if not isinstance(text, str):
        return "text must be a string"
    if (ch := next((ch for ch in text if ord(ch) > 0xFFFF), None)) is not None:
        return (f"Character U+{ord(ch):X} is outside the Basic Multilingual Plane; CorelDRAW automation stores it "
                "as a space plus half a character. Use a BMP character or type it in the CorelDRAW UI")
    return None


def normalize_breaks(text: str) -> str:
    """Line breaks as CorelDRAW stores them: one "\\r" per paragraph break."""
    return text.replace("\r\n", "\r").replace("\n", "\r")


def _span(old, new):
    """The changed middle: (start, end) in old and its replacement, keeping the longest common prefix and suffix."""
    prefix = 0
    while prefix < min(len(old), len(new)) and old[prefix] == new[prefix]:
        prefix += 1
    suffix = 0
    while suffix < min(len(old), len(new)) - prefix and old[-1 - suffix] == new[-1 - suffix]:
        suffix += 1
    return prefix, len(old) - suffix, "".join(new[prefix:len(new) - suffix])


def _edit_problems(before, after, plan, unit, style, keep_runs, fit):
    """Read-back differences of one shape: text, the styled range, and formatting outside the edit."""
    start, end, insert, styled_range = plan
    old = [c[0] for c in before]
    expected = old if insert is None else old[:start] + list(insert) + old[end:]
    if [c[0] for c in after] != expected:
        return ["the text differs from the request"]
    new_end = end if insert is None else start + len(insert)
    font, size, bold, italic = style
    for position, character in enumerate(after):
        source = position if position < start else position - new_end + end if position >= new_end else None
        if position in styled_range:
            if font is not None and character[5] != font:
                return [f"character {position} has font {character[5]!r}"]
            if size is not None and abs(character[6] - size) > _SIZE_TOLERANCE:
                return [f"character {position} has size {character[6]}"]
            if (bold is not None and bool(character[7]) != bold) or (italic is not None
                                                                     and bool(character[8]) != italic):
                return [f"character {position} has bold {bool(character[7])}, italic {bool(character[8])}"]
        elif source is not None and not fit and _attributes(character, unit) != _attributes(before[source], unit):
            return [f"character {position} outside the edit changed formatting"]
        if source is None and keep_runs and start != end and position not in styled_range \
                and _attributes(character, unit)[:2] != _attributes(before[start], unit)[:2]:
            return [f"new character {position} did not keep the replaced fill and outline"]
    return []


def edit_text(
    document_path: str,
    page_index: int,
    shape_ids: list[str],
    content: str | None = None,
    text_range: list[int] | None = None,
    font: str | None = None,
    size: float | None = None,
    bold: bool | None = None,
    italic: bool | None = None,
    alignment: Literal["left", "center", "right", "justify", "force_justify", "none"] | None = None,
    fit_to_frame: bool = False,
    keep_runs: bool = True,
) -> ToolResult:
    """Change the content and/or style of exact text shapes, keeping their fill and formatting. One undo step.

    content: the new text ("\\n" is a paragraph break). Only the changed middle is replaced, so the unchanged
    start and end keep their runs: "PANTONE 186 C 160" -> "186 C 200" keeps the fill. With text_range
    [start, end) (inspect_text positions), content replaces exactly that range (start == end inserts), and font,
    size (pt), bold, italic, alignment apply to it (with content: to the new text); without, to the whole text.
    alignment applies to whole paragraphs. fit_to_frame shrinks paragraph text into its frame.
    keep_runs (default true) refuses replacing characters that differ in fill or font, because CorelDRAW gives
    new text the formatting of the replaced range's last character; edit per run or pass keep_runs=false.
    Returns each shape's text, font_runs, fill_runs and frame read back.
    """
    if not isinstance(shape_ids, list) or not shape_ids:
        return ToolResult.fail("shape_ids must be a non-empty list of StaticID strings")
    style = (font, size, bold, italic)
    styled = any(value is not None for value in style)
    if content is None and not styled and alignment is None and not fit_to_frame:
        return ToolResult.fail("Pass content and/or a style (font, size, bold, italic, alignment, fit_to_frame)")
    if content is not None:
        if problem := check_text(content):
            return ToolResult.fail(problem)
        content = normalize_breaks(content)
    if text_range is not None:
        if not (isinstance(text_range, list) and len(text_range) == 2
                and all(isinstance(v, int) and not isinstance(v, bool) for v in text_range)
                and 0 <= text_range[0] <= text_range[1]):
            return ToolResult.fail("text_range is [start, end] with 0 <= start <= end")
        if text_range[0] == text_range[1] and content is None:
            return ToolResult.fail("An empty text_range only inserts content; a style needs start < end")
    if size is not None and (isinstance(size, bool) or not isinstance(size, (int, float)) or not 0 < size <= 3000):
        return ToolResult.fail("size is in points, above 0 and at most 3000")
    if fit_to_frame and size is not None:
        return ToolResult.fail("Pass size or fit_to_frame, not both (fitting sets the size)")
    if alignment is not None and alignment not in _ALIGNMENTS:
        return ToolResult.fail(f"alignment must be one of {', '.join(_ALIGNMENTS)}")

    def body(app, doc, page, targets):
        unit = doc.Unit
        texts = {i: t for i, t in targets.items() if t[1].type == _TEXT}
        kinds = {shape_id: row[0] for shape_id, row in _read(texts, ["$item.Text.Type"]).items()} if texts else {}

        def extra(shape_id, view, entry):
            if entry.type != _TEXT:
                return "not a text shape"
            if fit_to_frame and kinds.get(shape_id) != 1:
                return "fit_to_frame needs paragraph text in a frame"
            return None

        _check(targets, extra)
        resolved_font = font_name(app, font) if font is not None else None
        plans, refused = {}, []
        for shape_id, (view, entry) in targets.items():
            count = read_entries(view, [entry], ["$item.Text.Story.Characters.Count"])[0][0]
            reason = None
            if count > _MAX_CHARACTERS:
                reason = f"{count} characters; edit_text reads at most {_MAX_CHARACTERS}"
            elif text_range is not None and text_range[1] > count:
                reason = f"text_range ends after the text ({count} characters)"
            if reason:
                refused.append({"id": shape_id, "layer": view.name, "reason": reason})
                continue
            before = _characters(view, entry, count)
            if content is None:
                start, end, insert = (*(text_range or [0, count]), None)
            elif text_range is not None:
                start, end, insert = text_range[0], text_range[1], content
            else:
                start, end, insert = _span([c[0] for c in before], list(content))
            if keep_runs and insert is not None and len({_attributes(c, unit) for c in before[start:end]}) > 1:
                refused.append({"id": shape_id, "layer": view.name, "reason": (
                    f"the replaced characters [{start}, {end}) span several formatting runs; replace within "
                    "one run (text_range) or pass keep_runs=false")})
                continue
            new_end = end if insert is None else start + len(insert)
            styled_range = range(start, new_end) if text_range is not None else range(
                count - (end - start) + (new_end - start))
            plans[shape_id] = (view, entry, before, (start, end, insert, styled_range if styled else range(0)))
        if refused:
            raise _RefusedError(refused)

        with _command_group(doc, "MCP edit_text"):
            for shape_id, (view, entry, _, (start, end, insert, _)) in plans.items():
                shape = _com_shape(view, entry, shape_id)
                story = shape.Text.Story
                if insert:
                    story.Range(start, end).Text = insert
                elif insert is not None and start != end:
                    story.Range(start, end).Delete()
                new_end = end if insert is None else start + len(insert)
                target = story if text_range is None else story.Range(start, new_end)
                if styled and (text_range is None or new_end > start):
                    if resolved_font is not None:
                        target.Font = resolved_font
                    if size is not None:
                        target.Size = float(size)
                    if bold is not None:
                        target.Bold = bool(bold)
                    if italic is not None:
                        target.Italic = bool(italic)
                if alignment is not None:
                    target.Alignment = _ALIGNMENTS[alignment]
                if fit_to_frame:
                    shape.Text.FitTextToFrame()

        installed = _installed_fonts(app)
        shapes, problems = [], []
        for shape_id, (view, entry, before, plan) in plans.items():
            story_row = read_entries(view, [entry], _STORY)[0]
            after = _characters(view, entry, story_row[1])
            issues = _edit_problems(before, after, plan, unit, (resolved_font, size, bold, italic), keep_runs,
                                    fit_to_frame)
            record = _record(shape_id, view, story_row, after, ("fonts", "fills", "frame"), unit, installed, False)
            start, end, insert, _ = plan
            if insert is not None:
                record["replaced"] = {"range": [start, end], "old": _clean("".join(c[0] for c in before[start:end])),
                                      "new_range": [start, start + len(insert)]}
                neighbour = before[start] if start < end else before[start - 1] if start else before[end] if                     end < len(before) else None
                fallback = sorted({c[5] for c in after[start:start + len(insert)]} - {neighbour and neighbour[5]})
                if fallback and resolved_font is None:
                    record["font_fallback"] = (f"CorelDRAW set {', '.join(fallback)} on new characters that the "
                                               "font lacks glyphs for")
            if alignment is not None:
                read_back = read_entries(view, [entry], ["$item.Text.Story.Alignment"])[0][0]
                record["alignment"] = _ALIGNMENT_NAMES.get(read_back, read_back)
                if text_range is None and read_back != _ALIGNMENTS[alignment]:
                    issues.append(f"alignment reads {record['alignment']}")
            if fit_to_frame and record.get("frame", {}).get("overflowing"):
                issues.append("the text still overflows its frame")
            shapes.append(record)
            if issues:
                problems.append({"id": shape_id, "problems": issues})
        data = {"shapes": shapes}
        if problems:
            data["verification_errors"] = problems
        return data

    return _edit(document_path, page_index, shape_ids, body)
