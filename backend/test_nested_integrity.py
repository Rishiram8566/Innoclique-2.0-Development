
import json
from pathlib import Path

from ijson import items
from app.services.nested_geometry_reader import iter_drawing_pages


SOURCE = Path("data/validation_stage5/drawing_1002_complete_test.json")


def original_primitives(path):
    """Read original primitives one at a time."""
    with path.open("rb") as stream:
        for primitive in items(
            stream, "pages.item.primitives.item"
        ):
            yield primitive


def streamed_primitives(path):
    """Read primitives through our nested streaming reader."""
    for page in iter_drawing_pages(path):
        yield from page["primitives"]


print("Starting exact primitive integrity comparison...")

original = original_primitives(SOURCE)
streamed = streamed_primitives(SOURCE)

checked = 0
mismatches = 0
first_mismatch = None

while True:
    original_item = next(original, None)
    streamed_item = next(streamed, None)

    if original_item is None and streamed_item is None:
        break

    if original_item is None or streamed_item is None:
        mismatches += 1
        first_mismatch = ("Primitive count/order length mismatch", checked)
        break

    if original_item != streamed_item:
        mismatches += 1

        if first_mismatch is None:
            first_mismatch = (
                "Primitive content mismatch",
                checked + 1
            )

    checked += 1

print("\n========== INTEGRITY RESULTS ==========")
print("Primitives compared:", checked)
print("Mismatches:", mismatches)
print("First mismatch:", first_mismatch)
print("Exact match:", mismatches == 0)
print("=======================================")
