"""E14 dev checks through MCP: edit_text and create_shape text on the dev text_runs copy."""
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mc import call  # noqa: E402

P = r"C:\CorelMcpLab\runs\e14\dev\text_runs.cdr"


def runs(r, i=0):
    if not r.get("success"):
        return r.get("error"), r.get("data", {}).get("refused") or r.get("data", {}).get("verification_errors")
    s = r["data"]["shapes"][i]
    return (s["text"], [(x["start"], x["end"], x["text"], x["fill"].get("ink"), x["fill"].get("tint"))
                        for x in s["fill_runs"]],
            [(x["start"], x["end"], x["font"], x["size"], x["bold"]) for x in s["font_runs"]], r["_seconds"])


E = dict(document_path=P, page_index=1)
print("1 minimal suffix", runs(call("edit_text", **E, shape_ids=["4"], content="RED  WHITE")))
print("2 cross runs refused", runs(call("edit_text", **E, shape_ids=["4"], content="BLUE WHITE")))
print("3 cross runs forced", runs(call("edit_text", **E, shape_ids=["4"], content="BLUE WHITE", keep_runs=False)))
call("undo", document_path=P)
print("4 range", runs(call("edit_text", **E, shape_ids=["4"], content="--", text_range=[3, 5])))
print("5 style range", runs(call("edit_text", **E, shape_ids=["4"], text_range=[0, 3], font="times new roman",
                                 size=20, bold=True)))
r = call("edit_text", **E, shape_ids=["6"], alignment="center")
print("6 align", r["success"], r.get("error"), r["data"]["shapes"][0].get("alignment") if r["success"] else r)
r = call("edit_text", **E, shape_ids=["7"], content="Many words " * 80)
print("7 grow", r["success"], r.get("error"), r["data"]["shapes"][0].get("frame") if r["success"] else r)
r = call("edit_text", **E, shape_ids=["7"], fit_to_frame=True)
print("8 fit", r["success"], r.get("error"), r["data"]["shapes"][0].get("frame") if r["success"] else r,
      r["data"]["shapes"][0]["font_runs"][0]["size"] if r["success"] else None)
print("9 nonbmp", call("edit_text", **E, shape_ids=["9"], content="A\U0001F600B").get("error"))
print("10 missing font", call("edit_text", **E, shape_ids=["9"], font="NoSuchFontXYZ").get("error"))
print("11 fit artistic", call("edit_text", **E, shape_ids=["9"], fit_to_frame=True).get("data"))
print("12 multiline", runs(call("edit_text", **E, shape_ids=["6"], content="Line one\nLine 2\nLine three")))
print("13 bmp", runs(call("edit_text", **E, shape_ids=["9"], content="Aé中Ω text")))
r = call("create_shape", **E, kind="artistic_text", x=1, y=1, unit="in", text="PANTONE 300 C 160",
         font="Arial", size=18, fill="300 C")
print("14 create artistic", r.get("success"), r.get("error"), {k: r["data"].get(k) for k in ("shape_id", "text",
      "fill", "font_runs", "bounds", "verification_errors")})
r = call("create_shape", **E, kind="paragraph_text", x=1, y=2, width=3, height=1, unit="in",
         text="Para one\nPara two", size=12, fill="CMYK 0/0/0/100")
print("15 create paragraph", r.get("success"), r.get("error"), {k: r["data"].get(k) for k in (
    "shape_id", "text", "fill", "bounds", "verification_errors")})
print("16 bad kind args", call("create_shape", **E, kind="artistic_text", x=1, y=1, width=2, unit="in",
                               text="x").get("error"))
print("17 range past end", call("edit_text", **E, shape_ids=["5"], content="x", text_range=[3, 9]).get("data"))
print("18 not text", call("edit_text", **E, shape_ids=["9", "x"], content="x").get("error"))
