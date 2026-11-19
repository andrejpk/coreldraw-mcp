"""E13b dev 1 (MCP): offset_silhouette on the screen_job copy, then undo. Run from server/ with the server up."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mc import call, show  # noqa: E402

D = r"C:\CorelMcpLab\runs\e13b\work\screen_job.cdr"
ids = lambda: sorted(s["shape_id"] for s in call("inspect_shapes", document_path=D, page_index=1,  # noqa: E731
                                                  fields=["name"])["data"]["shapes"])
before = ids()
r = call("offset_silhouette", document_path=D, page_index=1, shape_ids=["4", "6", "8", "10"], offset=0.75,
         unit="pt", direction="inside", exclude_colors=["CMYK 0/0/0/100", "PANTONE Black C"],
         color="PANTONE Trans. White", target_layer="White Underbase", overprint_fill=True, name="ub_combined")
show(r, 3000)
after = ids()
print("new", sorted(set(after) - set(before)), "gone", sorted(set(before) - set(after)))
u = call("undo", document_path=D)
show(u, 600)
print("restored", ids() == before)
