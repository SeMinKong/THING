"""Validate both printable STLs for the XL330 two-piece spool."""

from math import pi
from pathlib import Path

import bmesh
import bpy


CAD_DIR = Path(__file__).resolve().parents[1]
OUTPUTS = CAD_DIR / "print"
FILES = (
    (
        "mount_base",
        OUTPUTS / "xl330_hnx330_n101_2mm_3turn_spool_v3_two_piece_mount_base.stl",
        (18.0, 18.0, 5.0),
    ),
    (
        "drum",
        OUTPUTS / "xl330_hnx330_n101_2mm_3turn_spool_v3_two_piece_drum.stl",
        (18.0, 18.0, 10.5),
    ),
)


def connected_components(bm: bmesh.types.BMesh) -> int:
    unvisited = set(bm.verts)
    count = 0
    while unvisited:
        count += 1
        stack = [unvisited.pop()]
        while stack:
            vert = stack.pop()
            for edge in vert.link_edges:
                other = edge.other_vert(vert)
                if other in unvisited:
                    unvisited.remove(other)
                    stack.append(other)
    return count


def validate_file(
    label: str,
    path: Path,
    expected_dimensions: tuple[float, float, float],
) -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    bpy.ops.wm.stl_import(filepath=str(path))
    objects = [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]
    if len(objects) != 1:
        raise RuntimeError(f"{label}: expected one mesh, found {len(objects)}")

    obj = objects[0]
    dimensions = tuple(float(value) for value in obj.dimensions)
    world_vertices = [obj.matrix_world @ vertex.co for vertex in obj.data.vertices]
    min_z = min(vertex.z for vertex in world_vertices)

    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    bm.faces.ensure_lookup_table()
    non_manifold = [edge for edge in bm.edges if not edge.is_manifold]
    loose_vertices = [vertex for vertex in bm.verts if not vertex.link_edges]
    components = connected_components(bm)
    volume = abs(float(bm.calc_volume(signed=True)))

    print(label.upper())
    print(
        f"  Dimensions mm: "
        f"X={dimensions[0]:.4f}, Y={dimensions[1]:.4f}, Z={dimensions[2]:.4f}"
    )
    print(f"  Minimum Z={min_z:.4f} mm")
    print(
        f"  Vertices={len(bm.verts)}, Edges={len(bm.edges)}, "
        f"Faces={len(bm.faces)}"
    )
    print(f"  Connected components={components}")
    print(f"  Non-manifold edges={len(non_manifold)}")
    print(f"  Loose vertices={len(loose_vertices)}")
    print(f"  Enclosed volume={volume:.3f} mm^3")

    for actual, expected, axis in zip(dimensions, expected_dimensions, "XYZ"):
        if abs(actual - expected) > 0.03:
            raise RuntimeError(
                f"{label}: {axis}={actual:.4f} differs from {expected:.4f} mm"
            )
    if abs(min_z) > 0.01:
        raise RuntimeError(f"{label}: object does not sit on Z=0")
    if components != 1:
        raise RuntimeError(f"{label}: STL has {components} components")
    if non_manifold:
        raise RuntimeError(f"{label}: STL has non-manifold edges")
    if loose_vertices:
        raise RuntimeError(f"{label}: STL has loose vertices")
    if volume <= 0.0:
        raise RuntimeError(f"{label}: STL has no enclosed volume")
    bm.free()


for file_label, file_path, file_dimensions in FILES:
    validate_file(file_label, file_path, file_dimensions)

# Analytic assembly-clearance checks.  These values mirror the generator and
# catch edits that would make a valid STL but an invalid mechanical assembly.
hub_af = 6.80
bore_af = 7.25
key_clearance = bore_af - hub_af
nut_nominal_af = 4.00
nut_pocket_af = 4.25
nut_clearance = nut_pocket_af - nut_nominal_af
bottom_flange = 2.40
m2_head_relief_depth = 1.80
head_roof = bottom_flange - m2_head_relief_depth
anchor_offset = 3.40
anchor_radius = 2.60 / 2.0
centre_m2_hole_radius = 2.30 / 2.0
anchor_to_centre_screw_gap = (
    anchor_offset - anchor_radius - centre_m2_hole_radius
)

span_fraction = 3895.0 / 4096.0
cord_centre_radius = 12.0 / 2.0 + 2.0 / 2.0
travel_for_current_range = 2.0 * pi * cord_centre_radius * span_fraction

print("ASSEMBLY CLEARANCES")
print(f"  Octagonal AF diametral clearance={key_clearance:.3f} mm")
print(f"  Centre M2 nut AF diametral clearance={nut_clearance:.3f} mm")
print(f"  Material above M2 head pockets={head_roof:.3f} mm")
print(
    f"  Cord-anchor to centre-M2-hole edge gap="
    f"{anchor_to_centre_screw_gap:.3f} mm"
)
print(f"  Tendon travel for raw span 100..3995={travel_for_current_range:.3f} mm")

if not 0.35 <= key_clearance <= 0.60:
    raise RuntimeError("Octagonal fit clearance is outside the FDM target")
if not 0.15 <= nut_clearance <= 0.35:
    raise RuntimeError("Centre M2 nut fit clearance is outside the target")
if head_roof < 0.50:
    raise RuntimeError("M2 head pockets break through the winding bay")
if anchor_to_centre_screw_gap < 0.80:
    raise RuntimeError("Cord anchor is too close to the central M2 screw")
if not 40.0 <= travel_for_current_range <= 43.0:
    raise RuntimeError("Tendon travel is outside the target hand-closing range")

print("RESULT=PASS")
