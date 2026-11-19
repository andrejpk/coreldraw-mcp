"""Minimal independent PDF check: inflate every stream and report spot inks and overprint states."""
import re
import sys
import zlib
from pathlib import Path


def scan(path):
    data = Path(path).read_bytes()
    chunks = [data]
    for match in re.finditer(rb"stream\r?\n", data):
        start = match.end()
        end = data.find(b"endstream", start)
        try:
            chunks.append(zlib.decompress(data[start:end]))
        except zlib.error:
            pass
    text = b"\n".join(chunks)
    names = sorted({re.sub(rb"#([0-9A-Fa-f]{2})", lambda m: bytes([int(m.group(1), 16)]), n).decode("latin-1")
                    for n in re.findall(rb"/Separation\s*/([^\s/\[\]<>()]+)", text)})
    return {
        "spots": names,
        "overprint_fill_true": len(re.findall(rb"/op\s+true", text)),
        "overprint_stroke_true": len(re.findall(rb"/OP\s+true", text)),
        "bytes": len(data),
    }


if __name__ == "__main__":
    for p in sys.argv[1:]:
        print(p, scan(p))
