
"""Memory-conscious nested reader for Stage 5.0 drawing JSON."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterator


def _require_ijson():
    try:
        import ijson
    except ImportError as exc:
        raise RuntimeError(
            "Nested geometry streaming requires ijson. "
            "Install it with: pip install ijson"
        ) from exc
    return ijson


def _read_value(events, first_event):
    """Build one JSON value from an ijson event stream."""
    from ijson.common import ObjectBuilder

    builder = ObjectBuilder()
    _, event, value = first_event
    builder.event(event, value)

    if event not in ("start_map", "start_array"):
        return builder.value

    depth = 1

    for _, event, value in events:
        builder.event(event, value)

        if event in ("start_map", "start_array"):
            depth += 1
        elif event in ("end_map", "end_array"):
            depth -= 1
            if depth == 0:
                break

    return builder.value


def _skip_value(events, first_event):
    """Skip one complete JSON value without building it."""
    _, event, _ = first_event

    if event not in ("start_map", "start_array"):
        return

    depth = 1

    for _, event, _ in events:
        if event in ("start_map", "start_array"):
            depth += 1
        elif event in ("end_map", "end_array"):
            depth -= 1
            if depth == 0:
                break


def _read_page_metadata(path: Path) -> list[dict[str, Any]]:
    """Read page metadata while skipping primitive geometry arrays."""
    ijson = _require_ijson()
    pages = []

    with path.open("rb") as stream:
        events = iter(ijson.parse(stream))

        for prefix, event, _ in events:
            if prefix != "pages.item" or event != "start_map":
                continue

            page = {}

            for prefix, event, value in events:
                if prefix == "pages.item" and event == "end_map":
                    break

                if prefix != "pages.item" or event != "map_key":
                    continue

                key = value
                first_event = next(events)

                if key == "primitives":
                    _skip_value(events, first_event)
                else:
                    page[key] = _read_value(events, first_event)

            pages.append(page)

    return pages


def _iter_all_primitives(path: Path) -> Iterator[dict]:
    """Stream every primitive from the drawing in source order."""
    ijson = _require_ijson()

    with path.open("rb") as stream:
        yield from ijson.items(
            stream, "pages.item.primitives.item"
        )


def _iter_page_primitives(
    path: Path,
    page_number: int,
) -> Iterator[dict]:
    """Compatibility helper for streaming one page's primitives."""
    for primitive in _iter_all_primitives(path):
        if primitive.get("page_number") == page_number:
            yield primitive


def iter_drawing_pages(path: str | Path) -> Iterator[dict]:
    """Yield pages with lazily streamed primitive arrays.

    A single primitive iterator is shared across pages, so the source
    geometry array is traversed only once when pages are consumed in order.
    """
    source = Path(path)

    if not source.is_file():
        raise FileNotFoundError(source)

    metadata_pages = _read_page_metadata(source)
    primitive_stream = iter(_iter_all_primitives(source))
    pending = next(primitive_stream, None)

    for page in metadata_pages:
        page_number = int(page["page_number"])

        def page_primitives(
            current_page=page_number,
        ) -> Iterator[dict]:
            nonlocal pending

            while pending is not None:
                primitive_page = pending.get("page_number")

                if primitive_page != current_page:
                    break

                current = pending
                pending = next(primitive_stream, None)
                yield current

        result = {}

        for key, value in page.items():
            if key == "text_spans":
                result["primitives"] = page_primitives()
            result[key] = value

        if "primitives" not in result:
            result["primitives"] = page_primitives()

        yield result


def iter_drawing_pages_with_metadata(
    path: str | Path,
) -> tuple[dict[str, Any], Iterator[dict]]:
    """Return top-level metadata and a lazy page iterator."""
    ijson = _require_ijson()
    source = Path(path)

    if not source.is_file():
        raise FileNotFoundError(source)

    metadata = {}

    with source.open("rb") as stream:
        events = iter(ijson.parse(stream))

        for prefix, event, value in events:
            if prefix != "" or event != "map_key":
                continue

            key = value
            first_event = next(events)

            if key == "pages":
                _skip_value(events, first_event)
            else:
                metadata[key] = _read_value(events, first_event)

    return metadata, iter_drawing_pages(source)
