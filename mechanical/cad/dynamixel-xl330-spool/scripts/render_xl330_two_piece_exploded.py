"""Render a useful two-part inspection view without adding decorative geometry."""

from math import pi
from pathlib import Path

import bpy


CAD_DIR = Path(__file__).resolve().parents[1]
OUTPUT_PATH = (
    CAD_DIR
    / "generated"
    / "xl330_hnx330_n101_2mm_3turn_spool_v3_two_piece_parts_preview.png"
)
OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

base = bpy.data.objects.get("HNX330_Mount_Base_PRINT_THIS")
drum = bpy.data.objects.get("XL330_12mm_Three_Wrap_Drum_PRINT_THIS")
camera = bpy.data.objects.get("Preview_camera")
target = bpy.data.objects.get("Camera_target")
if None in (base, drum, camera, target):
    raise RuntimeError("Expected assembly objects were not found")

# Base: hub and captive-nut pocket upward.
base.location = (-10.5, 0.0, 0.0)
base.rotation_euler = (0.0, 0.0, 0.0)

# Drum: flip it so the keyed socket and M2 head pockets face upward.
drum.location = (10.5, 0.0, 10.5)
drum.rotation_euler = (pi, 0.0, 0.0)

camera.location = (38.0, -46.0, 34.0)
camera.data.lens = 58.0
target.location = (0.0, 0.0, 3.2)
bpy.context.scene.render.filepath = str(OUTPUT_PATH)
bpy.ops.render.render(write_still=True)
print(f"Saved parts inspection render: {OUTPUT_PATH}")
