"""Inspect CorelDRAW's view and print configuration without submitting output."""

from core.connection import get_connection
from core.models import ToolResult
from tools.document import _scoped_document


def _overprint_view(document_path: str, enabled: bool | None = None) -> ToolResult:
    conn = get_connection()
    if not conn.status.connected:
        return ToolResult.fail("CorelDRAW is not connected")

    def read_view():
        doc, actual_path = _scoped_document(conn.app, document_path)
        view = doc.ActiveWindow.ActiveView
        if enabled is not None:
            view.SimulateOverprints = enabled
        return {"document_path": actual_path, "simulate_overprints": bool(view.SimulateOverprints)}

    result = conn.safe_call(read_view, max_attempts=1 if enabled is not None else None)
    if not result["success"]:
        return ToolResult.fail(result["error"])
    data = result["result"]
    if enabled is not None and data["simulate_overprints"] != enabled:
        return ToolResult(success=False, error="CorelDRAW did not apply the requested view setting", data=data)
    return ToolResult.ok(**data)


def get_simulate_overprints(document_path: str) -> ToolResult:
    """Read Simulate Overprints in the window of the specified active saved drawing."""
    return _overprint_view(document_path)


def set_simulate_overprints(document_path: str, enabled: bool) -> ToolResult:
    """Set Simulate Overprints in the specified active drawing's window and read the state back."""
    return _overprint_view(document_path, enabled)


def get_print_separation_settings(document_path: str) -> ToolResult:
    """Read the active drawing's print configuration and CorelDRAW's available separation plates.

    Plate availability and enabled flags describe configuration for the print range. They
    cannot establish which pages emit a non-empty plate, its image, or RIP output. This
    tool reads current settings without submitting a print job.
    """
    conn = get_connection()
    if not conn.status.connected:
        return ToolResult.fail("CorelDRAW is not connected")

    def read_settings():
        doc, actual_path = _scoped_document(conn.app, document_path)
        settings = doc.PrintSettings
        separations = settings.Separations
        fields = {
            "printer": lambda: settings.Printer.Name, "copies": lambda: settings.Copies,
            "print_range": lambda: settings.PrintRange, "page_range": lambda: settings.PageRange,
            "page_set": lambda: settings.PageSet, "print_to_file": lambda: bool(settings.PrintToFile),
            "separations_enabled": lambda: bool(separations.Enabled), "spot_to_cmyk": lambda: bool(separations.SpotToCMYK),
            "print_empty_plates": lambda: bool(separations.EmptyPlates),
            "preserve_overprints": lambda: bool(separations.PreserveOverprints),
            "available_plates": lambda: _available_plates(separations),
        }
        return {"document_path": actual_path, **_read_fields(fields)}

    result = conn.safe_call(read_settings)
    if result["success"]:
        return ToolResult.ok(**result["result"])
    return ToolResult.fail(result["error"])


def _read_fields(fields):
    result = {}
    unavailable = {}
    for field, read in fields.items():
        try:
            result[field] = read()
        except Exception as exc:
            result[field] = None
            unavailable[field] = str(exc)
    return {**result, "unavailable": unavailable}


def _available_plates(separations):
    plates = separations.Plates
    configured = []
    for index in range(1, plates.Count + 1):
        plate = plates.Item(index)
        fields = {
            "plate_type": lambda: plate.Type, "enabled": lambda: bool(plate.Enabled),
            "label": lambda: plate.Color, "angle": lambda: plate.Angle,
            "frequency": lambda: plate.Frequency,
        }
        configured.append({"index": index, **_read_fields(fields)})
    return configured
