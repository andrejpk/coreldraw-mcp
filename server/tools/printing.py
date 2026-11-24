"""Print a saved document through CorelDRAW's own print engine, with a dry run that lists the plates first."""

from __future__ import annotations

import json
import os
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path

from core.connection import get_connection
from core.models import ToolResult
from core.results import failure
from core.targets import resolve_document

_PRN_PAGE_RANGE = 3  # PrnPrintRange.prnPageRange
_PROCESS = ("Cyan", "Magenta", "Yellow", "Black")
_ledger_lock = threading.Lock()


# --- ledger (pure file work) ---------------------------------------------------------------------------------------

def ledger_path() -> Path:
    """The submission ledger: ``CORELDRAW_MCP_STATE_DIR`` if set, else ``%LOCALAPPDATA%\\coreldraw-mcp``, else temp."""
    base = os.environ.get("CORELDRAW_MCP_STATE_DIR") or (
        Path(os.environ["LOCALAPPDATA"], "coreldraw-mcp") if os.environ.get("LOCALAPPDATA")
        else Path(tempfile.gettempdir(), "coreldraw-mcp"))
    return Path(base, "print_ledger.json")


def _read_ledger(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


def _write_ledger(path: Path, ledger: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = path.with_suffix(".tmp")
    staged.write_text(json.dumps(ledger, indent=1), encoding="utf-8")
    os.replace(staged, path)


def reserve_submission(key: str, record: dict, path: Path | None = None) -> dict | None:
    """Record ``key`` as submitted before printing. Returns the earlier record when the key was used (nothing
    written), else None. Recorded first so a timed-out call that retries cannot print twice."""
    path = path or ledger_path()
    with _ledger_lock:
        ledger = _read_ledger(path)
        if key in ledger:
            return ledger[key]
        ledger[key] = record
        _write_ledger(path, ledger)
    return None


def release_submission(key: str, path: Path | None = None) -> None:
    """Forget a reservation whose print never started, so the same key can be retried."""
    path = path or ledger_path()
    with _ledger_lock:
        ledger = _read_ledger(path)
        if ledger.pop(key, None) is not None:
            _write_ledger(path, ledger)


def update_submission(key: str, changes: dict, path: Path | None = None) -> dict:
    path = path or ledger_path()
    with _ledger_lock:
        ledger = _read_ledger(path)
        ledger[key] = {**ledger.get(key, {}), **changes}
        _write_ledger(path, ledger)
        return ledger[key]


# --- planning (pure) ------------------------------------------------------------------------------------------------

def check_pages(pages, page_count) -> list[int]:
    if (not isinstance(pages, list) or not pages
            or not all(isinstance(p, int) and not isinstance(p, bool) for p in pages)):
        raise ValueError("pages must be a non-empty list of page numbers")
    if len(set(pages)) != len(pages) or any(not 1 <= p <= page_count for p in pages):
        raise ValueError(f"pages must be unique page numbers between 1 and {page_count}")
    return sorted(pages)


def page_range_text(pages: list[int]) -> str:
    """CorelDRAW page range text: runs become "2-4", others are comma separated ("1,3-4")."""
    runs, start, previous = [], None, None
    for page in pages:
        if start is None:
            start = previous = page
        elif page == previous + 1:
            previous = page
        else:
            runs.append((start, previous))
            start = previous = page
    runs.append((start, previous))
    return ",".join(str(a) if a == b else f"{a}-{b}" for a, b in runs)


def plan_page(page_inventory: dict, plate_state: dict, separations: bool, print_empty_plates: bool) -> dict:
    """Plates CorelDRAW will output for one page, and warnings. ``plate_state`` is {plate name: enabled} from the
    print settings' plate list; ``page_inventory`` is a get_ink_inventory page."""
    page = page_inventory["page_index"]
    if not separations:
        return {"page_index": page, "plates": ["Composite (all inks in one output)"], "warnings": []}
    implied = list(page_inventory["plates"]["print"])
    blank = list(page_inventory["plates"].get("blank_spot_plates", []))
    warnings = []
    output, blocked = [], []
    for plate in implied:
        if plate in plate_state and not plate_state[plate]:
            blocked.append(plate)
        else:
            output.append(plate)
    if blocked:
        warnings.append({"code": "used_plate_disabled", "plates": blocked,
                         "message": "These inks are on the page but their plate is switched off in the print "
                                    "settings; CorelDRAW will not output them"})
    missing = [plate for plate in output if plate_state and plate not in plate_state]
    if missing:
        warnings.append({"code": "plate_not_in_print_settings", "plates": missing,
                         "message": "Not in the print settings' plate list; whether CorelDRAW outputs these cannot "
                                    "be confirmed here"})
    if print_empty_plates and blank:
        output += [plate for plate in blank if plate_state.get(plate, True) and plate not in output]
        warnings.append({"code": "empty_plates_print", "plates": blank,
                         "message": "Print Empty Plates is on: plates with no marks print blank"})
    registration = set(page_inventory["plates"].get("registration_only", []))
    skipped = [plate for plate in output if plate in _PROCESS and plate in registration]
    if skipped:
        # Verified against a PostScript file: registration marks alone do not create CMYK plates on a spot job.
        output = [plate for plate in output if plate not in skipped]
        warnings.append({"code": "process_plates_registration_only", "plates": skipped, "severity": "info",
                         "message": "Only registration marks touch these process plates; CorelDRAW does not "
                                    "output them on a spot-color job"})
    process = [plate for plate in output if plate in _PROCESS]
    if process:
        warnings.append({"code": "process_plates_present", "plates": process,
                         "message": "Process (CMYK) plates will be output: process-colour art is on the page"})
    if not output:
        warnings.append({"code": "no_plates", "message": "No plates would be output for this page"})
    return {"page_index": page, "plates": output, "warnings": warnings}


def check_plates(plates, plan: list[dict], plate_state: dict) -> list[str]:
    """The plates to print out of ``plan`` (plan_page results), in the plan's order. Refuses names no page outputs,
    and plates CorelDRAW cannot switch: every plate the pages output must be in the print settings' plate list, or
    printing a subset could still output the others."""
    if (not isinstance(plates, list) or not plates
            or not all(isinstance(plate, str) and plate.strip() for plate in plates)):
        raise ValueError("plates must be a non-empty list of plate (ink) names from the dry run")
    if len(set(plates)) != len(plates):
        raise ValueError("plates must not repeat a name")
    manifest = []
    for page in plan:
        manifest += [plate for plate in page["plates"] if plate not in manifest]
    unknown = [plate for plate in plates if plate not in manifest]
    if unknown:
        raise ValueError(f"Not plates these pages output: {unknown}. The dry run's plates are {manifest}")
    unswitchable = [plate for plate in manifest if plate not in plate_state]
    if unswitchable:
        raise ValueError(f"These plates are not in the print settings' plate list, so a subset cannot be printed "
                         f"safely: {unswitchable}")
    return [plate for plate in manifest if plate in plates]


def only_plates(plan: list[dict], plates: list[str]) -> list[dict]:
    """The plan limited to ``plates``: what a call with them outputs per page."""
    return [{**page, "plates": [plate for plate in page["plates"] if plate in plates]} for page in plan]


# --- COM -------------------------------------------------------------------------------------------------------------

def _installed_printers() -> list[str]:
    import win32print

    return [p[2] for p in win32print.EnumPrinters(win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS)]


def _plate_state(settings) -> dict:
    plates = settings.Separations.Plates
    return {str(plate.Color): bool(plate.Enabled) for plate in (plates.Item(i) for i in range(1, plates.Count + 1))}


def _plate_items(settings) -> list:
    plates = settings.Separations.Plates
    return [plates.Item(i) for i in range(1, plates.Count + 1)]


def _set_plates(settings, enabled: dict) -> None:
    """Switch plates on or off by name ({name: enabled}); plates not named keep their state."""
    for plate in _plate_items(settings):
        name = str(plate.Color)
        if name in enabled and bool(plate.Enabled) != enabled[name]:
            plate.Enabled = enabled[name]


def _snapshot(settings) -> dict:
    return {
        "printer": settings.Printer.Name, "print_range": settings.PrintRange, "page_range": settings.PageRange,
        "copies": settings.Copies, "print_to_file": bool(settings.PrintToFile), "file_name": settings.FileName,
        "separations": bool(settings.Separations.Enabled),
    }


def _apply(settings, values: dict) -> None:
    if values["printer"] != settings.Printer.Name:
        settings.SelectPrinter(values["printer"])
    if values["page_range"] is not None:  # assigning a page range switches PrintRange, so set the range after it
        settings.PageRange = values["page_range"]
    settings.PrintRange = values["print_range"]
    settings.Copies = values["copies"]
    settings.Separations.Enabled = values["separations"]
    settings.PrintToFile = values["print_to_file"]
    if values["file_name"] is not None:
        settings.FileName = values["file_name"]


def _restore_clean(doc) -> str | None:
    """Clear the unsaved-changes flag after a print whose settings were all restored.

    Changing the print settings (printer, range, plates) and setting them back marks the document changed, although
    it matches the saved file again; left set, the next print of the same document is refused. Called only when the
    document was saved before the print and every setting was restored. Returns a warning, or None."""
    try:
        if bool(doc.Dirty):
            doc.Dirty = False
        if bool(doc.Dirty):
            return "the document stays marked as changed after the print"
    except Exception as exc:
        return f"the unsaved-changes flag was not restored: {exc}"
    return None


def _check_printer(printer: str) -> None:
    if not isinstance(printer, str) or not printer:
        raise ValueError("printer must be an exact printer name")
    installed = _installed_printers()
    if printer not in installed:
        raise ValueError(f"Printer {printer!r} is not installed. Installed printers: {installed}")


def print_document(
    document_path: str,
    printer: str,
    pages: list[int],
    separations: bool,
    copies: int = 1,
    dry_run: bool = True,
    submission_key: str = "",
    print_to_file: str = "",
    plates: list[str] | None = None,
) -> ToolResult:
    """Print pages of an open, saved document with CorelDRAW's own print, using the document's existing print
    settings (the shop's established ones). Changes only printer, page range, copies and the separations flag,
    and restores the document's print settings afterwards.

    - document_path: the open document (a saved file).
    - printer: exact installed printer name (required; refused when not installed).
    - pages: page numbers to print, for example [1] or [1, 3] (required).
    - separations: true prints one plate per ink, false prints a composite (required).
    - copies: 1 to 99 (default 1).
    - dry_run: default TRUE: nothing prints. Returns the plan: printer, pages, separations, the plates (ink names)
      CorelDRAW will output on each page (page inks from get_ink_inventory limited by the print settings' plate
      list), plus warnings such as process CMYK plates on a spot job, switched-off plates or empty plates. Check
      the plates before a real submission.
    - dry_run false: prints, and needs submission_key, a unique string for this job. Keys are kept in a ledger
      and a repeated key is refused, so a retry after a timeout cannot print twice (check the printer queue
      first, then use a new key to print again). Returns the submission time and ledger record.
    - print_to_file: optional absolute path; the output goes to that file instead of the printer (also avoids a
      file dialog).
    - plates: optional, with separations true: print only these plates (ink names from the dry run's plates), for
      example ["PANTONE 186 C"] to print one film. Names the pages do not output are refused. Only the named plates
      are switched on for the call and every plate's on/off state is restored afterwards. A dry run with plates
      returns the plan for just those plates; the ledger record names them.
    """
    if not isinstance(copies, int) or isinstance(copies, bool) or not 1 <= copies <= 99:
        return ToolResult.fail("copies must be a whole number between 1 and 99")
    if not isinstance(separations, bool):
        return ToolResult.fail("separations must be true or false")
    if not dry_run and not (isinstance(submission_key, str) and submission_key.strip()):
        return ToolResult.fail("submission_key is required when dry_run is false (a unique string per job)")
    if print_to_file and not (os.path.isabs(print_to_file) and os.path.isdir(os.path.dirname(print_to_file))):
        return ToolResult.fail("print_to_file must be an absolute path in an existing folder")
    if plates is not None and not separations:
        return ToolResult.fail("plates needs separations true: a composite has no plates to choose")
    conn = get_connection()

    def run():
        _check_printer(printer)
        doc, actual_path = resolve_document(conn.app, document_path, activate=not dry_run)
        wanted = check_pages(pages, doc.Pages.Count)
        settings = doc.PrintSettings
        if dry_run:
            return _dry_run(conn, doc, actual_path, settings, wanted)
        return _submit(conn, doc, actual_path, settings, wanted)

    def _plan(conn, doc, settings, wanted):
        from core.targets import resolve_page
        from core.units import unit_name
        from tools.inks import _page_inventory

        state = _plate_state(settings)
        empty = bool(settings.Separations.EmptyPlates)
        unit = unit_name(doc.Unit)
        plan = [plan_page(_page_inventory(conn, doc, resolve_page(doc, page), page, unit, False, 0, True), state,
                          separations, empty) for page in wanted]
        chosen = None if plates is None else check_plates(plates, plan, state)
        return state, empty, plan, chosen

    def _dry_run(conn, doc, actual_path, settings, wanted):
        state, empty, plan, chosen = _plan(conn, doc, settings, wanted)
        if chosen is not None:
            plan = only_plates(plan, chosen)
        warnings = []
        if settings.Printer.Name != printer:
            warnings.append({"code": "printer_differs", "message": (
                f"The document's print settings use {settings.Printer.Name!r}; its plate list belongs to that "
                f"printer and is applied to {printer!r} unchanged")})
        if bool(doc.Dirty):
            warnings.append({"code": "unsaved_changes", "message": "The document has unsaved changes; a real "
                             "submission is refused until it is saved"})
        return {"document_path": actual_path, "dry_run": True, "printed": False, "printer": printer,
                "pages": wanted, "page_range": page_range_text(wanted), "copies": copies,
                "separations": separations, "print_to_file": print_to_file or None, "plates": chosen,
                "document_settings": {"printer": settings.Printer.Name, "print_empty_plates": empty,
                                      "disabled_plates": sorted(p for p, on in state.items() if not on)},
                "plan": plan, "warnings": warnings}

    def _submit(conn, doc, actual_path, settings, wanted):
        if bool(doc.Dirty):
            raise ValueError("The document has unsaved changes; save it first so the print matches the file")
        state, chosen = None, None
        if plates is not None:
            state, _, _, chosen = _plan(conn, doc, settings, wanted)
        original = _snapshot(settings)
        wanted_values = {"printer": printer, "print_range": _PRN_PAGE_RANGE, "page_range": page_range_text(wanted),
                         "copies": copies, "separations": separations,
                         "print_to_file": bool(print_to_file), "file_name": print_to_file or None}
        record = {"document_path": actual_path, "pages": wanted, "printer": printer, "separations": separations,
                  "copies": copies, "print_to_file": print_to_file or None, "plates": chosen, "status": "submitting",
                  "time": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        previous = reserve_submission(submission_key, record)
        if previous is not None:
            return {"duplicate": True, "ledger_record": previous}
        restore_error = None
        started = False
        try:
            _apply(settings, wanted_values)
            applied = _snapshot(settings)
            for field in ("printer", "print_range", "page_range", "copies", "separations", "print_to_file"):
                if str(applied[field]) != str(wanted_values[field]):
                    raise RuntimeError(f"CorelDRAW did not accept {field}: wanted {wanted_values[field]!r}, "
                                       f"got {applied[field]!r}")
            if chosen is not None:
                wanted_plates = {name: name in chosen for name in state}
                _set_plates(settings, wanted_plates)
                if _plate_state(settings) != wanted_plates:
                    raise RuntimeError(f"CorelDRAW did not switch the plates to {chosen}")
            started = True
            settings.PrintOut()
            record = update_submission(submission_key, {"status": "submitted"})
        except Exception as exc:
            if started:  # PrintOut may have queued the job: keep the key so a retry cannot print twice
                update_submission(submission_key, {"status": "error", "error": str(exc)})
            else:
                release_submission(submission_key)
            raise
        finally:
            try:
                _apply(settings, original)
                if state is not None:
                    _set_plates(settings, state)
                if _snapshot(settings) != original or (state is not None and _plate_state(settings) != state):
                    restore_error = "print settings were not fully restored"
            except Exception as exc:
                restore_error = f"print settings were not restored: {exc}"
            if restore_error is None:
                restore_error = _restore_clean(doc)
        result = {"document_path": actual_path, "dry_run": False, "printed": True, "submission_key": submission_key,
                  "submitted_at": record["time"], "ledger_record": record, "ledger_file": str(ledger_path()),
                  "restored_settings": original}
        if restore_error:
            result["warning"] = restore_error
        return result

    result = conn.safe_call(run)
    if not result["success"]:
        return failure(result, document_path=document_path)
    data = result["result"] or {}
    if data.get("duplicate"):
        return ToolResult(success=False, data=data, error=(
            "submission_key was already used: nothing was printed. Check the printer queue; use a new key only "
            "to print again deliberately"))
    return ToolResult.ok(**data)
