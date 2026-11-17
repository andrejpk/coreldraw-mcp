"""Probe read-only legacy tools on the Kings work copy."""
import sys, json; sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))
from audit_lib import RUN, Session
D = RUN + r"\work\51019 - Kings High School 2021 Shirt Order - SCREENS.cdr"
with Session(RUN + r"\calls.jsonl") as s:
    s.task = "probe legacy"
    s.call("open_document", {"path": D})
    for t, a in [("get_layers", {}), ("select_shapes", {"by_type": "text"}), ("select_shapes", {"by_layer": "Film Information"}),
                 ("find_shape_by_name", {"name": ""}), ("check_missing_fonts", {}), ("check_text_overflow_all", {}),
                 ("check_dimensions", {"expected_width": 330.2, "expected_height": 457.2}),
                 ("export_preview_png", {"document_path": D, "path": RUN + r"\proofs\kings_preview.png"}),
                 ("get_ui_selection", {"document_path": D, "page_index": 1}), ("list_templates", {}),
                 ("list_all_text_shapes", {"page_index": 1, "document_path": D})]:
        r = s.call(t, a); print("##", t, a, r.get("_seconds"), json.dumps(r, ensure_ascii=False)[:500])
