"""Operate on CorelDRAW effects and overprint flags using exact shape IDs."""

import math
from typing import Literal

from core.drawing import CDR_GROUP_SHAPE
from core.models import ToolResult
from core.targets import resolve_page
from core.units import unit_code, unit_name
from tools.inspection import _CDR_UNIFORM_FILL, _color_details, _exact_shapes, _on_shape
from tools.shapes import _static_id

_CDR_CONTOUR = 6
_CDR_DIRECT_BLEND = 0
_CDR_TENTH_MICRON = 0
_CONTOUR_DIRECTIONS = {"inside": 0, "outside": 1, "to_center": 2}
_DIRECTION_NAMES = {value: name for name, value in _CONTOUR_DIRECTIONS.items()}
_OVERPRINT_PROPERTIES = {"fill": "OverprintFill", "outline": "OverprintOutline"}


def _overprint_flags(shape):
    return {name: bool(getattr(shape, attr)) for name, attr in _OVERPRINT_PROPERTIES.items()}


def get_shape_overprint(document_path: str, page_index: int, shape_id: str) -> ToolResult:
    """Read separate fill and outline overprint flags on one exact shape in an exact open document."""
    return _on_shape(document_path, page_index, shape_id, lambda app, doc, shape: _overprint_flags(shape))


def set_shape_overprint(
    document_path: str, page_index: int, shape_id: str, part: Literal["fill", "outline"], enabled: bool,
) -> ToolResult:
    """Set one overprint flag on an exact non-group shape and read back both flags."""
    if part not in _OVERPRINT_PROPERTIES:
        return ToolResult.fail("part must be fill or outline")

    def set_flag(app, doc, shape):
        if shape.Type == CDR_GROUP_SHAPE:
            raise ValueError("Use the exact ID of a child shape to change its overprint flag")
        expected = _overprint_flags(shape)
        expected[part] = enabled
        setattr(shape, _OVERPRINT_PROPERTIES[part], enabled)
        flags = _overprint_flags(shape)
        if flags != expected:
            flags["verification_error"] = "Overprint readback differs from the expected flags"
        return flags

    return _on_shape(document_path, page_index, shape_id, set_flag, write=True)


def _contours(shape):
    return [effect for effect in shape.Effects if effect.Type == _CDR_CONTOUR]


def _contour_details(effect, unit):
    contour = effect.Contour
    return {
        "direction": _DIRECTION_NAMES.get(contour.Direction, "unknown"),
        "offset": contour.Offset,
        "document_unit": unit_name(unit),
        "steps": contour.Steps,
        "fill_color": _color_details(contour.FillColor),
    }


def inspect_contours(document_path: str, page_index: int, shape_id: str) -> ToolResult:
    """Read contour directions, offsets in document units, step counts, and fill colors on an exact shape."""
    def inspect(app, doc, shape):
        return {"contours": [_contour_details(effect, doc.Unit) for effect in _contours(shape)]}

    return _on_shape(document_path, page_index, shape_id, inspect)


def apply_contour(
    document_path: str, page_index: int, shape_id: str,
    direction: Literal["inside", "outside"], offset: float, unit: Literal["mm", "cm", "in", "inch", "pt"], steps: int,
    fill_from_shape_id: str | None = None,
) -> ToolResult:
    """Apply an explicit contour to an exact source with no existing contour and read back the effect.

    Optionally use the uniform fill of another exact shape on the page as the contour color.
    Otherwise CorelDRAW supplies the default color. Check the returned fill before using it in a separation.
    """
    if direction not in ("inside", "outside") or unit not in ("mm", "cm", "in", "inch", "pt"):
        return ToolResult.fail("Specify inside/outside and one of mm, cm, in, or pt")
    if not math.isfinite(offset) or offset <= 0 or steps < 1:
        return ToolResult.fail("offset must be finite and positive, and steps must be at least one")

    def apply(app, doc, shape):
        if _contours(shape):
            raise ValueError("The source already has a contour. Inspect it before proceeding.")
        fill_color = None
        if fill_from_shape_id is not None:
            donor, = _exact_shapes(doc, resolve_page(doc, page_index), [fill_from_shape_id])
            if donor.Fill.Type != _CDR_UNIFORM_FILL:
                raise ValueError("The fill donor must have a uniform fill")
            fill_color = donor.Fill.UniformColor
        document_offset = app.ConvertUnits(offset, unit_code(unit), doc.Unit)
        # CorelDRAW can round effect distances to its internal measurement resolution.
        tolerance = app.ConvertUnits(1, _CDR_TENTH_MICRON, doc.Unit)
        if document_offset < tolerance:
            raise ValueError("The contour offset is below CorelDRAW's measurement resolution")
        # Dynamic pywin32 requires explicit nulls for the optional Color dispatch arguments.
        effect = shape.CreateContour(
            _CONTOUR_DIRECTIONS[direction], document_offset, steps, _CDR_DIRECT_BLEND, None, fill_color, None,
        )
        details = _contour_details(effect, doc.Unit)
        data = {"contour": details}
        if (details["direction"] != direction or details["steps"] != steps
                or not math.isclose(details["offset"], document_offset, rel_tol=0, abs_tol=tolerance)):
            data["verification_error"] = "CorelDRAW created a contour with different parameters. Inspect it before editing."
        return data

    return _on_shape(document_path, page_index, shape_id, apply, write=True)


def break_contour_apart(document_path: str, page_index: int, shape_id: str) -> ToolResult:
    """Separate the sole contour on the exact source and return source, result, and result leaf shape IDs."""
    def separate(app, doc, shape):
        effects = _contours(shape)
        if len(effects) != 1:
            raise ValueError(f"Expected exactly one contour on the source. Found {len(effects)}.")
        result = effects[0].Separate()
        shapes = [result.Item(i) for i in range(1, result.Count + 1)]
        generated = [item for item in shapes if _static_id(item) != shape_id]
        result_ids = [_static_id(item) for item in generated]
        leaf_ids = [leaf for item in generated for leaf in _leaf_ids(item)]
        data = {"source_shape_id": shape_id, "result_shape_ids": result_ids, "result_leaf_shape_ids": leaf_ids}
        if not result_ids or _contours(shape):
            data["verification_error"] = "Contour separation could not be confirmed. Inspect the page before retrying."
        return data

    return _on_shape(document_path, page_index, shape_id, separate, write=True)


def _leaf_ids(shape):
    if shape.Type == CDR_GROUP_SHAPE:
        for child in shape.Shapes:
            yield from _leaf_ids(child)
    else:
        yield _static_id(shape)
