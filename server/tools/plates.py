"""Plate-level feedback: publish a page proof, render its films with Ghostscript, measure and explain each plate."""

import json
import math
import re
import time
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
from core import separations as seps
from core.connection import get_connection
from core.models import ToolResult
from core.targets import resolve_document, resolve_page
from core.units import convert, unit_name
from tools.inks import PROCESS_PLATES, _OutOfTimeError, _page_inventory
from tools.proof import publish_proof

_PAD_PX = 2  # object bounds exclude outline width and antialiasing; widen them for attribution
# Unattributed art smaller than this is edge noise (miter joins, pixel snapping): px, and share of the plate's art.
_MIN_UNATTRIBUTED_PX, _MIN_UNATTRIBUTED_SHARE = 25, 0.0005
_INSET_UNITS = ("pt", "px", "mm", "in")
_MAX_INSET_PX = 60
_MAX_CONTAIN = 8
_ESTIMATED = {"rgb", "other", "bitmap"}


def _plate_key(name):
    return re.sub(r"[^0-9a-z]", "", name.lower())


def _safe_file(name):
    return re.sub(r"[^0-9A-Za-z._-]+", "_", name).strip("_")[:60] or "plate"


def _check_contain(contain):
    if contain is None:
        return []
    if not isinstance(contain, list) or len(contain) > _MAX_CONTAIN:
        raise ValueError(f"contain must be a list of at most {_MAX_CONTAIN} checks")
    checks = []
    for spec in contain:
        if not isinstance(spec, dict) or not isinstance(spec.get("plate"), str) or not spec.get("within"):
            raise ValueError('Each contain check needs "plate" (a plate name) and "within" (a list of plate names)')
        within = spec["within"]
        if isinstance(within, str) or not all(isinstance(name, str) for name in within):
            raise ValueError('"within" must be a list of plate names')
        unit = spec.get("unit", "pt")
        if unit not in _INSET_UNITS:
            raise ValueError(f'"unit" must be one of {", ".join(_INSET_UNITS)}')
        amount = spec.get("inset", 0)
        if not isinstance(amount, (int, float)):
            raise ValueError('"inset" must be a number')
        region = spec.get("region")
        if region is not None and (not isinstance(region, list) or len(region) != 4
                                   or not all(isinstance(value, (int, float)) for value in region)):
            raise ValueError('"region" must be [left, bottom, right, top] in document units')
        checks.append({"plate": spec["plate"], "within": list(within), "inset": float(amount), "unit": unit,
                       "region": region})
    return checks


def _output_folder(output_dir):
    folder = Path(output_dir)
    if not folder.is_absolute():
        raise ValueError("output_dir must be an absolute folder path")
    if folder.exists():
        if not folder.is_dir() or any(folder.iterdir()):
            raise ValueError("output_dir must be a new or empty folder")
    elif not folder.parent.is_dir():
        raise ValueError("The parent of output_dir must exist")
    return folder


class _Geometry:
    """Pixel <-> document coordinates of one rendered page."""

    def __init__(self, page_bounds, size, unit, dpi):
        self.left, self.bottom = page_bounds["left"], page_bounds["bottom"]
        self.right, self.top = page_bounds["right"], page_bounds["top"]
        self.width_px, self.height_px = size
        self.sx = (self.right - self.left) / self.width_px
        self.sy = (self.top - self.bottom) / self.height_px
        self.unit = unit
        self.dpi = dpi
        self.pixel_area = self.sx * self.sy

    def box(self, rect):
        if rect is None:
            return None
        x0, y0, x1, y1 = rect
        left, right = self.left + x0 * self.sx, self.left + x1 * self.sx
        top, bottom = self.top - y0 * self.sy, self.top - y1 * self.sy
        return {"left": round(left, 4), "bottom": round(bottom, 4), "right": round(right, 4), "top": round(top, 4),
                "width": round(right - left, 4), "height": round(top - bottom, 4), "px": list(rect)}

    def rect(self, bounds, pad=0, grow=0.0):
        """Pixel rectangle (x0, y0, x1, y1) of document bounds (left, bottom, right, top) grown by ``grow`` document
        units and ``pad`` pixels, clipped to the page; None if outside."""
        left, bottom, right, top = bounds[0] - grow, bounds[1] - grow, bounds[2] + grow, bounds[3] + grow
        x0 = max(0, math.floor((left - self.left) / self.sx) - pad)
        x1 = min(self.width_px, math.ceil((right - self.left) / self.sx) + pad)
        y0 = max(0, math.floor((self.top - top) / self.sy) - pad)
        y1 = min(self.height_px, math.ceil((self.top - bottom) / self.sy) + pad)
        return (x0, y0, x1, y1) if x0 < x1 and y0 < y1 else None

    def area(self, pixels):
        return round(pixels * self.pixel_area, 6)


def _included(objects, layer_name, include_hidden_printable):
    """Objects that the proof PDF can contain (by state and layer), and the states that count."""
    states = {"prints", "hidden_printable"} if layer_name or include_hidden_printable else {"prints"}
    return [obj for obj in objects if obj["state"] in states
            and (layer_name is None or (obj["layer"] == layer_name and not obj["master"]))]


def _prediction(objects, layer_name, include_hidden_printable):
    """Predicted proof plates: {plate: {"marked": bool, "kinds": set}} plus registration objects."""
    marking = _included(objects, layer_name, include_hidden_printable)
    plates, registration = {}, []
    for obj in marking:
        for part, _ink, kind, obj_plates in obj["uses"]:
            if part == "text_whitespace":
                continue
            if kind == "registration":
                registration.append(obj)
                continue
            for plate in obj_plates:
                entry = plates.setdefault(plate, {"marked": True, "kinds": set()})
                entry["kinds"].add(kind)
    if registration:
        # Registration paints every plate in the PDF: the process plates and every spot that the published
        # content names (also off-page, clipped or whitespace-only uses; E03).
        named_states = {"prints", "hidden_printable", "off_page", "clipped"}
        scope = [obj for obj in objects if obj["state"] in named_states
                 and (layer_name is None or (obj["layer"] == layer_name and not obj["master"]))]
        spots = {obj_plates[0] for obj in scope for _part, _ink, kind, obj_plates in obj["uses"]
                 if kind == "spot" and obj_plates}
        for plate in (*PROCESS_PLATES, *sorted(spots)):
            plates.setdefault(plate, {"marked": False, "kinds": set()})
    return plates, registration


def render_plates(
    document_path: str,
    page_index: int,
    output_dir: str,
    layer_name: str | None = None,
    include_hidden_printable: bool = False,
    dpi: int = 150,
    contain: list[dict] | None = None,
    max_ids_per_plate: int = 10,
) -> ToolResult:
    """Render what each film of one page will contain, measure every plate, and explain it, in one call.

    Publishes the page (or one page layer, layer_name) like export_page_proof_pdf, renders it with
    Ghostscript (tiffsep, process colors as written), and writes to output_dir (a new or empty absolute
    folder): proof.pdf, one film PNG per plate (black = ink), composite.png (all plates, overprints
    simulated) and plates.json. No image data is returned. dpi 36-300 (default 150).

    Per plate: non_blank; coverage_percent and inked_area of the page; bbox; registration_only.
    Registration (content on every plate at the same pixels, inside registration objects) is separated:
    art_* fields exclude it. Bounding boxes are in document units and page coordinates, like
    inspect_shapes bounds (left/bottom/right/top), plus px. attributed_ids: StaticIDs whose ink makes this
    plate and whose bounds overlap its art (approximate: bounding boxes, not exact shapes), largest first;
    unattributed_art: art no such object covers. differences compares rendered plates with the ink
    inventory: expected_plate_blank, unexpected_plate, art_missing (only registration rendered),
    process_plate_from_bitmaps_only, estimated_colors, unattributed_art.

    Hidden layers that are printable are omitted from the PDF (CorelDRAW print may include them): a
    warning names them; include_hidden_printable=true makes them visible while publishing (restored).

    contain: optional checks [{"plate": "A", "within": ["B", "C"], "inset": 0.5, "unit": "pt"|"px"|"mm"|"in",
    "region": [left, bottom, right, top]}]: art of plate A outside the union of the within plates' art,
    shrunk by inset (negative grows it), optionally only inside region. Returns protrusion area, percent,
    bbox and the largest pieces, e.g. to verify an underbase choke.
    """
    started = time.perf_counter()
    timings = {}
    try:
        folder = _output_folder(output_dir)
        checks = _check_contain(contain)
        if not 36 <= dpi <= 300:
            raise ValueError("dpi must be between 36 and 300")
        if not 0 <= max_ids_per_plate <= 50:
            raise ValueError("max_ids_per_plate must be between 0 and 50")
        if layer_name is not None and include_hidden_printable:
            raise ValueError("include_hidden_printable applies to whole-page renders, not layer_name renders")
        ghostscript = seps.find_ghostscript()
    except (ValueError, FileNotFoundError) as exc:
        return ToolResult.fail(str(exc))
    conn = get_connection()

    def inventory():
        doc, actual_path = resolve_document(conn.app, document_path)
        page = resolve_page(doc, page_index)
        unit = unit_name(doc.Unit)
        try:
            data = _page_inventory(conn, doc, page, page_index, unit, False, 0, False, collect=True)
        except _OutOfTimeError:
            raise TimeoutError(f"Page {page_index} could not be read within the time limit") from None
        return actual_path, unit, data

    result = conn.read_call(inventory)
    if not result["success"]:
        return ToolResult.fail(result["error"])
    actual_path, unit, page_data = result["result"]
    timings["inventory"] = round(time.perf_counter() - started, 2)

    folder.mkdir(exist_ok=True)
    pdf = folder / "proof.pdf"
    mark = time.perf_counter()
    result = conn.safe_call(lambda: publish_proof(conn.app, actual_path, page_index, pdf, layer_name,
                                                  include_hidden_printable))
    if not result["success"]:
        return ToolResult.fail(result["error"])
    proof = result["result"]
    timings["export"] = round(time.perf_counter() - mark, 2)

    # Everything below runs outside the CorelDRAW bridge: other tools can use CorelDRAW meanwhile.
    mark = time.perf_counter()
    try:
        with TemporaryDirectory(prefix="render-", dir=folder) as scratch:
            composite, rendered = seps.render_tiffsep(pdf, Path(scratch), ghostscript, dpi)
    except (RuntimeError, OSError, TimeoutError) as exc:
        return ToolResult.fail(f"Rendering failed: {exc}")
    timings["ghostscript"] = round(time.perf_counter() - mark, 2)

    mark = time.perf_counter()
    report = _analyze(page_data, unit, dpi, composite, rendered, folder, layer_name, include_hidden_printable,
                      checks, max_ids_per_plate)
    report["proof"] = {key: value for key, value in proof.items() if key not in ("document_path", "page_index")}
    timings["analysis"] = round(time.perf_counter() - mark, 2)
    timings["total"] = round(time.perf_counter() - started, 2)
    report["seconds"] = timings
    (folder / "plates.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    return ToolResult.ok(document_path=actual_path, page_index=page_index, output_dir=str(folder), **report)


def _analyze(page_data, unit, dpi, composite, rendered, folder, layer_name, include_hidden_printable, checks,
             max_ids):
    width_px, height_px = composite.size
    geometry = _Geometry(page_data["page_bounds"], (width_px, height_px), unit, dpi)
    warnings = []
    expected_w = convert(page_data["page_bounds"]["right"] - page_data["page_bounds"]["left"], unit, "in") * dpi \
        if unit in ("in", "mm", "cm", "pt") else width_px
    if abs(expected_w - width_px) > 2:
        warnings.append({"code": "pdf_size_differs_from_page",
                         "message": f"Rendered width {width_px} px, page width at {dpi} dpi {expected_w:.0f} px: "
                                    "bounding boxes are scaled to the page"})
    composite_path = folder / "composite.png"
    composite.save(composite_path, compress_level=1)

    objects = page_data["objects"]
    predicted, registration_objects = _prediction(objects, layer_name, include_hidden_printable)
    included = _included(objects, layer_name, include_hidden_printable)

    masks = {name: ink > 0 for name, ink in rendered}
    non_blank = [name for name, mask in masks.items() if mask.any()]
    registration = np.zeros((height_px, width_px), dtype=bool)
    if registration_objects and len(non_blank) >= 2:
        within = np.zeros_like(registration)
        for obj in registration_objects:
            rect = geometry.rect(obj["bounds"], _PAD_PX, obj["outline_width"] / 2) if obj["bounds"] else None
            if rect:
                within[rect[1]:rect[3], rect[0]:rect[2]] = True
        registration = within
        for name in non_blank:
            registration &= masks[name]
    page_pixels = width_px * height_px

    plates, art_masks = [], {}
    for index, (name, ink) in enumerate(rendered, start=1):
        mask = masks[name]
        art = mask & ~registration
        art_masks[name] = art
        png = folder / f"plate-{index:02d}-{_safe_file(name)}.png"
        seps.save_plate(ink, png)
        inked = int(mask.sum())
        art_pixels = int(art.sum())
        plate = {"plate": name, "kind": "process" if name in PROCESS_PLATES else "spot", "non_blank": inked > 0,
                 "registration_only": inked > 0 and art_pixels == 0,
                 "coverage_percent": round(100 * inked / page_pixels, 3), "inked_area": geometry.area(inked),
                 "bbox": geometry.box(seps.bbox(mask))}
        if inked and art_pixels != inked:
            plate.update(art_coverage_percent=round(100 * art_pixels / page_pixels, 3),
                         art_area=geometry.area(art_pixels), art_bbox=geometry.box(seps.bbox(art)))
        if art_pixels:
            plate.update(_attribute(name, art, included, geometry, max_ids))
        plate["png"] = str(png)
        plates.append(plate)

    differences = _differences(plates, predicted, included)
    hidden = [row["name"] for row in page_data["layers"] if row.get("hidden_printable") and row["objects"]]
    if hidden and not include_hidden_printable and not layer_name:
        warnings.append({"code": "hidden_printable_layers_omitted", "layers": hidden,
                         "message": "These hidden layers are printable: CorelDRAW print may include them, but the "
                                    "PDF proof omits them. Use include_hidden_printable=true to render them"})
    result = {
        "document_unit": unit, "area_unit": f"{unit}2", "dpi": dpi, "size_px": [width_px, height_px],
        "page_bounds": page_data["page_bounds"], "layer_name": layer_name,
        "include_hidden_printable": include_hidden_printable,
        "registration_detected": bool(registration.any()),
        "predicted_plates": sorted(predicted, key=_plate_order),
        "rendered_plates": [plate["plate"] for plate in plates if plate["non_blank"]],
        "plates": plates, "differences": differences, "warnings": warnings,
        "composite_png": str(composite_path),
        "render_source": "Ghostscript tiffsep of CorelDRAW Publish to PDF (not printer or RIP output)",
    }
    if checks:
        result["containment"] = [_containment(check, art_masks, geometry) for check in checks]
    return result


def _plate_order(name):
    order = list(PROCESS_PLATES)
    return (name in order, order.index(name) if name in order else 0, name)


def _contributors(name, objects):
    """[(object, inks, kinds)] whose (non-registration) inks put ink on plate ``name``.

    RGB, other color models and bitmaps count for every process plate: their separation depends on the color
    conversion (Ghostscript's differs from the inventory's estimate).
    """
    key = _plate_key(name)
    process = name in PROCESS_PLATES
    out = []
    for obj in objects:
        inks, kinds = [], set()
        for part, ink, kind, obj_plates in obj["uses"]:
            if part == "text_whitespace" or kind == "registration":
                continue
            if any(_plate_key(plate) == key for plate in obj_plates) or (process and kind in _ESTIMATED):
                if ink not in inks:
                    inks.append(ink)
                kinds.add(kind)
        if inks and obj["bounds"]:
            out.append((obj, inks, kinds))
    return out


def _attribute(name, art, objects, geometry, max_ids):
    table = seps.integral(art)
    covered = np.zeros_like(art)
    found = []
    for obj, inks, kinds in _contributors(name, objects):
        rect = geometry.rect(obj["bounds"], _PAD_PX, obj["outline_width"] / 2)
        if rect is None:
            continue
        pixels = seps.count_in(table, rect)
        if pixels:
            covered[rect[1]:rect[3], rect[0]:rect[2]] = True
            found.append((pixels, obj, inks, kinds))
    found.sort(key=lambda item: -item[0])
    out = {"attributed_count": len(found),
           "attributed_ids": [{"id": obj["id"], "layer": obj["layer"], "inks": inks, "art_px_in_bbox": pixels}
                              for pixels, obj, inks, _ in found[:max_ids]],
           "attributed_kinds": sorted({kind for *_, kinds in found for kind in kinds})}
    rest = art & ~covered
    rest_pixels = int(rest.sum())
    if rest_pixels >= max(_MIN_UNATTRIBUTED_PX, _MIN_UNATTRIBUTED_SHARE * int(art.sum())):
        out["unattributed_art"] = {"area": geometry.area(rest_pixels), "px": rest_pixels,
                                   "bbox": geometry.box(seps.bbox(rest))}
    return out


def _differences(plates, predicted, objects):
    rendered = {_plate_key(plate["plate"]): plate for plate in plates}
    expected = {_plate_key(name): (name, info) for name, info in predicted.items()}
    differences = []
    for key, (name, info) in expected.items():
        plate = rendered.get(key)
        if plate is None or not plate["non_blank"]:
            reason = ("the PDF has no such plate" if plate is None else "the plate is blank")
            if info["kinds"] and info["kinds"] <= _ESTIMATED:
                reason += "; its ink comes only from colors whose separation is estimated"
            differences.append({"type": "expected_plate_blank", "plate": name, "detail": reason})
        elif info["marked"] and plate["registration_only"]:
            differences.append({"type": "art_missing", "plate": name,
                                "detail": "objects with this ink should print, but only registration rendered "
                                          "(covered, clipped or knocked out?)"})
    for key, plate in rendered.items():
        if plate["non_blank"] and key not in expected:
            nearby = [obj["id"] for obj in objects if obj["bounds"] and plate.get("art_bbox", plate["bbox"])
                      and _overlaps(obj["bounds"], plate.get("art_bbox") or plate["bbox"])][:10]
            differences.append({"type": "unexpected_plate", "plate": plate["plate"],
                                "detail": "not predicted by the ink inventory", "bbox": plate.get("art_bbox")
                                or plate["bbox"], "ids_overlapping": nearby})
    for plate in plates:
        kinds = set(plate.get("attributed_kinds", []))
        if plate["kind"] == "process" and plate.get("attributed_count") and kinds:
            if kinds <= {"bitmap"}:
                differences.append({"type": "process_plate_from_bitmaps_only", "plate": plate["plate"],
                                    "detail": "only bitmaps put this process ink on the film"})
            elif kinds <= _ESTIMATED:
                differences.append({"type": "estimated_colors", "plate": plate["plate"],
                                    "detail": "only RGB/other-model colors or bitmaps put ink on this process film; "
                                              "the separation depends on color conversion"})
        if plate.get("unattributed_art"):
            differences.append({"type": "unattributed_art", "plate": plate["plate"],
                                "detail": "ink that no object with this ink covers (by bounding box)",
                                **plate["unattributed_art"]})
    return differences


def _overlaps(bounds, box):
    left, bottom, right, top = bounds
    return not (right < box["left"] or left > box["right"] or top < box["bottom"] or bottom > box["top"])


def _containment(check, art_masks, geometry):
    keys = {_plate_key(name): name for name in art_masks}
    missing = [name for name in [check["plate"], *check["within"]] if _plate_key(name) not in keys]
    out = {"plate": check["plate"], "within": check["within"], "inset": check["inset"], "unit": check["unit"]}
    if missing:
        return {**out, "error": f"No rendered plate named {', '.join(missing)}; plates: {', '.join(art_masks)}"}
    radius = check["inset"] if check["unit"] == "px" else convert(check["inset"], check["unit"], "in") * geometry.dpi
    if abs(radius) > _MAX_INSET_PX:
        return {**out, "error": f"inset is {radius:.1f} px at this dpi; the limit is {_MAX_INSET_PX} px"}
    subject = art_masks[keys[_plate_key(check["plate"])]].copy()
    union = np.zeros_like(subject)
    for name in check["within"]:
        union |= art_masks[keys[_plate_key(name)]]
    if check["region"]:
        left, bottom, right, top = check["region"]
        rect = geometry.rect((left, bottom, right, top))
        limit = np.zeros_like(subject)
        if rect:
            limit[rect[1]:rect[3], rect[0]:rect[2]] = True
        subject &= limit
    subject_pixels = int(subject.sum())
    # Erode only around the subject (plus the inset reach) to keep large pages fast.
    box = seps.bbox(subject)
    protrusion = np.zeros_like(subject)
    if box:
        reach = int(abs(radius)) + 1
        x0, y0 = max(0, box[0] - reach), max(0, box[1] - reach)
        x1, y1 = min(subject.shape[1], box[2] + reach), min(subject.shape[0], box[3] + reach)
        shrunk = seps.inset(union[y0:y1, x0:x1], radius)
        protrusion[y0:y1, x0:x1] = subject[y0:y1, x0:x1] & ~shrunk
    pixels = int(protrusion.sum())
    out.update(inset_px=round(radius, 2), plate_art_px=subject_pixels, protrusion_px=pixels,
               protrusion_area=geometry.area(pixels),
               protrusion_percent=round(100 * pixels / subject_pixels, 3) if subject_pixels else 0.0,
               protrusion_bbox=geometry.box(seps.bbox(protrusion)) if pixels else None)
    if pixels:
        pieces = seps.components(protrusion)
        if pieces is not None:
            out["largest_pieces"] = [{"px": size, "area": geometry.area(size), "bbox": geometry.box(rect)}
                                     for size, rect in pieces]
    return out

