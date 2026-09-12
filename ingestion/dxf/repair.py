"""Spec-guided repair for real-world flawed DXF files (bounded, honest).

Real drawings exported by scanners, other CAD libraries, or AI tools often
carry structural shortcuts that the strict ezdxf loader refuses — while the
measurable content (walls, polylines, text) is intact and loadable. The three
damage classes seen in real manual testing:

1. Entities missing the `100 AcDbEntity` / `100 <Type>` subclass markers the
   R13+ spec requires (observed: LWPOLYLINE, HATCH). The tag CONTENT is valid;
   only the subclass partition markers are absent. ezdxf's `SubclassProcessor`
   hard-indexes `subclasses[2]` and crashes before any geometry is seen.
2. A file truncated mid-tag before `EOF` — the final `0` group code dangles
   with no value. The measured content before the cut is complete.
3. Annotation entities (HATCH) whose content itself is incomplete (missing
   the required loop-count tag) — unparseable as drawn, and NOT measurable
   for takeoff in any case.

The repair is deliberately conservative and honest:
  * only entities with NO `100` marker of a known type are rewritten, and
    only with the two spec-required markers — no tag content is invented,
    moved between files, or guessed;
  * truncation closure appends only the missing structural tail (ENDSEC/EOF);
  * an entity type that still will not load AND is not measurable is dropped
    with an explicit warning (never silently, never fabricated);
  * every applied fix surfaces as a parse warning so the reviewer knows the
    bytes they uploaded were not spec-clean.

For damage classes beyond these, the caller falls back to ezdxf's own
`recover` module; if that fails too, parsing fails honestly.
"""
from __future__ import annotations

import re

# The two-marker subclass structure the R13+ spec requires, per entity type.
# Types NOT listed here load fine without markers (verified against the
# real-world corpus) or are repaired by ezdxf.recover instead.
_SUBCLASS_MARKERS: dict[str, str] = {
    "LWPOLYLINE": "AcDbPolyline",
    "HATCH": "AcDbHatch",
}

# Group codes belonging to the AcDbEntity (first) subclass per the DXF spec:
# handle, owner, markers, layer, linetype, color, visibility, and friends.
_ENTITY_SUBCLASS_CODES = frozenset({
    "5", "330", "100", "8", "6", "62", "67", "410", "420", "160", "310", "320",
    "60", "92",
})

# Types that are annotation only — not measurable geometry for takeoff.
# A file whose HATCH annotation is structurally broken still has measurable
# walls; dropping the broken annotation (with a warning) is the honest path.
_ANNOTATION_ONLY_TYPES = frozenset({
    "HATCH", "DIMENSION", "LEADER", "MLEADER", "TOLERANCE", "IMAGE", "WIPEOUT",
})

# ezdxf load errors name the entity type in either shape:
#   "DXFStructureError: HATCH: Missing required DXF tag ..." (type-labeled)
#   "missing 'AcDbPolyline' subclass in LWPOLYLINE(#422)"    (handle-labeled)
_ERROR_NAMED_TYPE = re.compile(r"^(?:[A-Za-z]+Error|Exception): ([A-Z][A-Z0-9_]+)\b")
_ERROR_HANDLE_TYPE = re.compile(r"\b([A-Z][A-Z0-9_]{3,})\(#")


def _pairs(lines: list[str]) -> list[tuple[str, str]]:
    """Group-code/value pairs; a dangling unpaired line is dropped (it is
    the truncation artifact this module closes honestly)."""
    out: list[tuple[str, str]] = []
    for i in range(0, len(lines) - 1, 2):
        out.append((lines[i], lines[i + 1]))
    return out


def _render(pairs: list[tuple[str, str]]) -> bytes:
    return ("\n".join(f"{c}\n{v}" for c, v in pairs) + "\n").encode("utf-8")


def inject_missing_subclass_markers(data: bytes) -> tuple[bytes, list[str]]:
    """Rewrite marker-less entities of known types into spec subclass form.

    Only entities whose tag stream contains NO `100` marker are touched —
    already-compliant entities pass through byte-for-byte. The rewrite
    partitions each entity's EXISTING tags: class codes stay in subclass 1
    (AcDbEntity), the rest go to subclass 2 (<Type>). Nothing is invented.

    One summary warning per damaged entity type (a drawing with 26 broken
    polylines reports the repair once, not 26 times).
    """
    try:
        text = data.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        # Non-UTF-8 content needs encoding detection before tag surgery;
        # leave it to ezdxf.recover (honest scope boundary).
        return data, []
    lines = text.splitlines()
    pairs = _pairs(lines)
    out: list[tuple[str, str]] = []
    counts: dict[str, int] = {}
    i = 0
    while i < len(pairs):
        code, val = pairs[i]
        if code == "0" and val in _SUBCLASS_MARKERS:
            # collect the entity's tags up to the next 0-code
            j = i + 1
            body: list[tuple[str, str]] = []
            while j < len(pairs) and pairs[j][0] != "0":
                body.append(pairs[j])
                j += 1
            if not any(c == "100" for c, _ in body):
                ent_sub = [p for p in body if p[0] in _ENTITY_SUBCLASS_CODES]
                type_sub = [p for p in body if p[0] not in _ENTITY_SUBCLASS_CODES]
                out.append(("0", val))
                out.append(("100", "AcDbEntity"))
                out.extend(ent_sub)
                out.append(("100", _SUBCLASS_MARKERS[val]))
                out.extend(type_sub)
                counts[val] = counts.get(val, 0) + 1
                i = j
                continue
        out.append((code, val))
        i += 1
    if not counts:
        return data, []
    warnings = [
        f"repaired {count} {etype} entit{'ies' if count > 1 else 'y'}: injected "
        f"missing AcDbEntity/{_SUBCLASS_MARKERS[etype]} subclass markers"
        for etype, count in sorted(counts.items())
    ]
    return _render(out), warnings


def close_truncation(data: bytes) -> tuple[bytes, list[str]]:
    """Append the missing structural tail to a truncated DRAWING.

    A file whose last complete tag pair is not `0/EOF` was cut mid-write.
    The honest closure appends only what the structure requires: ENDSEC if
    the file ends inside a section, then EOF. No drawing content is added.

    Guard: the file must contain an ENTITIES section at all AND at least one
    entity type ezdxf actually knows. A fragment whose only "entity" is
    garbage tags (or an empty ENTITIES section) is not a cut-off drawing —
    closing its tail would produce an empty document masquerading as a
    parsed drawing. Those refuse here and fall through to the honest
    DxfParseError.
    """
    try:
        text = data.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return data, []
    if "\nENTITIES\n" not in text and not text.endswith("\nENTITIES\n"):
        return data, []
    # At least one entity ezdxf can load must exist in the file, or there is
    # no drawing content worth closing. TABLE/LAYER etc. are section records
    # — only model entity types count (checked case-sensitively against
    # ezdxf's registry).
    from ezdxf.entities import factory as _factory

    lines_scan = text.splitlines()
    if not any(
        ln.strip() in _factory.ENTITY_CLASSES
        and ln.strip() not in {"TABLE", "LAYER", "BLOCK", "ENDBLK", "VIEW",
                               "VPORT", "UCS", "APPID", "DIMSTYLE", "STYLE"}
        for ln in lines_scan
    ):
        return data, []
    lines = list(text.splitlines())
    pairs = _pairs(lines)
    if not pairs:
        return data, []
    if pairs[-1] == ("0", "EOF"):
        return data, []
    dropped_tail = len(lines) - 2 * len(pairs)
    out = list(pairs)
    if out and out[-1] != ("0", "ENDSEC"):
        out.append(("0", "ENDSEC"))
    out.append(("0", "EOF"))
    note = "closed truncated tail with ENDSEC/EOF"
    if dropped_tail:
        note += f" (dropped {dropped_tail} dangling line{'s' if dropped_tail > 1 else ''})"
    return _render(out), [note]


def entity_type_from_error(message: str) -> str | None:
    """The entity type named in an ezdxf load error, if recognizable."""
    m = _ERROR_NAMED_TYPE.search(message) or _ERROR_HANDLE_TYPE.search(message)
    return m.group(1) if m else None


def drop_entity_type(data: bytes, dxftype: str) -> tuple[bytes, int]:
    """Remove every entity of one type (for unparseable annotation only).

    Returns the rewritten bytes and the dropped count. The caller decides
    whether the type is safe to drop (annotation-only); this never touches
    measurable geometry.
    """
    try:
        text = data.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return data, 0
    pairs = _pairs(text.splitlines())
    out: list[tuple[str, str]] = []
    dropped = 0
    i = 0
    while i < len(pairs):
        code, val = pairs[i]
        if code == "0" and val == dxftype:
            j = i + 1
            while j < len(pairs) and pairs[j][0] != "0":
                j += 1
            dropped += 1
            i = j
            continue
        out.append((code, val))
        i += 1
    if not dropped:
        return data, 0
    return _render(out), dropped


def is_annotation_only(dxftype: str) -> bool:
    """True when an entity type carries no measurable takeoff geometry."""
    return dxftype in _ANNOTATION_ONLY_TYPES


def annotation_type_order() -> tuple[str, ...]:
    """Stable probe order for the annotation-drop rung (most common first)."""
    return ("HATCH", "DIMENSION", "LEADER", "MLEADER", "TOLERANCE", "IMAGE", "WIPEOUT")


def file_has_entity(data: bytes, dxftype: str) -> bool:
    """Whether the tag stream contains at least one entity of the type."""
    try:
        text = data.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return False
    pairs = _pairs(text.splitlines())
    for code, val in pairs:
        if code == "0" and val == dxftype:
            return True
    return False
