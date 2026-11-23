
"""Stage 5.0: disk-backed JSON writer.

Writes nested JSON structures incrementally.
Generator values are serialized one item at a time, allowing large
geometry arrays to be written without building another large JSON string.
"""

from __future__ import annotations

from pathlib import Path
from collections.abc import Iterator
from decimal import Decimal
from typing import Any
import json
import math
import os


def _write_decimal(stream, value: Decimal) -> None:
    """Write a finite Decimal as a JSON number without converting to float."""

    if not value.is_finite():
        raise ValueError("Non-finite Decimal values are not valid JSON numbers.")

    stream.write(str(value))


def _write_value(
    stream,
    value: Any,
    encoder: json.JSONEncoder,
) -> None:
    """Write a JSON value recursively without materializing generators."""

    if isinstance(value, Decimal):
        _write_decimal(stream, value)
        return

    if isinstance(value, dict):
        stream.write("{")
        first = True

        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("JSON object keys must be strings.")

            if not first:
                stream.write(",")

            stream.write(encoder.encode(key))
            stream.write(":")
            _write_value(stream, item, encoder)
            first = False

        stream.write("}")
        return

    if isinstance(value, (list, tuple)):
        stream.write("[")
        for index, item in enumerate(value):
            if index:
                stream.write(",")
            _write_value(stream, item, encoder)
        stream.write("]")
        return

    if isinstance(value, Iterator):
        stream.write("[")
        first = True

        for item in value:
            if not first:
                stream.write(",")

            _write_value(stream, item, encoder)
            first = False

        stream.write("]")
        return

    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("Non-finite float values are not valid JSON numbers.")

    for chunk in encoder.iterencode(value):
        stream.write(chunk)


def write_json_atomic(
    target_path: str | Path,
    data: Any,
    *,
    ensure_ascii: bool = False,
) -> dict:
    """Stream JSON to a temporary file and publish it atomically.

    The original target is preserved if serialization fails.
    Generator values are consumed incrementally.
    """

    target = Path(target_path)
    target.parent.mkdir(parents=True, exist_ok=True)

    temporary = target.with_name(target.name + ".streaming.tmp")

    encoder = json.JSONEncoder(
        ensure_ascii=ensure_ascii,
        separators=(",", ":"),
        allow_nan=False,
    )

    try:
        with temporary.open(
            "w",
            encoding="utf-8",
            newline="\n",
        ) as stream:
            _write_value(stream, data, encoder)

            stream.flush()
            os.fsync(stream.fileno())

        os.replace(temporary, target)

    except Exception:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise

    return {
        "output_file": str(target),
        "size_bytes": target.stat().st_size,
        "write_complete": True,
    }
