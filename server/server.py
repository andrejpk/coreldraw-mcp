import argparse
import os
import struct
import sys
from pathlib import Path

from core.connection import close_connection, get_connection
from core.models import ToolResult
from core.results import CorelTool
from dotenv import load_dotenv
from fastmcp import FastMCP
from loguru import logger
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse

_ROOT = Path(__file__).resolve().parent.parent
_HTTP_OPTIONS = {"path": "/mcp", "stateless_http": True, "json_response": True, "host_origin_protection": True}
INSTRUCTIONS = """CorelDRAW automation. Conventions shared by every tool:
- document_path: an open document's full path as list_documents reports it (an unsaved document's title).
  Reads work on any open document; edits activate their document, because CorelDRAW undoes the active one.
- page_index and pages are one-based. shape_ids are StaticID strings of that page, from inspect_shapes or
  find_shapes.
- unit: "in", "mm", "cm" or "pt". Results report lengths in document_unit unless they name a unit. Bounds are
  [left, bottom, right, top] in page coordinates, y up.
- Colors are color specs, documented on set_fill.
- Edit tools are one undo step (undo reverts it) and read their result back. They refuse the whole call,
  changing nothing, for locked or master layers, effect-generated objects and other unsupported targets, and
  list each target in data.refused.
- Every tool returns {success, message, data, error}. A failed call's error says what to do next, and
  data.state tells CorelDRAW problems (busy or timeout: retry or poll get_coreldraw_status; disconnected,
  not_running, unavailable) from a request to fix (tool_error).
"""
mcp = FastMCP("coreldraw-signage", instructions=INSTRUCTIONS)

# Tool annotations (MCP hints; the defaults are read-write, destructive, not idempotent).
READ = {"readOnlyHint": True}
ADDS = {"destructiveHint": False}
ADDS_SAME = {"destructiveHint": False, "idempotentHint": True}
SETS = {"idempotentHint": True}


def _lean_schema(schema: dict) -> dict:
    """Drop the null branch and null default of optional parameters: omitting one means the same thing.

    Arguments are still validated against the function signature, so an explicit null keeps working.
    """
    for prop in schema.get("properties", {}).values():
        branches = prop.get("anyOf")
        if branches and {"type": "null"} in branches and "default" in prop and prop["default"] is None:
            rest = [branch for branch in branches if branch != {"type": "null"}]
            del prop["anyOf"], prop["default"]
            if len(rest) == 1:
                prop.update(rest[0])
            else:
                prop["anyOf"] = rest
    return schema


def add(fn, hints=None):
    """Register a tool function: docstring as description, no output schema (every tool returns the same
    ToolResult object, still sent as structured content; a failure has isError true), lean input schema, MCP
    annotations."""
    tool = CorelTool.from_function(fn, output_schema=None, annotations=hints)
    tool.parameters = _lean_schema(tool.parameters)
    mcp.add_tool(tool)


def setup_logging(*, http):
    logger.remove()
    if sys.stderr is not None:
        logger.add(sys.stderr, level="INFO")
    if http:
        logger.add(_ROOT / "logs/server.log", rotation="10 MB", retention="7 days", level="DEBUG")


def get_coreldraw_status() -> ToolResult:
    """Check that CorelDRAW responds, without touching any document. Returns state "ready" and the version.

    While another tool runs: state "busy" and running (operation, seconds, read_only, time_limit_seconds,
    timed_out, interrupting).
    """
    status = get_connection().probe()
    if status["success"]:
        return ToolResult.ok(state="ready", version=status["version"])
    data = {"state": status["state"]}
    if status.get("running"):
        data["running"] = status["running"]
    return ToolResult(success=False, error=status["error"], data=data)


@mcp.custom_route("/healthz", methods=["GET"])
async def healthz(request):
    return JSONResponse({"service": "coreldraw-signage", "status": "alive"})


@mcp.custom_route("/readyz", methods=["GET"])
async def readyz(request):
    status = await run_in_threadpool(get_coreldraw_status)
    return JSONResponse(status.model_dump(), status_code=200 if status.success else 503)


def register_tools():
    from tools import (
        colors,
        data_merge,
        document,
        effects,
        engineering,
        export,
        find,
        geometry,
        inks,
        inspection,
        layers,
        plates,
        preflight,
        proof,
        recolor,
        shapes,
        silhouette,
        templates,
        text,
    )

    add(get_coreldraw_status, READ)
    add(document.list_documents, READ)
    add(document.open_document, ADDS_SAME)
    add(document.create_document, ADDS)
    add(document.save_document, SETS)
    add(document.close_document)
    add(document.edit_pages)
    add(document.add_guideline, ADDS)

    add(shapes.create_shape, ADDS)
    add(shapes.import_file, ADDS)
    add(geometry.transform_shapes)
    add(geometry.arrange_shapes)
    add(shapes.group_shapes)
    add(shapes.powerclip)
    add(shapes.delete_shapes)
    add(inspection.inspect_shapes, READ)
    add(inks.get_ink_inventory, READ)
    add(find.find_shapes, ADDS_SAME)
    add(inspection.get_ui_selection, READ)
    add(effects.apply_contour)
    add(effects.separate_effect)
    add(silhouette.offset_silhouette, ADDS)
    add(geometry.duplicate_shapes, ADDS)
    add(geometry.combine_shapes)
    add(geometry.convert_to_curves, SETS)
    add(proof.simulate_overprints, ADDS_SAME)
    add(proof.get_print_separation_settings, READ)
    add(proof.export_page_proof_pdf, ADDS)
    add(plates.render_plates, ADDS)

    add(text.inspect_text, READ)
    add(text.edit_text, SETS)

    add(colors.set_fill, SETS)
    add(colors.set_outline, SETS)
    add(colors.set_overprint, SETS)
    add(colors.set_transparency, SETS)
    add(recolor.replace_color, SETS)
    add(colors.undo)

    add(layers.list_layers, READ)
    add(layers.set_layer_properties, SETS)
    add(layers.create_layer, ADDS)
    add(layers.move_shapes_to_layer, SETS)

    add(export.export_pdf, SETS)
    add(export.export_dxf, SETS)
    add(export.export_ai, SETS)
    add(export.export_svg, SETS)
    add(export.export_png, SETS)
    add(export.export_jpeg, SETS)
    add(export.export_preview_png, SETS)
    add(export.batch_export, SETS)

    add(preflight.check_dimensions, READ)

    add(templates.list_templates, READ)
    add(templates.get_template_info, READ)
    add(templates.get_size_variant, READ)

    add(engineering.populate_title_block, SETS)
    add(engineering.assemble_engineering_drawing, SETS)

    add(data_merge.read_excel_data, READ)
    add(data_merge.merge_record, SETS)
    add(data_merge.batch_merge, SETS)
    add(data_merge.generate_barcode, ADDS)
    add(data_merge.generate_qrcode, ADDS)

def _require_ghostscript():
    """Exit 1 unless Ghostscript is found as render_plates finds it and `--version` runs.

    Reports on stderr only: stdout is the MCP channel under --stdio.
    """
    import subprocess

    from core.separations import find_ghostscript

    try:
        executable = find_ghostscript()
        version = subprocess.run([str(executable), "--version"], capture_output=True, text=True, timeout=20,
                                 check=True).stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"Ghostscript: {exc}", file=sys.stderr)
        sys.exit(1)
    print(f"Ghostscript: {version} at {executable}", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description="CorelDRAW MCP bridge")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--stdio", action="store_true", help="Read/write MCP on stdin/stdout")
    mode.add_argument("--local-http", action="store_true", help="Use the workstation endpoint at 127.0.0.1:8765")
    parser.add_argument("--check", action="store_true", help="Validate the interpreter and tool registration, then exit")
    parser.add_argument("--require-ghostscript", action="store_true",
                        help="Fail at startup unless Ghostscript (render_plates) is found and runs")
    args = parser.parse_args()
    load_dotenv(_ROOT / ".env")
    register_tools()
    if sys.platform != "win32" or struct.calcsize("P") != 8:
        raise RuntimeError("Run the CorelDRAW server with 64-bit Python on Windows")
    if args.require_ghostscript:
        _require_ghostscript()
    transport = os.environ.get("MCP_TRANSPORT", "stdio")
    if args.stdio:
        transport = "stdio"
    elif args.local_http:
        transport = "streamable-http"
    setup_logging(http=transport != "stdio")
    host = "127.0.0.1" if args.local_http else os.environ.get("MCP_HOST", "127.0.0.1")
    port = 8765 if args.local_http else int(os.environ.get("MCP_PORT", "8765"))
    try:
        if transport == "stdio":
            if not args.check:
                mcp.run(transport="stdio", show_banner=False)
        elif transport in {"http", "streamable-http"}:
            if host not in {"127.0.0.1", "::1", "localhost"}:
                raise ValueError("The CorelDRAW HTTP server requires a loopback host")
            if args.check:
                mcp.http_app(transport="streamable-http", **_HTTP_OPTIONS)
                return
            logger.info("CorelDRAW MCP listening on {}:{} (PID {})", host, port, os.getpid())
            # No MCP session state to invalidate on a process restart. Corel itself is still shared.
            mcp.run(transport="streamable-http", host=host, port=port, show_banner=False, **_HTTP_OPTIONS)
        else:
            raise ValueError("MCP_TRANSPORT must be stdio or streamable-http")
    finally:
        close_connection()
        logger.info("CorelDRAW MCP stopped")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        logger.exception("CorelDRAW MCP startup or runtime failure")
        raise
