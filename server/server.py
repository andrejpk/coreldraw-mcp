import argparse
import os
import struct
import sys
from pathlib import Path

from core.connection import close_connection, get_connection
from core.models import ToolResult
from dotenv import load_dotenv
from fastmcp import FastMCP
from fastmcp.tools import Tool
from loguru import logger
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse

_ROOT = Path(__file__).resolve().parent.parent
_HTTP_OPTIONS = {"path": "/mcp", "stateless_http": True, "json_response": True, "host_origin_protection": True}
mcp = FastMCP("coreldraw-signage")


def setup_logging(*, http):
    logger.remove()
    if sys.stderr is not None:
        logger.add(sys.stderr, level="INFO")
    if http:
        logger.add(_ROOT / "logs/server.log", rotation="10 MB", retention="7 days", level="DEBUG")


def get_coreldraw_status() -> ToolResult:
    """Check whether the running CorelDRAW instance responds to COM without opening or changing artwork.

    While another tool is still running, returns state "busy" and running: the tool (operation), seconds,
    read_only, time_limit_seconds, timed_out and interrupting (a read past its limit being cancelled).
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
        geometry,
        inspection,
        layers,
        preflight,
        proof,
        shapes,
        templates,
        text,
    )

    mcp.add_tool(Tool.from_function(get_coreldraw_status, annotations={"readOnlyHint": True}))
    mcp.add_tool(Tool.from_function(document.list_documents, annotations={"readOnlyHint": True}))
    mcp.add_tool(document.open_document)
    mcp.add_tool(document.create_document)
    mcp.add_tool(document.save_document)
    mcp.add_tool(document.close_document)
    mcp.add_tool(document.edit_pages)
    mcp.add_tool(document.list_all_text_shapes)
    mcp.add_tool(document.add_guideline)

    mcp.add_tool(shapes.create_rectangle)
    mcp.add_tool(shapes.create_ellipse)
    mcp.add_tool(shapes.create_line)
    mcp.add_tool(shapes.import_svg)
    mcp.add_tool(shapes.import_image)
    mcp.add_tool(shapes.set_shape_size)
    mcp.add_tool(shapes.set_shape_position)
    mcp.add_tool(shapes.boolean_operation)
    mcp.add_tool(shapes.convert_to_curves)
    mcp.add_tool(shapes.group_shapes)
    mcp.add_tool(shapes.find_shape_by_name)
    mcp.add_tool(shapes.align_shapes)
    mcp.add_tool(shapes.distribute_shapes)
    mcp.add_tool(shapes.set_z_order)
    mcp.add_tool(shapes.delete_shape)
    mcp.add_tool(shapes.rotate_shape)
    mcp.add_tool(shapes.ungroup_shapes)
    mcp.add_tool(shapes.scale_shape)
    mcp.add_tool(shapes.select_shapes)
    mcp.add_tool(shapes.powerclip)
    mcp.add_tool(inspection.inspect_shapes)
    mcp.add_tool(inspection.get_ui_selection)
    mcp.add_tool(inspection.select_shapes_by_id)
    mcp.add_tool(effects.inspect_contours)
    mcp.add_tool(effects.apply_contour)
    mcp.add_tool(effects.break_contour_apart)
    mcp.add_tool(effects.get_shape_overprint)
    mcp.add_tool(effects.set_shape_overprint)
    mcp.add_tool(geometry.set_shape_position_exact)
    mcp.add_tool(geometry.duplicate_shape_by_id)
    mcp.add_tool(geometry.duplicate_shapes_by_id)
    mcp.add_tool(geometry.weld_copies_by_id)
    mcp.add_tool(geometry.weld_consecutive_shapes)
    mcp.add_tool(geometry.delete_shape_by_id)
    mcp.add_tool(proof.get_simulate_overprints)
    mcp.add_tool(proof.set_simulate_overprints)
    mcp.add_tool(proof.get_print_separation_settings)
    mcp.add_tool(proof.export_page_proof_pdf)
    mcp.add_tool(proof.export_layer_proof_pdf)

    mcp.add_tool(text.set_text_content)
    mcp.add_tool(text.set_text_content_by_id)
    mcp.add_tool(text.inspect_text_fill_runs)
    mcp.add_tool(text.copy_fill_to_text_range)
    mcp.add_tool(text.set_text_style)
    mcp.add_tool(text.fit_text_to_frame)
    mcp.add_tool(text.check_text_overflow)
    mcp.add_tool(text.convert_text_to_curves)
    mcp.add_tool(text.create_text_frame)

    mcp.add_tool(colors.set_fill_cmyk)
    mcp.add_tool(colors.set_fill_rgb)
    mcp.add_tool(colors.set_fill_pantone)
    mcp.add_tool(colors.copy_shape_fill)
    mcp.add_tool(colors.set_outline)
    mcp.add_tool(colors.set_no_fill)
    mcp.add_tool(colors.set_no_outline)
    mcp.add_tool(colors.check_rgb_colors)
    mcp.add_tool(colors.set_fountain_fill)
    mcp.add_tool(colors.set_transparency)

    mcp.add_tool(layers.create_layer)
    mcp.add_tool(layers.get_layers)
    mcp.add_tool(layers.assign_to_layer)
    mcp.add_tool(layers.set_layer_visible)
    mcp.add_tool(layers.lock_layer)
    mcp.add_tool(layers.get_page_layers)
    mcp.add_tool(layers.set_page_layer_state)

    mcp.add_tool(export.export_pdf)
    mcp.add_tool(export.export_dxf)
    mcp.add_tool(export.export_ai)
    mcp.add_tool(export.export_svg)
    mcp.add_tool(export.export_png)
    mcp.add_tool(export.export_jpeg)
    mcp.add_tool(export.export_preview_png)
    mcp.add_tool(export.batch_export)

    mcp.add_tool(preflight.check_dimensions)
    mcp.add_tool(preflight.check_text_overflow_all)
    mcp.add_tool(preflight.check_missing_fonts)
    mcp.add_tool(preflight.get_color_report)

    mcp.add_tool(templates.list_templates)
    mcp.add_tool(templates.get_template_info)
    mcp.add_tool(templates.get_size_variant)

    mcp.add_tool(engineering.populate_title_block)
    mcp.add_tool(engineering.assemble_engineering_drawing)

    mcp.add_tool(data_merge.read_excel_data)
    mcp.add_tool(data_merge.merge_record)
    mcp.add_tool(data_merge.batch_merge)
    mcp.add_tool(data_merge.generate_barcode)
    mcp.add_tool(data_merge.generate_qrcode)


def main():
    parser = argparse.ArgumentParser(description="CorelDRAW MCP bridge")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--stdio", action="store_true", help="Read/write MCP on stdin/stdout")
    mode.add_argument("--local-http", action="store_true", help="Use the workstation endpoint at 127.0.0.1:8765")
    parser.add_argument("--check", action="store_true", help="Validate the interpreter and tool registration, then exit")
    args = parser.parse_args()
    load_dotenv(_ROOT / ".env")
    register_tools()
    if sys.platform != "win32" or struct.calcsize("P") != 8:
        raise RuntimeError("Run the CorelDRAW server with 64-bit Python on Windows")
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
