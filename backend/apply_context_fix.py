from pathlib import Path
import re

path = Path("app/services/engineering_element_recognition.py")
source = path.read_text(encoding="utf-8")
updated = source

# 1. Allow callers to control how many nearby labels are retained.
old = "def _near_text_labels(text_spans, bbox, max_distance=48.0, page_width=None, page_height=None):"
new = "def _near_text_labels(text_spans, bbox, max_distance=48.0, page_width=None, page_height=None, max_labels=6):"

if updated.count(old) != 1:
    raise RuntimeError("Could not uniquely locate nearby-text function signature.")
updated = updated.replace(old, new, 1)

old = "for d, t, b in found[:6]]"
new = "for d, t, b in found[:max_labels]]"

if updated.count(old) != 1:
    raise RuntimeError("Could not uniquely locate nearby-label limit.")
updated = updated.replace(old, new, 1)

# 2. Replace the context classifier with distinct semantic categories.
start = updated.index("def _classify_pair_context(labels):")
end = updated.index("def _near_page_edge(", start)

replacement = '''def _classify_pair_context(labels):

    context = _semantic_context(labels)

    exclusions, hints = context["exclusion_signals"], context["hints"]

    if "tank_or_reservoir" in exclusions:
        return "tank_boundary_candidate", 0.36, "nearby_tank_or_reservoir_text"

    if "mechanical_equipment" in exclusions:
        return "equipment_outline_candidate", 0.36, "nearby_mechanical_equipment_text"

    if "stair_component" in exclusions:
        return "stair_boundary_candidate", 0.36, "nearby_stair_text"

    if "furniture_or_fixture" in exclusions:
        return "unclassified_parallel_line_pair_candidate", 0.28, "nearby_fixture_or_furniture_text"

    if "wall" in hints:
        return "wall_edge_pair_candidate", 0.52, "explicit_nearby_wall_text"

    return "unclassified_parallel_line_pair_candidate", 0.30, "no_explicit_wall_text"


'''

updated = updated[:start] + replacement + updated[end:]

# 3. Widen semantic context for parallel-line candidates.
old = '''labels = _near_text_labels(text_spans, bbox, max_distance=24,

                                           page_width=page_width, page_height=page_height)'''

new = '''labels = _near_text_labels(
                    text_spans,
                    bbox,
                    max_distance=96,
                    page_width=page_width,
                    page_height=page_height,
                    max_labels=12,
                )'''

if updated.count(old) != 1:
    raise RuntimeError("Could not uniquely locate parallel-line label lookup.")
updated = updated.replace(old, new, 1)

# 4. Remove endpoint-only promotion to wall_geometry_supported_candidate.
pattern = re.compile(
    r'^                if \(candidate_kind == "unclassified_parallel_line_pair_candidate".*?'
    r'(?=^                if gap <= 12 and candidate_kind == "wall_edge_pair_candidate":)',
    re.MULTILINE | re.DOTALL,
)

updated, count = pattern.subn(
    '''                # Endpoint connectivity is retained as topology evidence only.
                # It cannot independently promote an unknown boundary into a wall.
''',
    updated,
)

if count != 1:
    raise RuntimeError(f"Expected one endpoint-only promotion block; found {count}.")

if updated == source:
    raise RuntimeError("No source changes were produced.")

# Write only after every replacement has passed validation.
path.write_text(updated, encoding="utf-8", newline="\n")
print("Recognizer patch applied.")
print("Endpoint-only wall promotion removed.")
print("Tank and equipment classifications separated.")
print("Parallel-line semantic search expanded to 96 PDF points.")
