"""
Generate a small two-storey house as IFC4 (what Revit's File > Export > IFC produces, in miniature):
storeys at 0.0 and 3.0 m, external and internal walls with door and window openings, doors,
windows, floor and roof slabs, corner columns, and named spaces (rooms). Units: millimetres.
Output: HOUSE_MODEL.ifc next to this script.
"""
from pathlib import Path

import numpy as np
import ifcopenshell
import ifcopenshell.api as api

OUT = Path(__file__).parent / "HOUSE_MODEL.ifc"
model = api.run("project.create_file", version="IFC4")
project = api.run("root.create_entity", model, ifc_class="IfcProject", name="Test House")
api.run("unit.assign_unit", model, length={"is_metric": True, "raw": "MILLIMETERS"})
context = api.run("context.add_context", model, context_type="Model")
body = api.run("context.add_context", model, context_type="Model", context_identifier="Body",
               target_view="MODEL_VIEW", parent=context)
site = api.run("root.create_entity", model, ifc_class="IfcSite", name="Site")
building = api.run("root.create_entity", model, ifc_class="IfcBuilding", name="House")
api.run("aggregate.assign_object", model, relating_object=project, products=[site])
api.run("aggregate.assign_object", model, relating_object=site, products=[building])


MM = 0.001   # the authoring API takes SI metres; the file itself is in millimetres


def placement(x=0.0, y=0.0, z=0.0, angle_deg=0.0):
    x, y, z = x * MM, y * MM, z * MM
    m = np.eye(4)
    a = np.radians(angle_deg)
    m[0][0], m[0][1], m[1][0], m[1][1] = np.cos(a), -np.sin(a), np.sin(a), np.cos(a)
    m[0][3], m[1][3], m[2][3] = x, y, z
    return m


def material(name, rgb):
    mat = api.run("material.add_material", model, name=name)
    style = api.run("style.add_style", model, name=name)
    api.run("style.add_surface_style", model, style=style, ifc_class="IfcSurfaceStyleShading",
            attributes={"SurfaceColour": {"Name": None, "Red": rgb[0], "Green": rgb[1], "Blue": rgb[2]}})
    return mat, style


BRICK = material("Brick", (0.72, 0.42, 0.30))
PLASTER = material("Plaster partition", (0.88, 0.80, 0.70))
CONCRETE = material("Concrete", (0.66, 0.66, 0.68))
TIMBER = material("Timber", (0.52, 0.34, 0.18))
GLASS = material("Glass", (0.55, 0.78, 0.92))


def styled(product, rep, mat):
    api.run("material.assign_material", model, products=[product], material=mat[0])
    api.run("style.assign_representation_styles", model, shape_representation=rep, styles=[mat[1]])


def wall(storey, x, y, angle, length, height=3000.0, thickness=230.0, mat=BRICK, name="Wall"):
    w = api.run("root.create_entity", model, ifc_class="IfcWall", name=name)
    rep = api.run("geometry.add_wall_representation", model, context=body, length=length * MM, height=height * MM,
                  thickness=thickness * MM)
    api.run("geometry.assign_representation", model, product=w, representation=rep)
    api.run("geometry.edit_object_placement", model, product=w, matrix=placement(x, y, storey["z"], angle))
    api.run("spatial.assign_container", model, relating_structure=storey["entity"], products=[w])
    styled(w, rep, mat)
    return w


def opening_in(w, storey, x, y, angle, width, height, sill, thickness, filler_class, mat, name):
    """Cut an opening in a wall and fill it with a door or window."""
    op = api.run("root.create_entity", model, ifc_class="IfcOpeningElement")
    rep = api.run("geometry.add_wall_representation", model, context=body, length=width * MM, height=height * MM,
                  thickness=(thickness + 100) * MM)
    api.run("geometry.assign_representation", model, product=op, representation=rep)
    a = np.radians(angle)
    ox, oy = x - np.sin(a) * -50, y + np.cos(a) * -50   # centre the cutter across the wall
    api.run("geometry.edit_object_placement", model, product=op, matrix=placement(ox, oy, storey["z"] + sill, angle))
    api.run("feature.add_feature", model, feature=op, element=w)
    filler = api.run("root.create_entity", model, ifc_class=filler_class, name=name)
    panel = 50.0 if filler_class == "IfcDoor" else 24.0           # door leaf / glazing thickness (mm)
    frep = api.run("geometry.add_wall_representation", model, context=body, length=width * MM, height=height * MM,
                   thickness=panel * MM)
    api.run("geometry.assign_representation", model, product=filler, representation=frep)
    shift = (thickness - panel) / 2                                 # centre the panel in the wall
    px, py = x - np.sin(a) * shift, y + np.cos(a) * shift
    api.run("geometry.edit_object_placement", model, product=filler, matrix=placement(px, py, storey["z"] + sill, angle))
    api.run("spatial.assign_container", model, relating_structure=storey["entity"], products=[filler])
    api.run("feature.add_filling", model, opening=op, element=filler)
    filler.OverallHeight, filler.OverallWidth = float(height), float(width)
    styled(filler, frep, mat)


def slab(storey, z, thickness=150.0, name="Slab"):
    s = api.run("root.create_entity", model, ifc_class="IfcSlab", name=name)
    rep = api.run("geometry.add_slab_representation", model, context=body, depth=thickness * MM,
                  polyline=[(0.0, 0.0), (10.0, 0.0), (10.0, 8.0), (0.0, 8.0)])
    api.run("geometry.assign_representation", model, product=s, representation=rep)
    api.run("geometry.edit_object_placement", model, product=s, matrix=placement(0, 0, z))
    api.run("spatial.assign_container", model, relating_structure=storey["entity"], products=[s])
    styled(s, rep, CONCRETE)


def column(storey, x, y, height=3000.0):
    c = api.run("root.create_entity", model, ifc_class="IfcColumn", name="Column")
    profile = model.create_entity("IfcRectangleProfileDef", ProfileType="AREA", XDim=300.0, YDim=300.0)  # file units (mm)
    rep = api.run("geometry.add_profile_representation", model, context=body, profile=profile, depth=height * MM)
    api.run("geometry.assign_representation", model, product=c, representation=rep)
    api.run("geometry.edit_object_placement", model, product=c, matrix=placement(x, y, storey["z"]))
    api.run("spatial.assign_container", model, relating_structure=storey["entity"], products=[c])
    styled(c, rep, CONCRETE)


def space(storey, name, long_name, x0, y0, x1, y1):
    sp = api.run("root.create_entity", model, ifc_class="IfcSpace", name=name)
    sp.LongName = long_name
    rep = api.run("geometry.add_slab_representation", model, context=body, depth=2.8,
                  polyline=[(x0 * MM, y0 * MM), (x1 * MM, y0 * MM), (x1 * MM, y1 * MM), (x0 * MM, y1 * MM)])
    api.run("geometry.assign_representation", model, product=sp, representation=rep)
    api.run("geometry.edit_object_placement", model, product=sp, matrix=placement(0, 0, storey["z"]))
    api.run("aggregate.assign_object", model, relating_object=storey["entity"], products=[sp])


storeys = []
for i, (name, z) in enumerate([("Ground Floor", 0.0), ("First Floor", 3000.0)]):
    entity = api.run("root.create_entity", model, ifc_class="IfcBuildingStorey", name=name)
    entity.Elevation = z
    api.run("aggregate.assign_object", model, relating_object=building, products=[entity])
    storeys.append({"entity": entity, "z": z})

for i, st in enumerate(storeys):
    slab(st, st["z"], name="Floor slab")
    front = wall(st, 0, 0, 0, 10000, name="External wall")            # along x at y=0
    back = wall(st, 10000, 8000, 180, 10000, name="External wall")
    left = wall(st, 0, 8000 - 230, 270, 8000 - 460, name="External wall")
    right = wall(st, 10000, 230, 90, 8000 - 460, name="External wall")
    part = wall(st, 5000 + 57, 230, 90, 8000 - 460, thickness=115.0, mat=PLASTER, name="Partition")
    if i == 0:
        opening_in(front, st, 1500, 0, 0, 1000, 2100, 0, 230, "IfcDoor", TIMBER, "D Main door")
    else:
        opening_in(front, st, 1500, 0, 0, 1500, 1200, 900, 230, "IfcWindow", GLASS, "W1")
    opening_in(front, st, 7000, 0, 0, 1800, 1200, 900, 230, "IfcWindow", GLASS, "W2")
    opening_in(part, st, 5000 + 57, 3000, 90, 900, 2100, 0, 115, "IfcDoor", TIMBER, "D1")
    for x, y in [(0, 0), (9700, 0), (0, 7700), (9700, 7700)]:
        column(st, x, y)
    space(st, "1" if i == 0 else "4", "LIVING ROOM" if i == 0 else "BEDROOM 1", 230, 230, 5000, 7770)
    space(st, "2" if i == 0 else "5", "KITCHEN" if i == 0 else "BEDROOM 2", 5172, 230, 9770, 7770)
slab(storeys[1], 6000.0, name="Roof slab")

model.write(str(OUT))
print("saved", OUT, OUT.stat().st_size, "bytes", len(model.by_type("IfcProduct")), "products")
