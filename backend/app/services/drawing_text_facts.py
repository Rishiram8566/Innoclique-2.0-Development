"""
Facts read from a drawing's own text: room names and sizes, door and window
tags, door/window schedule rows and sill/head notes.

Only printed text is used; nothing here guesses a value. A caller that finds
no schedule or note for an opening must say its height is assumed.
"""

from __future__ import annotations

import re
from typing import Any

# Words that name a space on architectural plans (general, not project-specific).
ROOM_WORDS = (
    r"ROOM|BED ?ROOM|BED|LIVING|DINING|KITCHEN|TOILET|BATH|W\.?C|WASH|STORE|DRESS|HALL|LOBBY|OFFICE|"
    r"CORRIDOR|PASSAGE|BALCONY|PANTRY|UTILITY|STAIR ?(?:CASE)?|LIFT|ELEVATOR|RECEPTION|MEETING|PRAYER|"
    r"ACCOMMODATION|SIT ?OUT|PORCH|FOYER|GARAGE|STUDY|GUEST|DRAWING|POOJA|LAUNDRY|"
    r"SERVANT|TERRACE|VERANDAH|CLOSET|CABIN|SHAFT|ENTRANCE|"
    # Common abbreviations and regional names on residential plans.
    r"KIT|KITCH|KITCHENETTE|LVG|LIV|LOUNGE|FAMILY|MAJLIS|DEN|SITTING|BR ?-?\d+|M\.?BR|BDR|BDRM|MBR|M\.?B\.?R|NURSERY|KIDS|"
    r"CHILDREN|MAID|T ?& ?B|T/B|TLT|TOI|LAV|LAVATORY|RESTROOM|EN-?SUITE|SHOWER|POWDER|D/R|D\.R|DNG|DIN|"
    r"WIC|W\.I\.C|WALK-?IN|UTL|LIBRARY|CONFERENCE|WAITING"
)
ROOM_LABEL = re.compile(rf"^(?=.*\b(?:{ROOM_WORDS})\b)[A-Z][A-Z0-9 /&\-.()]{{1,40}}$")
NOT_A_ROOM = re.compile(r"\b(?:SCALE|NOTES?|LEGEND|DRAWING NO|DRG|REV|PLAN|SECTION|ELEVATION|WALL|COLUMN|THK|SSL|FFL|LVL|LEVEL)\b")
SIZE_TEXT = re.compile(r"^\s*(\d{3,5})\s*[X×x]\s*(\d{3,5})\s*(?:MM)?\s*$", re.IGNORECASE)
OPENING_TAG = re.compile(r"^(D|W|V)(\d{0,2}[A-Z]?)$")
SCHEDULE_ROW = re.compile(
    r"^\s*(?P<tag>(?:D|W|V)\d{0,2}[A-Z]?)\s*[-:–=]?\s*(?P<width>\d{3,4})\s*[X×x]\s*(?P<height>\d{3,4})"
    r"(?:.*?SILL\s*(?:HT\.?|HEIGHT|LEVEL|LVL)?\s*[:=@]?\s*(?P<sill>\d{3,4}))?",
    re.IGNORECASE,
)
SILL_NOTE = re.compile(r"\bSILL\s*(?:HT\.?|HEIGHT|LEVEL|LVL)?\s*[:=@]?\s*(\d{3,4})\s*(?:MM)?\b", re.IGNORECASE)
HEAD_NOTE = re.compile(r"\b(?:DOOR|LINTEL|HEAD)\s*(?:HT\.?|HEIGHT|LEVEL|LVL)\s*[:=@]?\s*(\d{4})\s*(?:MM)?\b", re.IGNORECASE)


def room_label(text: str) -> str | None:
    clean = " ".join((text or "").split()).upper()
    if not clean or SIZE_TEXT.match(clean) or NOT_A_ROOM.search(clean) or not ROOM_LABEL.match(clean):
        return None
    return clean


def size_text(text: str) -> tuple[float, float] | None:
    match = SIZE_TEXT.match(text or "")
    return (float(match.group(1)), float(match.group(2))) if match else None


def opening_tag(text: str) -> str | None:
    clean = (text or "").strip().upper()
    return clean if OPENING_TAG.match(clean) else None


def schedule_row(text: str) -> dict[str, Any] | None:
    match = SCHEDULE_ROW.match((text or "").upper())
    if not match:
        return None
    return {
        "tag": match.group("tag").upper(),
        "width_mm": float(match.group("width")),
        "height_mm": float(match.group("height")),
        "sill_mm": float(match.group("sill")) if match.group("sill") else None,
        "text": text.strip(),
    }


def sill_note(text: str) -> float | None:
    if schedule_row(text):
        return None
    match = SILL_NOTE.search(text or "")
    return float(match.group(1)) if match else None


def head_note(text: str) -> float | None:
    match = HEAD_NOTE.search(text or "")
    return float(match.group(1)) if match else None


def collect(entry: dict[str, Any], text: str, bbox: list[float] | None) -> None:
    """Add this text span's facts to a sheet-index entry."""
    has_box = isinstance(bbox, (list, tuple)) and len(bbox) == 4
    box = [float(v) for v in bbox] if has_box else None
    name = room_label(text)
    if name and box:
        entry.setdefault("rooms", []).append((name, box))
    size = size_text(text)
    if size and box:
        entry.setdefault("sizes", []).append((size, box))
    tag = opening_tag(text)
    if tag and box:
        entry.setdefault("opening_tags", []).append((tag, box))
    row = schedule_row(text)
    if row:
        entry.setdefault("opening_schedule", {}).setdefault(row["tag"], row)
    sill = sill_note(text)
    if sill:
        entry.setdefault("sill_notes", []).append(sill)
    head = head_note(text)
    if head:
        entry.setdefault("head_notes", []).append(head)
    from app.services.envelope_details import is_envelope_text

    if is_envelope_text(text):
        entry.setdefault("envelope_texts", []).append(" ".join(text.split()))
    arrow = " ".join((text or "").split()).upper()
    if arrow in ("UP", "DN", "DOWN") and box:
        entry.setdefault("stair_arrows", []).append(("DN" if arrow == "DOWN" else arrow, box))
