"""E13b dev 2 (MCP): explore offset_silhouette / combine_shapes / effects on run-folder copies; prints summaries."""
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mc import call  # noqa: E402

W = Path(r"C:\CorelMcpLab\runs\e13b\dev")
W.mkdir(exist_ok=True)


def copy(name):
    target = W / name
    shutil.copyfile(Path(r"C:\CorelMcpLab\templates") / name, target)
    r = call("open_document", path=str(target))
    assert r["success"], r
    return str(target)


def short(r, keys=("result_id", "layer", "area", "subpaths", "holes", "participant_count", "skipped", "offset",
                   "fill", "overprint", "participants", "result_ids", "results", "refused", "shapes", "new_ids",
                   "verification_errors", "warnings")):
    out = {"ok": r.get("success"), "err": r.get("error"), "s": r.get("_seconds")}
    data = r.get("data") or {}
    out.update({k: data[k] for k in keys if k in data})
    return json.dumps(out, default=str)[:1500]


cases = sys.argv[1:] or ["colors", "text", "structure", "effects", "layers", "combine"]
if "colors" in cases:
    C = copy("colors.cdr")
    S = dict(document_path=C, page_index=1)
    print("colors excl", short(call("offset_silhouette", **S, shape_ids=["19", "22", "14", "4"], offset=1, unit="pt",
                                    direction="outside", exclude_colors=["CMYK 0/0/0/100"])))
    call("undo", document_path=C)
    print("colors all", short(call("offset_silhouette", **S, shape_ids=["19", "22", "14", "4"], offset=1, unit="pt",
                                   direction="outside")))
    call("undo", document_path=C)
    print("colors zero", short(call("offset_silhouette", **S, shape_ids=["4", "5"], offset=0, target_layer="Process",
                                    overprint_fill=True, keep_sources=False)))
    call("undo", document_path=C)
if "text" in cases:
    T = copy("text_runs.cdr")
    S = dict(document_path=T, page_index=1)
    print("text out", short(call("offset_silhouette", **S, shape_ids=["6", "7", "8"], offset=0.5, direction="outside",
                                 color="PANTONE Trans. White")))
    call("undo", document_path=T)
    print("text black", short(call("offset_silhouette", **S, shape_ids=["4", "8", "6"], offset=0.5,
                                   exclude_colors=["CMYK 0/0/0/100"], color="PANTONE Trans. White")))
    call("undo", document_path=T)
if "structure" in cases:
    St = copy("structure.cdr")
    S = dict(document_path=St, page_index=1)
    print("groups", short(call("offset_silhouette", **S, shape_ids=["6", "15"], offset=1, color="PANTONE Trans. White")))
    call("undo", document_path=St)
    print("powerclip", short(call("offset_silhouette", **S, shape_ids=["21"], offset=1, color="PANTONE Trans. White")))
    print("clip content", short(call("offset_silhouette", **S, shape_ids=["16"], offset=1)))
if "effects" in cases:
    E = copy("effects.cdr")
    S = dict(document_path=E, page_index=1)
    insp = call("inspect_shapes", **S, fields=["name", "type"])
    for s in insp["data"]["shapes"]:
        print(" ", s["shape_id"], s.get("name"), s["type"], s.get("parent_id"), s.get("effect_details"),
              s.get("effect_control_id") or s.get("effect"))
    print("control", short(call("offset_silhouette", **S, shape_ids=["4"], offset=1, color="PANTONE Trans. White")))
    call("undo", document_path=E)
if "layers" in cases:
    L = copy("layers.cdr")
    S = dict(document_path=L, page_index=1)
    print("locked src kept", short(call("offset_silhouette", **S, shape_ids=["17", "14"], offset=1,
                                        target_layer="Art")))
    call("undo", document_path=L)
    print("locked target", short(call("offset_silhouette", **S, shape_ids=["14"], offset=1, target_layer="Locked")))
    print("locked consume", short(call("offset_silhouette", **S, shape_ids=["17", "14"], offset=1,
                                       target_layer="Art", keep_sources=False)))
    print("mixed pages", short(call("offset_silhouette", **S, shape_ids=["14", "18"], offset=1)))
    print("master", short(call("offset_silhouette", **S, shape_ids=["22"], offset=1)))
if "combine" in cases:
    C = copy("colors.cdr")
    S = dict(document_path=C, page_index=1)
    for op, ids in (("weld", ["4", "5", "6"]), ("trim", ["4", "5"]), ("intersect", ["4", "5"]),
                    ("combine", ["4", "5"])):
        print(op, short(call("combine_shapes", **S, shape_ids=ids, operation=op, preserve_order=False)))
        call("undo", document_path=C)
    r = call("combine_shapes", **S, shape_ids=["4", "6"], operation="combine", preserve_order=False)
    rid = r["data"]["result_ids"][0]
    print("break", short(call("combine_shapes", **S, shape_ids=[rid], operation="break_apart")))
    call("undo", document_path=C)
    call("undo", document_path=C)
    print("weld keep layer", short(call("combine_shapes", **S, shape_ids=["4", "8"], operation="weld",
                                        preserve_order=False, keep_sources=True, target_layer="Outlines")))
    call("undo", document_path=C)
