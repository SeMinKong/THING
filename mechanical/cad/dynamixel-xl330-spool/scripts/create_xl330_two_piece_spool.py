"""Generate a compact two-piece XL330 tendon spool in Blender.

The mount base bolts to a ROBOTIS HNX330-N101 horn using the horn's official
four M2 holes on a 12 mm pitch circle.  A keyed octagonal hub transmits torque
to a small 12 mm winding drum.  One M2 screw and a captive M2 nut
clamp the drum to the base.

The design intentionally contains no decorative cord and no helical groove.
"""

from math import cos, pi, sin
from pathlib import Path

import bpy
from mathutils import Vector


CAD_DIR = Path(__file__).resolve().parents[1]
OUT = CAD_DIR / "generated"
STEM = "xl330_hnx330_n101_2mm_3turn_spool_v3_two_piece"
BLEND_PATH = OUT / f"{STEM}_assembly.blend"
DRUM_STL_PATH = OUT / f"{STEM}_drum.stl"
BASE_STL_PATH = OUT / f"{STEM}_mount_base.stl"
PREVIEW_PATH = OUT / f"{STEM}_preview.png"

# Official HNX330-N101 mounting pattern, millimetres.
MOUNT_PCD = 12.0
M2_CLEARANCE_DIAMETER = 2.30
M2_HEAD_RELIEF_DIAMETER = 4.10
M2_HEAD_RELIEF_DEPTH = 1.80
HORN_CENTRE_SCREW_RELIEF_DIAMETER = 6.0
HORN_CENTRE_SCREW_RELIEF_DEPTH = 0.50

# Mount-base geometry.
BASE_DIAMETER = 18.0
BASE_THICKNESS = 1.80
HUB_ACROSS_FLATS = 6.80
HUB_HEIGHT = 3.20
HUB_TOTAL_HEIGHT = BASE_THICKNESS + HUB_HEIGHT
CENTRE_M2_NUT_POCKET_ACROSS_FLATS = 4.25
CENTRE_M2_NUT_POCKET_DEPTH = 1.80
CENTRE_M2_TAIL_CLEARANCE_DIAMETER = 2.40

# Removable winding-drum geometry.
CORE_DIAMETER = 12.0
FLANGE_DIAMETER = 18.0
BOTTOM_FLANGE_THICKNESS = 2.40
WINDING_BAY_WIDTH = 6.50
TOP_FLANGE_THICKNESS = 1.60
DRUM_TOTAL_HEIGHT = (
    BOTTOM_FLANGE_THICKNESS + WINDING_BAY_WIDTH + TOP_FLANGE_THICKNESS
)
HUB_BORE_ACROSS_FLATS = 7.25
HUB_BORE_DEPTH = 3.60
CENTRE_M2_SCREW_CLEARANCE_DIAMETER = 2.30
CENTRE_M2_HEAD_RELIEF_DIAMETER = 4.30
CENTRE_M2_HEAD_RELIEF_DEPTH = 1.50

# One 2 mm cord, three side-by-side wraps.
CORD_DIAMETER = 2.0
CORD_WRAP_COUNT = 3
CORD_ANCHOR_DIAMETER = 2.60
CORD_ANCHOR_OFFSET = 3.40
CORD_ANCHOR_Z = BOTTOM_FLANGE_THICKNESS + WINDING_BAY_WIDTH / 2.0

EDGE_BEVEL = 0.15


def clear_scene() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    for datablocks in (
        bpy.data.meshes,
        bpy.data.curves,
        bpy.data.materials,
        bpy.data.cameras,
        bpy.data.lights,
    ):
        for block in list(datablocks):
            if block.users == 0:
                datablocks.remove(block)


def configure_scene_units() -> None:
    scene = bpy.context.scene
    scene.unit_settings.system = "METRIC"
    scene.unit_settings.length_unit = "MILLIMETERS"
    scene.unit_settings.scale_length = 0.001


def cylinder(
    name: str,
    diameter: float,
    depth: float,
    z_bottom: float,
    *,
    vertices: int = 128,
    x: float = 0.0,
    y: float = 0.0,
    rotation_z: float = 0.0,
) -> bpy.types.Object:
    bpy.ops.mesh.primitive_cylinder_add(
        vertices=vertices,
        radius=diameter / 2.0,
        depth=depth,
        location=(x, y, z_bottom + depth / 2.0),
        rotation=(0.0, 0.0, rotation_z),
    )
    obj = bpy.context.object
    obj.name = name
    return obj


def prism_from_across_flats(
    name: str,
    across_flats: float,
    sides: int,
    depth: float,
    z_bottom: float,
) -> bpy.types.Object:
    circumradius = across_flats / (2.0 * cos(pi / sides))
    # Rotate half a facet so a flat, rather than a vertex, faces each cardinal
    # mounting-screw direction.
    return cylinder(
        name,
        2.0 * circumradius,
        depth,
        z_bottom,
        vertices=sides,
        rotation_z=pi / sides,
    )


def boolean_apply(
    target: bpy.types.Object,
    tool: bpy.types.Object,
    operation: str,
) -> None:
    bpy.context.view_layer.objects.active = target
    modifier = target.modifiers.new(
        name=f"{operation.lower()}_{tool.name}",
        type="BOOLEAN",
    )
    modifier.operation = operation
    modifier.solver = "EXACT"
    modifier.object = tool
    bpy.ops.object.modifier_apply(modifier=modifier.name)
    bpy.data.objects.remove(tool, do_unlink=True)


def subtract(target: bpy.types.Object, cutter: bpy.types.Object) -> None:
    boolean_apply(target, cutter, "DIFFERENCE")


def unite(target: bpy.types.Object, addition: bpy.types.Object) -> None:
    boolean_apply(target, addition, "UNION")


def create_mount_base() -> bpy.types.Object:
    base = cylinder(
        "HNX330_Mount_Base_PRINT_THIS",
        BASE_DIAMETER,
        BASE_THICKNESS,
        0.0,
        vertices=192,
    )
    # Bake the cylinder's placement so the printable base has its lowest face
    # exactly at local Z=0 even after it is positioned in the preview scene.
    bpy.ops.object.select_all(action="DESELECT")
    base.select_set(True)
    bpy.context.view_layer.objects.active = base
    bpy.ops.object.transform_apply(location=True, rotation=False, scale=False)

    hub = prism_from_across_flats(
        "Octagonal_torque_hub",
        HUB_ACROSS_FLATS,
        8,
        HUB_HEIGHT + 0.25,
        BASE_THICKNESS - 0.25,
    )
    unite(base, hub)

    # Four standard PHS M2x4 screws attach this plate to the HNX330-N101.
    radius = MOUNT_PCD / 2.0
    for index in range(4):
        theta = index * pi / 2.0
        subtract(
            base,
            cylinder(
                f"M2_horn_mount_{index + 1}",
                M2_CLEARANCE_DIAMETER,
                BASE_THICKNESS + 0.40,
                -0.20,
                vertices=96,
                x=radius * cos(theta),
                y=radius * sin(theta),
            ),
        )

    # Captive standard M2 hex nut: nominal 4.0 mm across flats, 1.6 mm thick.
    nut_pocket_bottom = HUB_TOTAL_HEIGHT - CENTRE_M2_NUT_POCKET_DEPTH
    subtract(
        base,
        prism_from_across_flats(
            "M2_nut_pocket_AF4p25",
            CENTRE_M2_NUT_POCKET_ACROSS_FLATS,
            6,
            CENTRE_M2_NUT_POCKET_DEPTH + 0.20,
            nut_pocket_bottom,
        ),
    )

    # Clearance for the small amount by which the central M2x8 exits the nut.
    subtract(
        base,
        cylinder(
            "Centre_M2_screw_tail_clearance",
            CENTRE_M2_TAIL_CLEARANCE_DIAMETER,
            0.95,
            2.45,
            vertices=96,
        ),
    )

    # The standard XL330 horn centre screw is normally recessed; this shallow
    # relief also tolerates a slightly proud installation without rocking.
    subtract(
        base,
        cylinder(
            "Horn_centre_screw_relief",
            HORN_CENTRE_SCREW_RELIEF_DIAMETER,
            HORN_CENTRE_SCREW_RELIEF_DEPTH + 0.10,
            -0.05,
            vertices=128,
        ),
    )
    return base


def create_drum_blank() -> bpy.types.Object:
    flange_radius = FLANGE_DIAMETER / 2.0
    core_radius = CORE_DIAMETER / 2.0
    upper_bay_z = BOTTOM_FLANGE_THICKNESS + WINDING_BAY_WIDTH
    profile = (
        (0.0, flange_radius),
        (BOTTOM_FLANGE_THICKNESS, flange_radius),
        (BOTTOM_FLANGE_THICKNESS, core_radius),
        (upper_bay_z, core_radius),
        (upper_bay_z, flange_radius),
        (DRUM_TOTAL_HEIGHT, flange_radius),
    )
    segments = 192
    vertices: list[tuple[float, float, float]] = []
    for z, radius in profile:
        for segment in range(segments):
            theta = 2.0 * pi * segment / segments
            vertices.append((radius * cos(theta), radius * sin(theta), z))

    faces: list[tuple[int, ...]] = []
    for ring in range(len(profile) - 1):
        for segment in range(segments):
            nxt = (segment + 1) % segments
            a = ring * segments + segment
            b = ring * segments + nxt
            c = (ring + 1) * segments + nxt
            d = (ring + 1) * segments + segment
            faces.append((a, b, c, d))
    faces.append(tuple(reversed(range(segments))))
    top_start = (len(profile) - 1) * segments
    faces.append(tuple(top_start + segment for segment in range(segments)))

    mesh = bpy.data.meshes.new("XL330_12mm_Three_Wrap_Drum_Mesh")
    mesh.from_pydata(vertices, [], faces)
    mesh.update(calc_edges=True)
    drum = bpy.data.objects.new("XL330_12mm_Three_Wrap_Drum_PRINT_THIS", mesh)
    bpy.context.collection.objects.link(drum)
    return drum


def add_drum_interface(drum: bpy.types.Object) -> None:
    # Keyed socket receives the base's octagonal torque hub.
    subtract(
        drum,
        prism_from_across_flats(
            "Octagonal_hub_socket_AF7p75",
            HUB_BORE_ACROSS_FLATS,
            8,
            HUB_BORE_DEPTH + 0.10,
            -0.05,
        ),
    )

    # Pockets clear the heads of the four M2 horn-mounting screws.
    radius = MOUNT_PCD / 2.0
    for index in range(4):
        theta = index * pi / 2.0
        subtract(
            drum,
            cylinder(
                f"M2_head_clearance_{index + 1}",
                M2_HEAD_RELIEF_DIAMETER,
                M2_HEAD_RELIEF_DEPTH + 0.05,
                -0.05,
                vertices=96,
                x=radius * cos(theta),
                y=radius * sin(theta),
            ),
        )

    # One M2x8 screw clamps the removable drum to the base.
    subtract(
        drum,
        cylinder(
            "Centre_M2_through_hole",
            CENTRE_M2_SCREW_CLEARANCE_DIAMETER,
            DRUM_TOTAL_HEIGHT + 0.40,
            -0.20,
            vertices=96,
        ),
    )
    subtract(
        drum,
        cylinder(
            "Centre_M2_head_relief",
            CENTRE_M2_HEAD_RELIEF_DIAMETER,
            CENTRE_M2_HEAD_RELIEF_DEPTH + 0.10,
            DRUM_TOTAL_HEIGHT - CENTRE_M2_HEAD_RELIEF_DEPTH,
            vertices=128,
        ),
    )


def add_offset_cord_anchor(drum: bpy.types.Object) -> None:
    # An offset chord avoids the central M2 screw.  The hole is shorter and
    # easier to thread than a diameter-spanning hole.
    cutter = cylinder(
        "2p6mm_offset_cord_anchor",
        CORD_ANCHOR_DIAMETER,
        FLANGE_DIAMETER + 4.0,
        CORD_ANCHOR_Z - (FLANGE_DIAMETER + 4.0) / 2.0,
        vertices=96,
        x=CORD_ANCHOR_OFFSET,
    )
    cutter.location.z = CORD_ANCHOR_Z
    cutter.rotation_mode = "QUATERNION"
    cutter.rotation_quaternion = Vector((0.0, 0.0, 1.0)).rotation_difference(
        Vector((0.0, 1.0, 0.0))
    )
    subtract(drum, cutter)


def bevel_print_part(obj: bpy.types.Object, name: str) -> None:
    modifier = obj.modifiers.new(name=name, type="BEVEL")
    modifier.width = EDGE_BEVEL
    modifier.segments = 2
    modifier.limit_method = "ANGLE"
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.modifier_apply(modifier=modifier.name)


def annotate_parts(base: bpy.types.Object, drum: bpy.types.Object) -> None:
    base["official_horn"] = "ROBOTIS HNX330-N101"
    base["mounting_pattern"] = "4 x M2 clearance on PCD 12 mm"
    base["horn_fasteners"] = "4 x PHS M2x4"
    base["captive_nut"] = "standard M2 hex nut, AF 4.0 mm"
    base["print_orientation"] = "flat horn-facing side on build plate"

    drum["core_diameter_mm"] = CORE_DIAMETER
    drum["flange_diameter_mm"] = FLANGE_DIAMETER
    drum["winding_bay_width_mm"] = WINDING_BAY_WIDTH
    drum["cord_capacity"] = "one 2 mm cord, three side-by-side wraps"
    drum["clamp_screw"] = "M2x8 pan or button head, head OD <= 4.1 mm"
    drum["print_orientation"] = "central M2 screw-head face on build plate"


def create_material(name: str, color: tuple[float, float, float, float]) -> bpy.types.Material:
    material = bpy.data.materials.new(name)
    material.use_nodes = True
    node = material.node_tree.nodes.get("Principled BSDF")
    node.inputs["Base Color"].default_value = color
    node.inputs["Roughness"].default_value = 0.30
    node.inputs["Metallic"].default_value = 0.06
    return material


def create_preview_scene(base: bpy.types.Object, drum: bpy.types.Object) -> None:
    base.data.materials.append(
        create_material("Mount_base_orange", (0.42, 0.055, 0.012, 1.0))
    )
    drum.data.materials.append(
        create_material("Drum_teal", (0.018, 0.22, 0.30, 1.0))
    )

    # Assemble the actual two parts; there are no preview-only cord objects.
    base.location = (0.0, 0.0, 0.0)
    drum.location = (0.0, 0.0, BASE_THICKNESS)

    bpy.ops.mesh.primitive_plane_add(size=120.0, location=(0.0, 0.0, -0.02))
    floor = bpy.context.object
    floor.name = "Render_floor_not_exported"
    floor_material = create_material(
        "Studio_floor",
        (0.045, 0.055, 0.070, 1.0),
    )
    floor_material.node_tree.nodes["Principled BSDF"].inputs["Roughness"].default_value = 0.58
    floor.data.materials.append(floor_material)

    bpy.ops.object.light_add(type="AREA", location=(22.0, -20.0, 34.0))
    key = bpy.context.object
    key.name = "Key_light"
    key.data.energy = 48000.0
    key.data.shape = "DISK"
    key.data.size = 19.0

    bpy.ops.object.light_add(type="AREA", location=(-22.0, -5.0, 19.0))
    fill = bpy.context.object
    fill.name = "Fill_light"
    fill.data.energy = 22000.0
    fill.data.size = 15.0

    bpy.ops.object.light_add(type="AREA", location=(7.0, 24.0, 18.0))
    rim = bpy.context.object
    rim.name = "Rim_light"
    rim.data.energy = 16000.0
    rim.data.size = 12.0

    bpy.ops.object.camera_add(location=(34.0, -34.0, 25.0))
    camera = bpy.context.object
    camera.name = "Preview_camera"
    camera.data.lens = 58.0
    bpy.context.scene.camera = camera
    target = bpy.data.objects.new("Camera_target", None)
    target.location = (0.0, 0.0, (BASE_THICKNESS + DRUM_TOTAL_HEIGHT) * 0.48)
    bpy.context.collection.objects.link(target)
    constraint = camera.constraints.new(type="TRACK_TO")
    constraint.target = target
    constraint.track_axis = "TRACK_NEGATIVE_Z"
    constraint.up_axis = "UP_Y"

    scene = bpy.context.scene
    scene.render.engine = "BLENDER_EEVEE"
    scene.render.resolution_x = 1000
    scene.render.resolution_y = 1000
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.filepath = str(PREVIEW_PATH)
    scene.world.use_nodes = True
    background = scene.world.node_tree.nodes.get("Background")
    background.inputs["Color"].default_value = (0.010, 0.016, 0.026, 1.0)
    background.inputs["Strength"].default_value = 0.40
    scene.view_settings.look = "AgX - Medium High Contrast"


def export_selected(obj: bpy.types.Object, path: Path) -> None:
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    try:
        bpy.ops.wm.stl_export(
            filepath=str(path),
            export_selected_objects=True,
            apply_modifiers=True,
        )
    except (AttributeError, TypeError):
        bpy.ops.export_mesh.stl(filepath=str(path), use_selection=True)


def export_files(base: bpy.types.Object, drum: bpy.types.Object) -> None:
    OUT.mkdir(parents=True, exist_ok=True)

    # Base already sits at its printable origin.
    export_selected(base, BASE_STL_PATH)

    # Export the drum on the build plane, then restore its assembled location.
    assembled_drum_z = drum.location.z
    drum.location.z = 0.0
    bpy.context.view_layer.update()
    export_selected(drum, DRUM_STL_PATH)
    drum.location.z = assembled_drum_z
    bpy.context.view_layer.update()

    bpy.ops.wm.save_as_mainfile(filepath=str(BLEND_PATH))
    bpy.context.scene.render.filepath = str(PREVIEW_PATH)
    bpy.ops.render.render(write_still=True)


clear_scene()
configure_scene_units()
base_object = create_mount_base()
drum_object = create_drum_blank()
add_drum_interface(drum_object)
add_offset_cord_anchor(drum_object)
bevel_print_part(base_object, "Base_FDM_edge_relief")
bevel_print_part(drum_object, "Drum_FDM_edge_relief")
annotate_parts(base_object, drum_object)
create_preview_scene(base_object, drum_object)
export_files(base_object, drum_object)

print("TWO-PIECE SPOOL DESIGN")
print(f"  Drum: OD {FLANGE_DIAMETER:.2f} x H {DRUM_TOTAL_HEIGHT:.2f} mm")
print(f"  Core: OD {CORE_DIAMETER:.2f} mm")
print(f"  Winding bay: {WINDING_BAY_WIDTH:.2f} mm for 3 x 2 mm cord")
print(f"  Base: OD {BASE_DIAMETER:.2f} x H {HUB_TOTAL_HEIGHT:.2f} mm")
print(f"  Key fit: hub AF {HUB_ACROSS_FLATS:.2f}, bore AF {HUB_BORE_ACROSS_FLATS:.2f} mm")
print(f"  Assembly height above horn face: {BASE_THICKNESS + DRUM_TOTAL_HEIGHT:.2f} mm")
print(f"Saved: {BASE_STL_PATH}")
print(f"Saved: {DRUM_STL_PATH}")
print(f"Saved: {BLEND_PATH}")
print(f"Saved: {PREVIEW_PATH}")
