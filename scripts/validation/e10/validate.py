r"""E10 validation through the running MCP server: render_plates on every page of the given documents.

For each page: render_plates (timed), get_ink_inventory's pdf_proof plates (E03 prediction) and an independent
render of the same proof.pdf with server/render_separation_proof.py. Prints agreement and every difference.

    python validate.py <out_dir> <doc.cdr> [...]

Writes <out_dir>\<doc>\p<N>\ (render_plates output), <out_dir>\<doc>\p<N>.independent\ and <out_dir>\validate.json.
"""
import json
import ntpath
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "server"))
from mc import call  # noqa: E402
from render_separation_proof import render  # noqa: E402

GS = Path(r"C:\Program Files\gs\gs10.08.0\bin\gswin64c.exe")


def main():
    out_root = Path(sys.argv[1])
    out_root.mkdir(parents=True, exist_ok=True)
    report = {}
    for document in sys.argv[2:]:
        name = ntpath.basename(document)
        opened = call("open_document", path=document, activate=False)
        assert opened.get("success"), opened
        inventory = call("get_ink_inventory", document_path=document, max_ids_per_ink=0)
        assert inventory.get("success"), inventory
        folder = out_root / Path(name).stem[:24].strip().replace(" ", "_")
        shutil.rmtree(folder, ignore_errors=True)
        folder.mkdir()
        pages = {}
        for page in inventory["data"]["pages"]:
            index = page["page_index"]
            r = call("render_plates", document_path=document, page_index=index, output_dir=str(folder / f"p{index}"))
            if not r.get("success"):
                print(f"{name} p{index}: FAILED {r.get('error')}")
                pages[index] = {"error": r.get("error")}
                continue
            data = r["data"]
            independent = render(folder / f"p{index}" / "proof.pdf", folder / f"p{index}.independent", GS, 150, 1)
            independent_plates = sorted(p["name"] for p in independent["plates"] if not p["blank"])
            inventory_plates = sorted(page["plates"]["pdf_proof"])
            rendered = sorted(data["rendered_plates"])
            row = {
                "seconds": r["_seconds"], "tool_seconds": data["seconds"], "bytes": len(json.dumps(r)),
                "predicted": sorted(data["predicted_plates"]), "inventory_pdf_proof": inventory_plates,
                "rendered": rendered, "independent_render": independent_plates,
                "registration_only": [p["plate"] for p in data["plates"] if p["registration_only"]],
                "differences": data["differences"], "warnings": [w["code"] for w in data["warnings"]],
            }
            ok = (row["predicted"] == inventory_plates == rendered == independent_plates)
            row["agree"] = ok and not [d for d in data["differences"] if d["type"] in
                                       ("expected_plate_blank", "unexpected_plate", "art_missing")]
            pages[index] = row
            print(f"{name} p{index}: {'OK' if row['agree'] else 'DIFF'} {r['_seconds']}s {row['bytes']}B "
                  f"rendered={rendered} reg_only={row['registration_only']} "
                  f"diffs={[(d['type'], d['plate']) for d in data['differences']]} warnings={row['warnings']}")
            if not ok:
                print("   predicted", row["predicted"], "inventory", inventory_plates, "independent", independent_plates)
        report[name] = pages
    (out_root / "validate.json").write_text(json.dumps(report, indent=1), encoding="utf-8")


main()
