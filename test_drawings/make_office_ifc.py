"""
Generate Innoclique's showcase model: a modern six-storey office building as IFC4 (what Revit's
File > Export > IFC produces), original to this project. 7.2 m column grid (5 x 3 bays), double-height
ground floor, glass curtain walls with dark aluminium fins, white slab edges, a concrete core with a
lift overrun, an entrance canopy, open-plan desks, and roof-top plant: chillers, a cooling tower,
air handling units and a solar array behind a parapet. Units: millimetres.
Output: OFFICE_MODEL.ifc next to this script (imported by the app; the result is the landing page's
sample model, frontend/public/showcase/sample-building.json).
"""
from pathlib import Path

import numpy as np
import ifcopenshell.api as api

OUT = Path(__file__).parent / "OFFICE_MODEL.ifc"
model = api.run("project.create_file", version="IFC4")
project = api.run("root.create_entity", model, ifc_class="IfcProject", name="Innoclique sample office")
api.run("unit.assign_unit", model, length={"is_metric": True, "raw": "MILLIMETERS"})
context = api.run("context.add_context", model, context_type="Model")
body = api.run("context.add_context", model, context_type="Model", context_identifier="Body",
               target_view="MODEL_VIEW", parent=context)
site = api.run("root.create_entity", model, ifc_class="IfcSite", name="Site")
building = api.run("root.create_entity", model, ifc_class="IfcBuilding", name="Office building")
api.run("aggregate.assign_object", model, relating_object=project, products=[site])
api.run("aggregate.assign_object", model, relating_object=site, products=[building])

MM = 0.001   # the authoring API takes SI metres; the file itself is in millimetres


def placement(x=0.0, y=0.0, z=0.0, angle_deg=0.0):
    m = np.eye(4)
    a = np.radians(angle_deg)
    m[0][0], m[0][1], m[1][0], m[1][1] = np.cos(a), -np.sin(a), np.sin(a), np.cos(a)
    m[0][3], m[1][3], m[2][3] = x * MM, y * MM, z * MM
    return m


def material(name, rgb, transparency=0.0):
    mat = api.run("material.add_material", model, name=name)
    style = api.run("style.add_style", model, name=name)
    attributes = {"SurfaceColour": {"Name": None, "Red": rgb[0], "Green": rgb[1], "Blue": rgb[2]}}
    if transparency:
        attributes["Transparency"] = transparency
    api.run("style.add_surface_style", model, style=style, ifc_class="IfcSurfaceStyleShading", attributes=attributes)
    return mat, style


WHITE = material("White concrete", (0.90, 0.91, 0.92))
CONCRETE = material("Concrete", (0.62, 0.63, 0.65))
GLASS = material("Glass", (0.42, 0.63, 0.84), transparency=0.55)
FIN = material("Dark aluminium", (0.17, 0.19, 0.23))
TIMBER = material("Timber soffit", (0.62, 0.43, 0.27))
PLANT = material("Plant grey", (0.76, 0.78, 0.80))
PV = material("Solar panel", (0.10, 0.17, 0.36))
DESK = material("Desk white", (0.95, 0.95, 0.93))
SOFA = material("Fabric orange", (0.93, 0.48, 0.16))


def place(product, rep, storey, mat, x, y, z, angle=0.0):
    api.run("geometry.assign_representation", model, product=product, representation=rep)
    api.run("geometry.edit_object_placement", model, product=product, matrix=placement(x, y, z, angle))
    api.run("spatial.assign_container", model, relating_structure=storey, products=[product])
    api.run("material.assign_material", model, products=[product], material=mat[0])
    api.run("style.assign_representation_styles", model, shape_representation=rep, styles=[mat[1]])
    return product


def box(storey, ifc_class, name, x, y, z, lx, ly, h, mat, angle=0.0, predefined=None):
    """A box lx (local x) by ly (local y) by h, its corner at (x, y, z)."""
    product = api.run("root.create_entity", model, ifc_class=ifc_class, name=name,
                      **({"predefined_type": predefined} if predefined else {}))
    rep = api.run("geometry.add_wall_representation", model, context=body, length=lx * MM, height=h * MM,
                  thickness=ly * MM)
    return place(product, rep, storey, mat, x, y, z, angle)


def plate(storey, ifc_class, name, outline, z, depth, mat, predefined=None):
    product = api.run("root.create_entity", model, ifc_class=ifc_class, name=name,
                      **({"predefined_type": predefined} if predefined else {}))
    rep = api.run("geometry.add_slab_representation", model, context=body, depth=depth * MM,
                  polyline=[(px * MM, py * MM) for px, py in outline])
    return place(product, rep, storey, mat, 0, 0, z)


def column(storey, x, y, z, height, size=600.0):
    c = api.run("root.create_entity", model, ifc_class="IfcColumn", name="Column C600")
    profile = model.create_entity("IfcRectangleProfileDef", ProfileType="AREA", XDim=size, YDim=size)
    rep = api.run("geometry.add_profile_representation", model, context=body, profile=profile, depth=height * MM)
    return place(c, rep, storey, CONCRETE, x, y, z)


def space(storey, name, long_name, x0, y0, x1, y1, z, height):
    sp = api.run("root.create_entity", model, ifc_class="IfcSpace", name=name)
    sp.LongName = long_name
    rep = api.run("geometry.add_slab_representation", model, context=body, depth=height * MM * 0.9,
                  polyline=[(x0 * MM, y0 * MM), (x1 * MM, y0 * MM), (x1 * MM, y1 * MM), (x0 * MM, y1 * MM)])
    api.run("geometry.assign_representation", model, product=sp, representation=rep)
    api.run("geometry.edit_object_placement", model, product=sp, matrix=placement(0, 0, z))
    api.run("aggregate.assign_object", model, relating_object=storey, products=[sp])


# ---- Layout (mm) ----
BAY = 7200.0
NX, NY = 5, 3
GX, GY = NX * BAY, NY * BAY                       # 36.0 x 21.6 m between outer grid lines
FACADE = 450.0                                    # glass line outside the outer grid lines
EDGE = 350.0                                      # slab edge beyond the glass
SLAB = 300.0
LEVELS = [("Ground Floor", 0.0, 5000.0)] + [(f"Level {i}", 5000.0 + (i - 1) * 3800.0, 3800.0) for i in range(1, 6)]
ROOF = LEVELS[-1][1] + LEVELS[-1][2]
CORE = (28800.0 + 600.0, 7200.0 + 600.0, 36000.0 - 600.0, 14400.0 - 600.0)   # lift + stair core, east bay

x0, y0, x1, y1 = -FACADE, -FACADE, GX + FACADE, GY + FACADE                 # glass line
sx0, sy0, sx1, sy1 = x0 - EDGE, y0 - EDGE, x1 + EDGE, y1 + EDGE              # slab edge
slab_outline = [(sx0, sy0), (sx1, sy0), (sx1, sy1), (sx0, sy1)]

storeys = []
for name, z, height in LEVELS:
    entity = api.run("root.create_entity", model, ifc_class="IfcBuildingStorey", name=name)
    entity.Elevation = z
    api.run("aggregate.assign_object", model, relating_object=building, products=[entity])
    storeys.append((entity, z, height))
roof_storey = api.run("root.create_entity", model, ifc_class="IfcBuildingStorey", name="Roof")
roof_storey.Elevation = ROOF
api.run("aggregate.assign_object", model, relating_object=building, products=[roof_storey])


def facade_side(storey, z, height, ax, ay, length, angle, fin_out):
    """Glass from slab to slab along one side, with a dark fin every 1.8 m on its outer face."""
    clear = height - SLAB
    box(storey, "IfcPlate", "Curtain wall glazing", ax, ay, z, length, 30.0, clear, GLASS, angle, "CURTAIN_PANEL")
    n = max(1, round(length / 1800.0))
    a = np.radians(angle)
    ux, uy = np.cos(a), np.sin(a)                  # along the side
    nx, ny = -np.sin(a), np.cos(a)                 # local +y: into the building for every side below
    for i in range(n + 1):
        t = length * i / n - 30.0
        # each fin stands proud of the glass by fin_out, on the outside
        fx, fy = ax + ux * t - nx * fin_out, ay + uy * t - ny * fin_out
        box(storey, "IfcBuildingElementProxy", "Facade fin mullion", fx, fy, z, 60.0, fin_out, clear, FIN, angle)


for index, (storey, z, height) in enumerate(storeys):
    # floor slab with its white edge, and the columns of the 7.2 m grid
    plate(storey, "IfcSlab", "Floor slab", slab_outline, z - SLAB, SLAB, WHITE, "FLOOR")
    for i in range(NX + 1):
        for j in range(NY + 1):
            column(storey, i * BAY, j * BAY, z, height - SLAB)      # the profile is centred on its grid point
    # concrete core walls
    cx0, cy0, cx1, cy1 = CORE
    for (ax, ay, length, angle) in [(cx0, cy0, cx1 - cx0, 0), (cx1, cy1, cx1 - cx0, 180),
                                    (cx0, cy1, cy1 - cy0, 270), (cx1, cy0, cy1 - cy0, 90)]:
        box(storey, "IfcWall", "Core wall 300", ax, ay, z, length, 300.0, height - SLAB, CONCRETE, angle)
    # curtain walls on all four sides (glass inside the fins)
    fin = 320.0
    facade_side(storey, z, height, x0, y0, x1 - x0, 0, fin)          # south
    facade_side(storey, z, height, x1, y1, x1 - x0, 180, fin)        # north
    facade_side(storey, z, height, x0, y1, y1 - y0, 270, fin)        # west
    facade_side(storey, z, height, x1, y0, y1 - y0, 90, fin)         # east

    if index == 0:
        # entrance: glass doors in the south facade and a timber-soffit canopy above them
        for k in range(2):
            box(storey, "IfcDoor", "Entrance door (glass)", 14400 + k * 1800 + 150, y0 - 40, 0, 1500, 60, 2700, GLASS)
        plate(storey, "IfcSlab", "Entrance canopy", [(11400, y0 - 4200), (24600, y0 - 4200), (24600, y0), (11400, y0)],
              3900, 250, TIMBER, "FLOOR")
        box(storey, "IfcFurniture", "Reception desk", 15000, 4200, 0, 4800, 900, 1100, DESK)
        for k in range(3):
            box(storey, "IfcFurniture", "Lobby sofa", 6000 + k * 3200, 3000, 0, 2200, 900, 750, SOFA)
        space(storey, "G01", "LOBBY", 0, 0, 28800, GY, z, height)
    else:
        # open-plan office: rows of desks either side of the central corridor
        for row_y in (2200.0, 4600.0, 16000.0, 18400.0):
            for k in range(10):
                box(storey, "IfcFurniture", "Office desk", 1200 + k * 2650, row_y, z, 1600, 800, 740, DESK)
        for k in range(2):
            box(storey, "IfcFurniture", "Meeting table", 8000 + k * 9000, 9900, z, 3600, 1400, 740, DESK)
        space(storey, f"{index}01", "OPEN OFFICE", 0, 0, 28800, GY, z, height)

# roof: slab, parapet, lift / stair overrun and roof-top plant
plate(roof_storey, "IfcSlab", "Roof slab", slab_outline, ROOF - SLAB, SLAB, WHITE, "ROOF")
for (ax, ay, length, angle) in [(sx0, sy0, sx1 - sx0, 0), (sx1, sy1, sx1 - sx0, 180),
                                (sx0, sy1, sy1 - sy0, 270), (sx1, sy0, sy1 - sy0, 90)]:
    box(roof_storey, "IfcWall", "Parapet 1100", ax, ay, ROOF, length, 200.0, 1100, WHITE, angle)
cx0, cy0, cx1, cy1 = CORE
box(roof_storey, "IfcWall", "Lift overrun", cx0, cy0, ROOF, cx1 - cx0, cy1 - cy0, 3600, CONCRETE)
for k in range(2):
    box(roof_storey, "IfcChiller", "Air-cooled chiller", 2400 + k * 7200, 15000, ROOF, 6000, 2300, 2400, PLANT)
box(roof_storey, "IfcCoolingTower", "Cooling tower", 19800, 14600, ROOF, 4200, 4200, 4200, PLANT)
for k in range(2):
    box(roof_storey, "IfcUnitaryEquipment", "Air handling unit", 2400 + k * 7200, 9600, ROOF, 5400, 2200, 2000, PLANT)
for k in range(6):
    box(roof_storey, "IfcSolarDevice", "Solar panel row", 2000 + k * 4300, 1600, ROOF + 400, 3600, 5200, 120, PV)

model.write(str(OUT))
print("saved", OUT, OUT.stat().st_size, "bytes", len(model.by_type("IfcProduct")), "products")
