# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 e1ght3
"""Local preview addon. No network access or third-party Python packages."""
bl_info = {
    "name": "AnimJ exporter for Resonite", "author": "e1ght3",
    "version": (0, 3, 1), "blender": (5, 0, 0), "location": "File > Export",
    "description": "Export object and bone AnimJ, with an optional FBX preserving static shape keys",
    "category": "Import-Export",
}

from pathlib import Path
import traceback

import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, IntProperty, StringProperty
from bpy_extras.io_utils import ExportHelper

from .core import export_bundle


class RESOANIMATION_OT_export(bpy.types.Operator, ExportHelper):
    bl_idname = "export_scene.resoanimation"
    bl_label = "AnimJ exporter for Resonite"
    bl_options = {"PRESET"}
    filename_ext = ".resobundle"
    filter_glob: StringProperty(default="*.resobundle", options={"HIDDEN"})
    scope: EnumProperty(name="Objects", items=(("SELECTED", "Selected Objects", "Include selected objects, their parents and referenced armatures"),
                                              ("COLLECTION", "Collection", "Export a collection and its descendants")))
    collection_name: StringProperty(name="Collection")
    include_fbx: BoolProperty(name="Export FBX", default=True,
        description="Include model.fbx with AnimJ. Disable to update animation on an existing matching exported model")
    reference_bundle: StringProperty(name="Existing Model (optional)", subtype='DIR_PATH',
        description="Previous model .resobundle folder. Verify hashes, target names, hierarchy and exact rest transforms before exporting another clip")
    shape_mode: EnumProperty(name="Shape Keys", default="PRESERVE", items=(
        ("PRESERVE", "Preserve in FBX", "Keep static relative Shape Keys; set initial weights in Resonite manually. Expression animation is not exported"),
        ("BAKE_START", "Bake Start Shape", "Flatten the shape at Start Frame in the export copy; original Blender Shape Keys remain unchanged")))
    binding_mode: EnumProperty(name="Connection", default="NATIVE_NAMES", items=(
        ("NATIVE_NAMES", "Resonite: Setup fields by name", "Export matching unique names for Resonite's built-in Animator button; no PC helper"),
        ("RA1_PATH", "Legacy: RA1 paths", "Keep original model names and encoded path metadata for developer/manual binding")))
    frame_start: IntProperty(name="Start Frame", default=1)
    frame_end: IntProperty(name="End Frame", default=61)
    frame_step: FloatProperty(name="Sample Step (frames)", default=0.25, min=0.01, max=100, precision=2)

    def invoke(self, context, event):
        self.frame_start, self.frame_end = context.scene.frame_start, context.scene.frame_end
        if not self.filepath:
            self.filepath = "motion.resobundle"
        return ExportHelper.invoke(self, context, event)

    def draw(self, context):
        layout = self.layout
        layout.label(text="Preview: Blender 5.0.x / Resonite")
        layout.prop(self, "scope")
        if self.scope == "COLLECTION":
            layout.prop_search(self, "collection_name", bpy.data, "collections")
        layout.prop(self, "frame_start")
        layout.prop(self, "frame_end")
        layout.prop(self, "frame_step")
        layout.prop(self, "include_fbx")
        if self.include_fbx:
            layout.prop(self, "shape_mode")
            if self.shape_mode == "PRESERVE":
                layout.label(text="Resonite starts shape weights at 0.")
                layout.label(text="Set initial weights using READ-ME.txt.")
        else:
            layout.label(text="Use the same previously exported model.")
            layout.label(text="Keep names, hierarchy, units and rest pose.")
            layout.prop(self, 'reference_bundle')
            layout.label(text="Optional: verify against its original bundle.")
        layout.prop(self, "binding_mode")
        if self.binding_mode == "NATIVE_NAMES":
            layout.label(text="FBX node names get unique RA_ names.")
            layout.label(text="Original Blender names stay unchanged.")
        layout.label(text="Creates a NEW folder; does not overwrite.")
        layout.label(text="Select meshes too; selecting a rig alone excludes meshes.")

    def execute(self, context):
        try:
            destination = Path(bpy.path.abspath(self.filepath))
            if destination.suffix != self.filename_ext:
                destination = destination.with_name(destination.name + self.filename_ext)
            report = export_bundle(context, destination, scope=self.scope, collection_name=self.collection_name,
                start=self.frame_start, end=self.frame_end, step=self.frame_step, name=destination.stem,
                binding_mode=self.binding_mode, include_fbx=self.include_fbx, shape_mode=self.shape_mode,
                reference_bundle=self.reference_bundle if not self.include_fbx else '')
        except Exception as exc:
            traceback.print_exc()
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        if any(r['verticesOverFourInfluences'] or r['unweightedControlPoints'] for r in report['skinningDiagnostics']):
            self.report({"WARNING"}, "Export complete. Skin weights may deform differently in Resonite; see READ-ME.txt and export-report.json.")
        elif self.include_fbx and report['shapeInitialValues']:
            self.report({"WARNING"}, "Export complete. Set initial shape weights in Resonite; see READ-ME.txt, or use Bake Start Shape.")
        else:
            self.report({"INFO"}, f"Exported {report['tracks']} tracks to {destination.name}; see export-report.json")
        return {"FINISHED"}


def menu_export(self, context):
    self.layout.operator(RESOANIMATION_OT_export.bl_idname, text="AnimJ exporter for Resonite (.resobundle)")


def register():
    bpy.utils.register_class(RESOANIMATION_OT_export)
    bpy.types.TOPBAR_MT_file_export.append(menu_export)


def unregister():
    bpy.types.TOPBAR_MT_file_export.remove(menu_export)
    bpy.utils.unregister_class(RESOANIMATION_OT_export)
