# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 e1ght3
"""Evaluated timeline -> rest FBX + absolute local AnimJ tracks."""
from contextlib import contextmanager
import math
from pathlib import Path
import tempfile

import bpy
from bpy_extras.io_utils import axis_conversion
from mathutils import Matrix

from . import fbx, mesh
from .format import (VERSION, PROFILE, PROPERTIES, compare_reference, digest, file_digest,
                     load_reference_bundle, node_token, sample_frames, sample_times, write_json)

MIRROR = Matrix.Diagonal((-1, 1, 1, 1))
LEFT = MIRROR @ axis_conversion(to_forward="-Z", to_up="Y").to_4x4()
TRS_TOLERANCE = 1e-5


def matrix_error(a, b):
    return max(abs(a[i][j] - b[i][j]) for i in range(4) for j in range(4))


def decompose(matrix, label):
    if not all(math.isfinite(v) for row in matrix for v in row):
        raise ValueError(f"Non-finite transform: {label}")
    p, q, s = matrix.decompose()
    residual = matrix_error(matrix, Matrix.LocRotScale(p, q, s))
    if min(s) <= 1e-8 or matrix.determinant() <= 0 or residual > TRS_TOLERANCE:
        raise ValueError(f"Unsupported shear / reflected or zero scale: {label} (TRS error {residual:.6g})")
    q.normalize()
    return p, q, s


def values(p, q, s):
    return [dict(zip("xyz", p)), dict(zip("xyzw", (q.x, q.y, q.z, q.w))), dict(zip("xyz", s))]


def collect_objects(context, scope, collection_name=""):
    if scope == "SELECTED":
        initial = list(context.selected_objects)
    elif scope == "COLLECTION":
        collection = bpy.data.collections.get(collection_name)
        if not collection:
            raise ValueError("Choose an existing Collection")
        initial = list(collection.all_objects)
    else:
        raise ValueError("Unknown export scope")
    if not initial:
        raise ValueError("Select objects or choose a nonempty Collection")
    result, pending = set(), list(initial)
    while pending:
        obj = pending.pop()
        if obj in result:
            continue
        if obj.name not in context.view_layer.objects:
            raise ValueError(f"Object is outside the current view layer: {obj.name}")
        if obj.type not in {"MESH", "EMPTY", "ARMATURE"}:
            raise ValueError(f"Unsupported object type: {obj.name} ({obj.type}); choose only Mesh/Empty/Armature")
        result.add(obj)
        if obj.parent:
            pending.append(obj.parent)
        for modifier in obj.modifiers:
            if modifier.type == "ARMATURE" and modifier.show_viewport and modifier.object:
                pending.append(modifier.object)
    return sorted(result, key=lambda o: o.name), sorted(o.name for o in result - set(initial))


def preflight(objects):
    warnings = []
    checked_images = set()
    for obj in objects:
        if obj.parent_type not in {"OBJECT", "BONE"}:
            raise ValueError(f"Unsupported parenting: {obj.name} ({obj.parent_type})")
        if obj.parent_type == "BONE" and (not obj.parent or obj.parent_bone not in obj.parent.data.bones):
            raise ValueError(f"Missing parent bone: {obj.name}")
        if obj.instance_type != "NONE" or obj.rigid_body or obj.rigid_body_constraint:
            raise ValueError(f"Instances / physics are not supported yet: {obj.name}")
        if obj.type == "ARMATURE":
            if obj.data.pose_position != "POSE":
                raise ValueError(f"Set Armature to Pose Position before exporting: {obj.name}")
            for bone in obj.data.bones:
                if bone.bbone_segments != 1:
                    raise ValueError(f"Segmented B-Bone is not supported: {obj.name}/{bone.name}")
        if obj.type == "MESH":
            mesh.validate(obj)
            if any(slot.material and slot.material.use_nodes for slot in obj.material_slots):
                warnings.append(f"{obj.name}: FBX materials/textures use Blender's standard conversion; Resonite appearance is not verified.")
            for slot in obj.material_slots:
                if not slot.material or not slot.material.use_nodes:
                    continue
                for node in slot.material.node_tree.nodes:
                    image = getattr(node, 'image', None)
                    if not image or image in checked_images:
                        continue
                    checked_images.add(image)
                    if image.source == 'FILE' and not image.packed_file:
                        path = Path(bpy.path.abspath(image.filepath, library=image.library))
                        if not path.is_file():
                            warnings.append(f'Missing texture file: {image.name} ({path}). Re-link or pack the image for a textured FBX; original image paths are not changed.')
    return warnings


@contextmanager
def preserved_state(context, objects):
    scene, layer = context.scene, context.view_layer
    state = (scene.frame_current, scene.frame_subframe, list(context.selected_objects), layer.objects.active, context.mode)
    poses = {o.data: o.data.pose_position for o in objects if o.type == "ARMATURE"}
    visibility = {o: (o.hide_get(), o.hide_viewport, o.hide_render, o.hide_select) for o in objects}
    try:
        if context.mode == "POSE":
            bpy.ops.object.mode_set(mode="OBJECT")
        for obj in layer.objects:
            obj.select_set(False)
        for obj in objects:
            obj.hide_set(False)
            obj.hide_viewport = obj.hide_render = obj.hide_select = False
        layer.update()
        for obj in objects:
            if not obj.visible_get():
                raise ValueError(f'Export collection must be visible in the current view layer: {obj.name}')
            obj.select_set(True)
        layer.objects.active = objects[0]
        yield
    finally:
        for data, position in poses.items():
            data.pose_position = position
        scene.frame_set(state[0], subframe=state[1])
        layer.update()
        for obj in layer.objects:
            obj.select_set(False)
        for obj in state[2]:
            obj.select_set(True)
        layer.objects.active = state[3]
        if state[4] == "POSE":
            bpy.ops.object.mode_set(mode="POSE")
        for obj, flags in visibility.items():
            obj.hide_set(flags[0])
            obj.hide_viewport, obj.hide_render, obj.hide_select = flags[1:]


def world_samples(context, nodes, meters):
    graph = context.evaluated_depsgraph_get()
    evaluated = {n["object"]: n["object"].evaluated_get(graph) for n in nodes}
    result = {}
    for node in nodes:
        obj = evaluated[node["object"]]
        world = obj.matrix_world.copy()
        if node["bone"]:
            world = world @ obj.pose.bones[node["bone"]].matrix
        world.translation *= meters
        result[node["id"]] = LEFT @ world @ MIRROR
    return result


def local_matrix(node_id, world, models):
    parent = models[node_id]["parent"]
    return world[parent].inverted() @ world[node_id] if parent else world[node_id]


def export_bundle(context, destination, *, scope="SELECTED", collection_name="", start=None, end=None, step=0.25, name="Motion", binding_mode="NATIVE_NAMES", include_fbx=True, shape_mode="PRESERVE", reference_bundle=""):
    if bpy.app.version[:2] != (5, 0):
        raise ValueError("This preview supports Blender 5.0.x only (tested: 5.0.1)")
    if context.mode not in {"OBJECT", "POSE"}:
        raise ValueError("Switch to Object or Pose Mode before exporting")
    if binding_mode not in {"NATIVE_NAMES", "RA1_PATH"}:
        raise ValueError("Unknown animation binding mode")
    if shape_mode not in {'PRESERVE', 'BAKE_START'}:
        raise ValueError('Unknown Shape Keys mode')
    scene = context.scene
    start = scene.frame_start if start is None else start
    end = scene.frame_end if end is None else end
    frames = sample_frames(start, end, step)
    fps = scene.render.fps / scene.render.fps_base
    times = sample_times(frames, fps)
    frame_parameter = scene.bl_rna.functions['frame_set'].parameters['frame']
    if math.floor(start) < frame_parameter.hard_min or math.floor(end) > frame_parameter.hard_max:
        raise ValueError(f'Frame range exceeds Blender limits: {frame_parameter.hard_min} to {frame_parameter.hard_max}')
    destination = Path(destination).expanduser().absolute()
    if destination.exists():
        raise ValueError("Output folder already exists; choose a new bundle name")
    reference = load_reference_bundle(bpy.path.abspath(reference_bundle)) if reference_bundle else None
    objects, added = collect_objects(context, scope, collection_name)
    warnings = preflight(objects)
    count = len(objects) + sum(len(o.data.bones) for o in objects if o.type == "ARMATURE")
    if len(frames) * count * 3 > 2000000:
        raise ValueError("Export exceeds two million keys; shorten the range or increase Sample Step")
    meters = 1.0 if scene.unit_settings.system == "NONE" else scene.unit_settings.scale_length
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".resoanimation-", dir=destination.parent) as temp, preserved_state(context, objects):
        stage = Path(temp) / "bundle"
        stage.mkdir()
        scene.frame_set(math.floor(start), subframe=start % 1)
        rigs = [o for o in objects if o.type == "ARMATURE"]
        for obj in rigs:
            obj.data.pose_position = "REST"
        context.view_layer.update()
        with mesh.prepared_meshes(context, objects, shape_mode) as mesh_preparation:
            fbx.export_model(stage / "model.fbx")
            shape_channels = fbx.shape_channels(stage / 'model.fbx')
            skinning = fbx.skinning_diagnostics(stage / 'model.fbx')
            expected_shapes = sum(r['shapeKeysPreserved'] for r in mesh_preparation)
            if len(shape_channels) != expected_shapes:
                raise ValueError(f'FBX Shape Key count mismatch: expected {expected_shapes}, got {len(shape_channels)}')
        models, fbx_meters = fbx.read_models(stage / "model.fbx")
        if abs(fbx_meters - meters) > 1e-7 * max(1, meters):
            raise ValueError("FBX and AnimJ unit mismatch")
        nodes = fbx.map_sources(objects, models)
        original_paths = {key: model['path'][:] for key, model in models.items()}
        if binding_mode == "NATIVE_NAMES":
            aliases = fbx.assign_native_names(stage / "model.fbx", nodes)
            models, rewritten_meters = fbx.read_models(stage / "model.fbx")
            if set(models) != set(aliases) or rewritten_meters != fbx_meters:
                raise ValueError("Native FBX rewrite changed model identities or units")
            if any(models[key]['name'] != alias for key, alias in aliases.items()):
                raise ValueError("Native FBX names were not retained")
        rest_world = world_samples(context, nodes, meters)
        rest, rest_errors = {}, []
        for node in nodes:
            key = node["id"]
            expected = rest_world[key]
            from_fbx = MIRROR @ models[key]["world"] @ MIRROR
            delta = matrix_error(expected, from_fbx)
            rest_errors.append(delta)
            if delta > 2e-5:
                raise ValueError(f"FBX rest transform differs from evaluated scene: {node['sourceKey']} ({delta:.6g})")
            rest[key] = values(*decompose(local_matrix(key, rest_world, models), f"{node['sourceKey']} REST"))
        fingerprint = digest([{"path": models[n["id"]]["path"], "rest": rest[n["id"]]} for n in nodes])
        tracks, bindings, node_report = [], [], []
        for node in nodes:
            key, model = node["id"], models[node["id"]]
            token = model["name"] if binding_mode == "NATIVE_NAMES" else node_token(model["path"])
            node_report.append({"sourceKey": node["sourceKey"], "targetPathSegments": model["path"],
                                "parentPathSegments": models[model["parent"]]["path"] if model["parent"] else [],
                                "restTRS": rest[key]})
            if binding_mode == "NATIVE_NAMES":
                node_report[-1].update(nativeName=token, originalTargetPathSegments=original_paths[key])
            for prop, value_type in PROPERTIES:
                bindings.append({"index": len(tracks), "node": token, "property": prop, "valueType": value_type,
                                 "sourceKey": node["sourceKey"], "targetPathSegments": model["path"]})
                tracks.append({"trackType": "Curve", "valueType": value_type,
                               "data": {"node": token, "property": prop, "keyframes": []}})
        reference_check = compare_reference(reference, node_report, binding_mode) if reference else None
        for obj in rigs:
            obj.data.pose_position = "POSE"
        context.view_layer.update()
        previous = {}
        for frame, seconds in zip(frames, times):
            scene.frame_set(math.floor(frame), subframe=frame % 1)
            world = world_samples(context, nodes, meters)
            for i, node in enumerate(nodes):
                p, q, s = decompose(local_matrix(node["id"], world, models), f"{node['sourceKey']} frame {frame:g}")
                if i in previous and previous[i].dot(q) < 0:
                    q.negate()
                previous[i] = q.copy()
                for j, value in enumerate(values(p, q, s)):
                    tracks[3 * i + j]["data"]["keyframes"].append({"time": seconds, "value": value, "interpolation": "Linear"})
        clip = {"name": name, "globalDuration": (end - start) / fps, "tracks": tracks}
        write_json(stage / "motion.animj", clip)
        binding_manifest = {"schemaVersion": 4 if binding_mode == "NATIVE_NAMES" else 3, "profile": PROFILE,
            "pathBase": "FBX scene root (usually RootNode), excluding import placement slot",
            "modelSignature": fingerprint, "nodes": node_report, "bindings": bindings}
        if binding_mode == "NATIVE_NAMES":
            binding_manifest['bindingMode'] = binding_mode
            binding_manifest['namingPolicy'] = 'source-key-sha256-20-readable-suffix-v1'
        write_json(stage / "motion.bindings.json", binding_manifest)
        if not include_fbx:
            # FBX still supplies the exact basis internally, but is not a
            # deliverable when the user requests animation-only output.
            (stage / 'model.fbx').unlink()
            if not reference_check:
                warnings.append('AnimJ-only: no existing model was checked. Use Existing Model (optional) to compare names, hierarchy and rest pose against its original bundle.')
        warnings.append("Preview profile: bone playback verified on test rigs; new geometry/shape exports require live verification.")
        over_four = sum(r['verticesOverFourInfluences'] for r in skinning)
        if over_four:
            warnings.append(f'{over_four} FBX control points have more than 4 bone influences. Resonite keeps at most 4; deformation may differ. Weights were not modified. See skinningDiagnostics.')
        unweighted = sum(r['unweightedControlPoints'] for r in skinning)
        if unweighted:
            warnings.append(f'{unweighted} FBX control points in skinned meshes have no positive bone weights. Resonite may fill empty bindings; inspect deformation.')
        warnings.append('Shape Key animation is not included in AnimJ. Preserve writes static controls and DeformPercent to FBX, but Resonite does not restore those initial weights automatically.')
        initial_shapes = []
        for prepared in mesh_preparation:
            native = next(models[n['id']]['name'] for n in nodes if not n['bone'] and n['object'].name == prepared['object'])
            for shape_name, weight in zip(prepared.get('shapeNames', []), prepared.get('startShapeValues', [])):
                if weight != 0:
                    initial_shapes.append({'object': prepared['object'], 'targetSlotName': native,
                                           'shapeName': shape_name, 'value': weight})
        if shape_channels:
            warnings.append('Resonite may remove tiny/empty shape controls during import. Match by shape name, not by the original Blender index.')
        if include_fbx and initial_shapes:
            warnings.append('Set the listed shapeInitialValues manually in Resonite, or export with Bake Start Shape to preserve the starting appearance without adjustable shapes.')
        report = {"exporterVersion": VERSION, "blenderVersion": bpy.app.version_string, "profile": PROFILE, "bindingMode": binding_mode,
            "includeFBX": bool(include_fbx), "shapeMode": shape_mode, "meshPreparation": mesh_preparation,
            "shapeKeysInReferenceFBX": len(shape_channels), "shapeKeyAnimationExported": False,
            "shapeInitialValues": initial_shapes, "resoniteRestoresShapeInitialValues": False,
            "referenceModelCheck": reference_check,
            "skinningDiagnostics": skinning,
            "modelSignature": fingerprint, "objects": len(objects), "bones": len(nodes) - len(objects),
            "tracks": len(tracks), "samplesPerTrack": len(frames), "frameStart": start, "frameEnd": end,
            "frameStep": step, "fps": fps, "durationSeconds": clip["globalDuration"], "metersPerUnit": meters,
            "automaticallyIncludedObjects": added, "fbxRestMatrixMaxError": max(rest_errors),
            "warnings": warnings, "liveResoniteVerifiedForThisExport": False,
            "filesSHA256": {p.name: file_digest(p) for p in stage.iterdir() if p.is_file()}}
        write_json(stage / "export-report.json", report)
        instructions = (
            "Attach Animator to the OUTER model slot, above ALL animated slots.\n"
            "Set Clip to the imported animation provider; wait for loading.\n"
            "Press 'Setup fields by name' in the Animator Inspector, then verify all Fields before Play.\n"
            "No PC helper or Resoloop is required for this connection method.\n"
            "Do not rename the RA_ model/bone slots, combine identical model copies under one Animator,\n"
            "or bind over an existing Animator/IK driver. Use a matching FBX model and AnimJ.\n"
            if binding_mode == "NATIVE_NAMES" else
            "Legacy RA1 mode: use motion.bindings.json for manual/developer connection.\n"
            "'Setup fields by name' does not interpret RA1 encoded paths.\n")
        (stage / "READ-ME.txt").write_text(
            f"AnimJ exporter for Resonite {VERSION} / {binding_mode}\n\n" +
            ("Import model.fbx and motion.animj into Resonite.\n" if include_fbx else
             "Import motion.animj and use your existing matching model. This folder intentionally contains NO FBX.\n"
             "Compare modelSignature in export-report.json with the model bundle; names/hierarchy/units/rest pose must match.\n") +
            "FBX: Advanced, Scale=1, Auto Scale off, Rig and Import Bones on, Setup IK / Force T-Pose / Animations off.\n"
            "AnimJ must pass through the Resonite importer; do not set its URL on StaticAnimationProvider.\n"
            "Keep the StaticAnimationProvider inside the model's outer slot before saving the whole item.\n"
            "Shape Key animation is NOT included. Preserve exports static shape controls to FBX; Bake Start Shape flattens them.\n"
            "Resonite initializes shape weights to 0 and may remove tiny/empty shapes. Match controls by name, NOT Blender index.\n"
            "The importer may rename a control (for example, Name.Name); verify its actual Inspector label. Names below are FBX names.\n"
            "For the starting appearance, set the weights below in each mesh's SkinnedMeshRenderer, or use Bake Start Shape.\n"
            + ''.join(f"  {s['targetSlotName']} / {s['shapeName']} = {s['value']:g}\n" for s in initial_shapes)
            + (f"Existing model verified: {reference_check['bundleName']}\nScope: {reference_check['scope']}\n" if reference_check else
               "Existing model compatibility was not checked against a previous bundle.\n")
            + instructions +
            (f"Skinning: {over_four} control points exceed 4 bone influences; {unweighted} have no positive weights. See export-report.json.\n" if over_four or unweighted else '') +
            "motion.bindings.json records Animator.Fields order, source names and rest pose.\n"
            "Path base: the FBX scene root (usually RootNode), not the outer placement slot.\n"
            "See project docs/blender-addon-user-guide.ja.md for installation and limits.\n", encoding="utf-8")
        # Publish only a complete bundle. Never merge with a previous export.
        if destination.exists():
            raise ValueError("Output folder appeared during export; choose another name")
        stage.rename(destination)
    return report
