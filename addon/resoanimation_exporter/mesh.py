# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 e1ght3
"""Prepare export-only mesh data; preserve relative morphs and linear skinning.

No source mesh, shape block, modifier or action is applied/deleted. Temporary
evaluated objects are never exported. Source data pointers and modifier flags
are restored before timeline sampling and on every failure.
"""
from contextlib import contextmanager

import bpy
import numpy as np

GEOMETRY_MODIFIERS = {'MIRROR', 'SUBSURF', 'TRIANGULATE'}
NORMAL_MODIFIERS = {'WEIGHTED_NORMAL'}
SHADING_NODES = {
    'NodeGroupInput', 'NodeGroupOutput', 'NodeReroute', 'NodeFrame',
    'GeometryNodeSetShadeSmooth', 'GeometryNodeInputShadeSmooth',
    'GeometryNodeInputEdgeSmooth', 'GeometryNodeInputMeshEdgeAngle',
    'FunctionNodeBooleanMath', 'FunctionNodeCompare', 'ShaderNodeMath',
    'ShaderNodeValue', 'FunctionNodeInputBool', 'FunctionNodeInputInt',
}
GEOMETRY_TOLERANCE = 2e-5


def curves(data):
    """Yield curves assigned to this ID, including its NLA action slots.

    A layered Action may contain animation for many unrelated data blocks.
    Inspecting all channelbags produces false unsupported-animation errors.
    Drivers belong directly to this ID and do not need a slot filter.
    """
    animation = getattr(data, 'animation_data', None)
    if not animation:
        return
    yield from animation.drivers
    actions = set()

    def assigned_action(owner):
        action = getattr(owner, 'action', None)
        if action:
            slot = getattr(owner, 'action_slot', None)
            actions.add((action, slot.handle if slot else None))

    if animation.action:
        assigned_action(animation)

    def strips(items):
        for strip in items:
            assigned_action(strip)
            strips(getattr(strip, 'strips', []))
    for track in animation.nla_tracks:
        strips(track.strips)
    for action, slot_handle in actions:
        if action.is_action_legacy:
            yield from getattr(action, 'fcurves', [])
            continue
        if slot_handle is None:
            continue
        for layer in action.layers:
            for strip in layer.strips:
                for bag in getattr(strip, 'channelbags', []):
                    if bag.slot_handle == slot_handle:
                        yield from bag.fcurves


def validate(obj):
    key = obj.data.shape_keys
    if key:
        if not key.use_relative:
            raise ValueError(f'Absolute Shape Keys are not supported: {obj.name}')
        if any(curves(key)):
            raise ValueError(f'Animated Shape Keys are not exported yet: {obj.name}; export bone/object motion with static shape values')
        if obj.show_only_shape_key:
            raise ValueError(f'Turn off Show Only Shape Key before exporting: {obj.name}')
    if any(curves(obj.data)):
        raise ValueError(f'Animated mesh data is not supported: {obj.name}')
    if any(c.data_path.startswith('modifiers[') for c in curves(obj)):
        raise ValueError(f'Animated modifier settings are not supported: {obj.name}')
    active = [m for m in obj.modifiers if m.show_viewport or m.show_render]
    armatures = [m for m in active if m.type == 'ARMATURE']
    if len(armatures) > 1:
        raise ValueError(f'Multiple Armature modifiers are not supported: {obj.name}')
    after_armature = False
    for mod in active:
        if mod.show_viewport != mod.show_render:
            raise ValueError(f'Modifier viewport/render visibility differs: {obj.name}/{mod.name}')
        if mod.type == 'ARMATURE':
            after_armature = True
            if (not mod.object or mod.use_deform_preserve_volume or mod.use_bone_envelopes or
                    not mod.use_vertex_groups or mod.vertex_group):
                raise ValueError(f'Unsupported Armature skinning settings: {obj.name}/{mod.name}')
        elif mod.type in GEOMETRY_MODIFIERS:
            if after_armature:
                raise ValueError(f'Geometry modifier after Armature cannot be baked safely: {obj.name}/{mod.name}')
            if mod.type == 'MIRROR' and mod.mirror_object:
                raise ValueError(f'Mirror Object dependencies are not supported yet: {obj.name}/{mod.name}')
        elif mod.type == 'NODES':
            group = mod.node_group
            if (not group or any(curves(group)) or any(n.bl_idname not in SHADING_NODES for n in group.nodes)):
                raise ValueError(f'Only shading-only Geometry Nodes are supported: {obj.name}/{mod.name}')
        elif mod.type not in NORMAL_MODIFIERS:
            raise ValueError(f'Unsupported static modifier: {obj.name}/{mod.name} ({mod.type})')


def coordinates(mesh):
    result = np.empty(len(mesh.vertices) * 3, dtype=np.float32)
    mesh.vertices.foreach_get('co', result)
    if not np.isfinite(result).all():
        raise ValueError('Non-finite mesh coordinates')
    return result.astype(np.float64)


def topology(mesh):
    return (len(mesh.vertices), tuple(tuple(e.vertices) for e in mesh.edges),
            tuple((tuple(p.vertices), p.material_index) for p in mesh.polygons))


def weights(mesh):
    return tuple(tuple(sorted((g.group, round(g.weight, 7)) for g in v.groups)) for v in mesh.vertices)


def prepare(context, obj, shape_mode, allocated):
    """Return a new mesh and a diagnostic record; allocated owns all new data."""
    clone = obj.copy()
    clone.data = obj.data.copy()
    source_copy = clone.data
    allocated.append(source_copy)
    context.scene.collection.objects.link(clone)
    try:
        clone.animation_data_clear()
        clone.select_set(False)
        clone.hide_set(False)
        clone.hide_viewport = False
        clone.hide_render = False
        for m in clone.modifiers:
            if m.type == 'ARMATURE':
                m.show_viewport = m.show_render = False
        key = clone.data.shape_keys
        originals = list(obj.data.shape_keys.key_blocks)[1:] if obj.data.shape_keys else []
        values = [0.0 if k.mute else k.value for k in originals]
        def evaluate():
            context.view_layer.update()
            graph = context.evaluated_depsgraph_get()
            result = bpy.data.meshes.new_from_object(clone.evaluated_get(graph), preserve_all_data_layers=True, depsgraph=graph)
            allocated.append(result)
            return result
        modifiers = [m.name for m in obj.modifiers if m.type != 'ARMATURE' and m.show_viewport]
        report = {'object': obj.name, 'staticModifiersBaked': modifiers,
                  'shapeMode': shape_mode, 'shapeKeysPreserved': 0, 'shapeKeysBaked': 0,
                  'maxMorphReconstructionError': 0.0}
        if not key or shape_mode == 'BAKE_START':
            result = evaluate()
            report.update(vertices=len(result.vertices), shapeKeysBaked=len(originals))
            return result, report
        key.animation_data_clear()
        for k in key.key_blocks:
            k.slider_min, k.slider_max = -10, 10
            k.value, k.mute = 0, False
        base = evaluate()
        base_co, base_topology, base_weights = coordinates(base), topology(base), weights(base)
        deltas = []
        def checked_evaluation(label):
            result = evaluate()
            try:
                if topology(result) != base_topology or weights(result) != base_weights:
                    raise ValueError(f'Modifier changes topology or skin weights between Shape Keys: {obj.name}/{label}')
                return coordinates(result)
            finally:
                allocated.remove(result)
                bpy.data.meshes.remove(result)
        for i, original in enumerate(originals, 1):
            k = key.key_blocks[i]
            k.value = 1.0
            delta = checked_evaluation(original.name) - base_co
            deltas.append(delta)
            k.value = 0.5
            error = float(np.max(np.abs(checked_evaluation(original.name + ' half') - (base_co + 0.5 * delta)), initial=0))
            report['maxMorphReconstructionError'] = max(report['maxMorphReconstructionError'], error)
            k.value = 0
        # Test the start shape and a simultaneous blend, not only isolated keys.
        for blend_values in (values, [0.37] * len(originals)):
            expected = base_co.copy()
            for k, value, delta in zip(list(key.key_blocks)[1:], blend_values, deltas):
                k.value = value
                expected += value * delta
            error = float(np.max(np.abs(checked_evaluation('combined shapes') - expected), initial=0))
            report['maxMorphReconstructionError'] = max(report['maxMorphReconstructionError'], error)
        if report['maxMorphReconstructionError'] > GEOMETRY_TOLERANCE:
            raise ValueError(f'Modifiers cannot preserve linear Shape Key combinations: {obj.name} (error {report["maxMorphReconstructionError"]:.6g})')
        # Flatten relative-key chains and vertex-group masks into Basis-relative
        # deltas, which FBX supports without Blender-specific mask metadata.
        clone.data = base
        clone.shape_key_add(name=obj.data.shape_keys.key_blocks[0].name)
        for original, value, delta in zip(originals, values, deltas):
            k = clone.shape_key_add(name=original.name)
            k.data.foreach_set('co', (base_co + delta).astype(np.float32))
            k.slider_min, k.slider_max = original.slider_min, original.slider_max
            k.value = value
        base.update()
        report.update(vertices=len(base.vertices), shapeKeysPreserved=len(originals),
                      shapeNames=[k.name for k in originals], startShapeValues=values)
        return base, report
    finally:
        bpy.data.objects.remove(clone, do_unlink=True)


@contextmanager
def prepared_meshes(context, objects, shape_mode):
    allocated, backups, records = [], [], []
    try:
        for obj in objects:
            if obj.type != 'MESH':
                continue
            modifiers = [m for m in obj.modifiers if m.type != 'ARMATURE' and (m.show_viewport or m.show_render)]
            if not obj.data.shape_keys and not modifiers:
                continue
            result, record = prepare(context, obj, shape_mode, allocated)
            backups.append((obj, obj.data, [(m, m.show_viewport, m.show_render) for m in modifiers], obj.active_shape_key_index))
            obj.data = result
            for m in modifiers:
                m.show_viewport = m.show_render = False
            records.append(record)
        context.view_layer.update()
        yield records
    finally:
        for obj, data, flags, active_key in reversed(backups):
            obj.data = data
            obj.active_shape_key_index = active_key
            for m, viewport, render in flags:
                m.show_viewport, m.show_render = viewport, render
        for data in reversed(allocated):
            bpy.data.meshes.remove(data)
        context.view_layer.update()
