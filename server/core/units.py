"""CorelDRAW measurement units: names for the cdrUnit codes and explicit conversion.

Tools never change a document's unit to measure. They read ``doc.Unit`` and convert values explicitly.
"""

from contextlib import contextmanager

TENTH_MICRON = 0
INCH = 1
MILLIMETER = 3
POINT = 14

# cdrUnit codes from the CorelDRAW type library.
_NAMES = {
    0: "tenth_micron", 1: "in", 2: "ft", 3: "mm", 4: "cm", 5: "px", 6: "mi", 7: "m", 8: "km",
    9: "didot", 10: "agate", 11: "yd", 12: "pica", 13: "cicero", 14: "pt", 15: "q", 16: "h",
}
_ALIASES = {"inch": 1, "inches": 1, "millimeter": 3, "centimeter": 4, "point": 14, "points": 14}
_CODES = {name: code for code, name in _NAMES.items()} | _ALIASES
# Inches per unit, for the units whose size does not depend on a document resolution.
_INCHES = {
    0: 1 / 254000, 1: 1.0, 2: 12.0, 3: 1 / 25.4, 4: 1 / 2.54, 6: 63360.0, 7: 1 / 0.0254, 8: 1000 / 0.0254,
    11: 36.0, 12: 1 / 6, 14: 1 / 72, 15: 0.25 / 25.4, 16: 0.25 / 25.4,
}
#: Units accepted for measurements passed to tools.
MEASUREMENT_UNITS = ("in", "mm", "cm", "pt")


def unit_name(code) -> str:
    """Return the short name ("in", "mm", ...) of a cdrUnit code."""
    return _NAMES.get(int(code), f"unit_{int(code)}")


def unit_code(unit) -> int:
    """Return the cdrUnit code for a unit name such as "in", "inch", "mm", "cm" or "pt" (or a code)."""
    if isinstance(unit, int) and unit in _NAMES:
        return unit
    code = _CODES.get(str(unit).strip().lower())
    if code is None:
        raise ValueError(f"Unsupported unit {unit!r}; use one of {', '.join(MEASUREMENT_UNITS)}")
    return code


def convert(value: float, from_unit, to_unit, app=None) -> float:
    """Convert a length between units (names or codes). Pixel-like units need ``app`` (CorelDRAW ConvertUnits)."""
    source, target = unit_code(from_unit), unit_code(to_unit)
    if source == target:
        return value
    if source in _INCHES and target in _INCHES:
        return value * _INCHES[source] / _INCHES[target]
    if app is None:
        raise ValueError(f"Converting {unit_name(source)} to {unit_name(target)} needs CorelDRAW")
    return app.ConvertUnits(value, source, target)


@contextmanager
def temporary_unit(doc, unit):
    """Use another document unit inside the block and always restore the original one."""
    original = doc.Unit
    code = unit_code(unit)
    if original != code:
        doc.Unit = code
    try:
        yield
    finally:
        if doc.Unit != original:
            doc.Unit = original
