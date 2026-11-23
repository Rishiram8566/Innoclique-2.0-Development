
"""Stage 5.0: two-pass streaming reader for project geometry reports."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterator


def _require_ijson():
    try:
        import ijson
    except ImportError as exc:
        raise RuntimeError(
            "Project report streaming requires ijson. "
            "Install it with: pip install ijson"
        ) from exc
    return ijson


def _read_value(events, first_event):
    """Build one JSON value from an event stream."""
    _, event, value = first_event

    if event not in ("start_map", "start_array"):
        return value

    from ijson.common import ObjectBuilder

    builder = ObjectBuilder()
    builder.event(event, value)
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
    """Skip a complete JSON value without materializing it."""
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


def _read_pages_metadata(events, first_event):
    """Read page metadata while skipping primitive geometry arrays."""
    _, event, _ = first_event

    if event != "start_array":
        raise ValueError("Drawing pages must be a JSON array.")

    pages = []

    while True:
        try:
            prefix, event, value = next(events)
        except StopIteration:
            raise ValueError(
                "Unexpected end of JSON inside drawing pages."
            )

        if event == "end_array":
            break

        if event != "start_map":
            raise ValueError("Each drawing page must be a JSON object.")

        page = {}

        while True:
            try:
                prefix, event, value = next(events)
            except StopIteration:
                raise ValueError(
                    "Unexpected end of JSON inside a page."
                )

            if event == "end_map":
                break

            if event != "map_key":
                continue

            key = value
            first = next(events)

            if key == "primitives":
                _skip_value(events, first)
            else:
                page[key] = _read_value(events, first)

        pages.append(page)

    return pages


def _normalize_source_hash(drawing: dict[str, Any]) -> None:
    """Normalize legacy and nested source hashes for pipeline compatibility."""
    top_level_hash = drawing.get("source_sha256")
    source_metadata = drawing.get("source")

    nested_hash = (
        source_metadata.get("sha256")
        if isinstance(source_metadata, dict)
        else None
    )

    # Preserve an existing top-level hash.
    # Use the nested hash when the top-level field is absent or empty.
    if not top_level_hash and nested_hash:
        drawing["source_sha256"] = nested_hash


def _read_report_metadata(path: Path) -> list[dict[str, Any]]:
    """First pass: collect drawing/page metadata, skipping primitives."""
    ijson = _require_ijson()
    drawings = []

    with path.open("rb") as stream:
        events = iter(ijson.parse(stream))

        for prefix, event, value in events:
            if prefix != "drawings.item" or event != "start_map":
                continue

            drawing = {}

            while True:
                try:
                    prefix, event, value = next(events)
                except StopIteration:
                    raise ValueError(
                        "Unexpected end of JSON inside a drawing."
                    )

                if event == "end_map" and prefix == "drawings.item":
                    break

                if event != "map_key" or prefix != "drawings.item":
                    continue

                key = value
                first = next(events)

                if key == "pages":
                    drawing["pages"] = _read_pages_metadata(
                        events, first
                    )
                else:
                    drawing[key] = _read_value(events, first)

            # Support both source hash formats.
            _normalize_source_hash(drawing)

            drawings.append(drawing)

    return drawings


def iter_project_drawing_metadata(
    path: str | Path,
) -> Iterator[dict[str, Any]]:
    """Yield lightweight drawing metadata without geometry arrays."""
    source = Path(path)

    if not source.is_file():
        return

    for drawing in _read_report_metadata(source):
        item = {
            key: value
            for key, value in drawing.items()
            if key != "pages"
        }
        yield item


def get_project_drawing_metadata(
    path: str | Path,
) -> dict[str, dict[str, Any]]:
    """Build a lightweight index keyed by drawing ID."""
    result = {}

    for item in iter_project_drawing_metadata(path):
        drawing_id = str(item.get("drawing_id") or "")

        if drawing_id:
            result[drawing_id] = item

    return result


def _iter_all_report_primitives(
    path: Path,
) -> Iterator[tuple[int, int, Any]]:
    """Second pass: stream (drawing index, page index, primitive)."""
    ijson = _require_ijson()

    drawing_index = -1
    page_index = -1

    with path.open("rb") as stream:
        events = iter(ijson.parse(stream))

        for prefix, event, value in events:

            if prefix == "drawings.item" and event == "start_map":
                drawing_index += 1
                page_index = -1
                continue

            if not prefix.startswith("drawings.item.pages.item"):
                continue

            if (
                prefix == "drawings.item.pages.item"
                and event == "start_map"
            ):
                page_index += 1
                continue

            if (
                prefix == "drawings.item.pages.item.primitives.item"
                and event in ("start_map", "start_array")
            ):
                primitive = _read_value(
                    events, (prefix, event, value)
                )
                yield drawing_index, page_index, primitive

            elif (
                prefix == "drawings.item.pages.item.primitives.item"
                and event not in ("end_map", "end_array")
            ):
                yield drawing_index, page_index, value


def iter_project_drawings_streamed(
    path: str | Path,
) -> Iterator[dict[str, Any]]:
    """Yield complete drawing records with lazily streamed primitives.

    The report is read twice sequentially. Drawing and page metadata are
    retained; primitive arrays are streamed one object at a time.

    Consumers must fully consume each page's primitives before advancing
    to the next page, as the geometry stream is shared.
    """
    source = Path(path)

    if not source.is_file():
        return

    metadata_drawings = _read_report_metadata(source)

    primitive_stream = iter(_iter_all_report_primitives(source))
    pending = next(primitive_stream, None)

    for drawing_index, drawing_metadata in enumerate(metadata_drawings):
        drawing = dict(drawing_metadata)
        page_metadata = drawing.pop("pages", [])

        streamed_pages = []

        for page_index, page in enumerate(page_metadata):

            def page_primitives(
                current_drawing=drawing_index,
                current_page=page_index,
            ):
                nonlocal pending

                while pending is not None:
                    pending_drawing, pending_page, primitive = pending

                    if (
                        pending_drawing != current_drawing
                        or pending_page != current_page
                    ):
                        break

                    pending = next(primitive_stream, None)
                    yield primitive

            streamed_page = dict(page)
            streamed_page["primitives"] = page_primitives()
            streamed_pages.append(streamed_page)

        drawing["pages"] = iter(streamed_pages)
        yield drawing
