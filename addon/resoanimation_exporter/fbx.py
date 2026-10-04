# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 e1ght3
"""Export fixed-profile FBX and read back its actual model hierarchy."""
import math
from collections import defaultdict

import bpy
from mathutils import Euler, Matrix, Vector

from .format import native_name

OPTIONS = dict(
    use_selection=True, object_types={"EMPTY", "MESH", "ARMATURE"},
    global_scale=1.0, apply_unit_scale=True, apply_scale_options="FBX_SCALE_UNITS",
    axis_forward="-Z", axis_up="Y", use_space_transform=True, bake_space_transform=False,
    primary_bone_axis="Y", secondary_bone_axis="X", use_armature_deform_only=False,
    add_leaf_bones=False, armature_nodetype="NULL", bake_anim=False,
    bake_anim_use_all_actions=False, bake_anim_use_nla_strips=False,
    bake_anim_step=1.0, bake_anim_simplify_factor=0.0, use_mesh_modifiers=True,
    path_mode="COPY", embed_textures=True,
)


def export_model(path):
    result = bpy.ops.export_scene.fbx(filepath=str(path), **OPTIONS)
    if result != {"FINISHED"} or not path.is_file():
        raise RuntimeError("Blender FBX export failed")


def shape_channels(path):
    from io_scene_fbx import parse_fbx
    root, _ = parse_fbx.parse(str(path))
    objects = next(e for e in root.elems if e.id == b'Objects')
    return [e.props[1].split(b'\x00\x01')[0].decode('utf-8') for e in objects.elems
            if e.id == b'Deformer' and e.props[2] == b'BlendShapeChannel']


def skinning_diagnostics(path):
    """Report actual serialized influences without changing user weights.

    The inspected Resonite BoneBinding holds four influences. This is a
    compatibility diagnostic, not a prediction of deformed vertex error.
    """
    from io_scene_fbx import parse_fbx
    root, _ = parse_fbx.parse(str(path))
    objects = {e.props[0]: e for g in root.elems if g.id == b'Objects' for e in g.elems}
    children = defaultdict(list)
    for group in root.elems:
        if group.id == b'Connections':
            for edge in group.elems:
                if edge.props[0] == b'OO':
                    children[edge.props[2]].append(edge.props[1])

    def matches(key, kind, subtype):
        obj = objects.get(key)
        return obj is not None and obj.id == kind and len(obj.props) > 2 and obj.props[2] == subtype

    result = []
    for key, geometry in objects.items():
        if not matches(key, b'Geometry', b'Mesh'):
            continue
        skins = [child for child in children[key] if matches(child, b'Deformer', b'Skin')]
        if not skins:
            continue
        count = len(next(e.props[0] for e in geometry.elems if e.id == b'Vertices')) // 3
        weights = defaultdict(list)
        negative_weights = []
        for skin in skins:
            for cluster in children[skin]:
                if not matches(cluster, b'Deformer', b'Cluster'):
                    continue
                fields = {e.id: e.props[0] for e in objects[cluster].elems if e.props}
                indices, values = fields.get(b'Indexes', []), fields.get(b'Weights', [])
                if len(indices) != len(values):
                    raise ValueError('FBX skin indices and weights differ in length')
                for index, weight in zip(indices, values):
                    if not 0 <= index < count or not math.isfinite(weight):
                        raise ValueError('Invalid FBX skin index or weight')
                    if weight > 0:
                        weights[index].append(weight)
                    elif weight < 0:
                        # Evaluated subdivision can generate tiny negative
                        # values. Diagnose them, never silently alter the FBX.
                        negative_weights.append(weight)
        result.append({'geometry': geometry.props[1].split(b'\x00\x01')[0].decode('utf-8'),
                       'controlPoints': count, 'maxInfluences': max(map(len, weights.values()), default=0),
                       'verticesOverFourInfluences': sum(len(values) > 4 for values in weights.values()),
                       'unweightedControlPoints': count - len(weights),
                       'negativeWeightCount': len(negative_weights),
                       'minimumNegativeWeight': min(negative_weights, default=0),
                       'maxNormalizedWeightOutsideTopFour': max(
                           (sum(sorted(values, reverse=True)[4:]) / sum(values) for values in weights.values()), default=0)})
    return result


def assign_native_names(path, nodes):
    """Rename only serialized FBX Model labels; leave Blender data untouched.

    Skin clusters, bind poses, geometry and hierarchy connect by numeric FBX
    IDs. Preserve every other parsed element and verify the rewritten file.
    """
    from io_scene_fbx import parse_fbx, encode_bin
    aliases = {n['id']: native_name(n['sourceKey']) for n in nodes}
    if len(set(aliases.values())) != len(nodes):
        raise ValueError('Native binding name collision')
    root, version = parse_fbx.parse(str(path))
    objects = next(e for e in root.elems if e.id == b'Objects')
    changed = set()
    for elem in objects.elems:
        if elem.id == b'Model':
            key = elem.props[0]
            if key not in aliases or elem.props_type[1] != ord('S'):
                raise ValueError('Unexpected FBX Model while assigning binding names')
            _, separator, kind = elem.props[1].partition(b'\x00\x01')
            if not separator:
                raise ValueError('Unexpected FBX Model name encoding')
            elem.props[1] = aliases[key].encode('utf-8') + separator + kind
            changed.add(key)
    if changed != set(aliases):
        raise ValueError('FBX binding name assignment was incomplete')
    writers = dict(zip(b'BCZYILFDRSilfdbc', (
        'add_bool','add_char','add_int8','add_int16','add_int32','add_int64',
        'add_float32','add_float64','add_bytes','add_string','add_int32_array',
        'add_int64_array','add_float32_array','add_float64_array','add_bool_array','add_byte_array')))

    def encode(elem):
        out = encode_bin.FBXElem(elem.id)
        for typ, value in zip(elem.props_type, elem.props):
            method = writers.get(typ)
            if method is None:
                raise ValueError(f'Unsupported FBX property encoding: {typ}')
            getattr(out, method)(value)
        out.elems = [encode(child) for child in elem.elems]
        return out

    encode_bin.write(str(path), encode(root), version)
    actual, actual_version = parse_fbx.parse(str(path))

    def verify(expected, observed):
        if (expected.id != observed.id or expected.props_type != observed.props_type or
                len(expected.props) != len(observed.props) or len(expected.elems) != len(observed.elems)):
            raise ValueError('FBX rewrite changed the element structure')
        if any(a != b for a, b in zip(expected.props, observed.props)):
            raise ValueError(f'FBX rewrite changed data in {expected.id!r}')
        for a, b in zip(expected.elems, observed.elems):
            verify(a, b)

    if version != actual_version:
        raise ValueError('FBX rewrite changed the file version')
    verify(root, actual)
    return aliases


def read_models(path):
    # This parser ships with Blender 5.0. It is intentionally version-pinned.
    from io_scene_fbx import parse_fbx
    root, version = parse_fbx.parse(str(path))
    sections = {e.id: e for e in root.elems}
    settings = next(e for e in sections[b"GlobalSettings"].elems if e.id == b"Properties70")
    unit = next(p.props[4] for p in settings.elems if p.props[0] == b"UnitScaleFactor") / 100
    models = {}
    for e in sections[b"Objects"].elems:
        if e.id != b"Model":
            continue
        props = {p.props[0]: p.props[4:] for group in e.elems if group.id == b"Properties70" for p in group.elems}
        for key in (b"PreRotation", b"PostRotation", b"GeometricTranslation", b"GeometricRotation"):
            if any(props.get(key, (0, 0, 0))):
                raise ValueError(f"Unexpected FBX transform {key!r}")
        if props.get(b"RotationOrder", [0])[0] != 0:
            raise ValueError("Unexpected FBX Euler order")
        p = Vector(props.get(b"Lcl Translation", (0, 0, 0))) * unit
        q = Euler([math.radians(v) for v in props.get(b"Lcl Rotation", (0, 0, 0))], "XYZ").to_quaternion()
        s = Vector(props.get(b"Lcl Scaling", (1, 1, 1)))
        models[e.props[0]] = {"name": e.props[1].split(b"\x00\x01")[0].decode("utf-8"),
                              "kind": e.props[2], "local": Matrix.LocRotScale(p, q, s)}
    for c in sections[b"Connections"].elems:
        if c.id == b"C" and c.props[0] == b"OO":
            child, parent = c.props[1:3]
            if child in models and (parent == 0 or parent in models):
                if "parent" in models[child]:
                    raise ValueError("FBX model has multiple parents")
                models[child]["parent"] = parent
    def resolve(key, visiting):
        model = models[key]
        if "path" in model:
            return
        if key in visiting or "parent" not in model:
            raise ValueError("Invalid FBX hierarchy")
        parent = model["parent"]
        if parent:
            resolve(parent, visiting | {key})
        model["path"] = (models[parent]["path"] if parent else []) + [model["name"]]
        model["world"] = (models[parent]["world"] if parent else Matrix.Identity(4)) @ model["local"]
    for key in models:
        resolve(key, set())
    paths = [tuple(m["path"]) for m in models.values()]
    if len(set(paths)) != len(paths):
        raise ValueError("Duplicate FBX sibling names; rename the colliding object/bone")
    return models, unit


def map_sources(objects, models):
    nodes = []
    def single(candidates, label):
        if len(candidates) != 1:
            raise ValueError(f"FBX node is missing or ambiguous: {label}")
        return candidates[0]
    for obj in objects:
        model_id = single([k for k, m in models.items() if m["name"] == obj.name and m["kind"] != b"LimbNode"], obj.name)
        nodes.append({"object": obj, "bone": None, "id": model_id, "sourceKey": ["OBJECT", obj.name]})
        if obj.type == "ARMATURE":
            for bone in obj.data.bones:
                chain, current = [], bone
                while current:
                    chain.insert(0, current.name)
                    current = current.parent
                path = models[model_id]["path"] + chain
                bone_id = single([k for k, m in models.items() if m["path"] == path and m["kind"] == b"LimbNode"], str(path))
                nodes.append({"object": obj, "bone": bone.name, "id": bone_id, "sourceKey": ["OBJECT", obj.name, "BONE", bone.name]})
    if len(nodes) != len(models) or len({n["id"] for n in nodes}) != len(models):
        raise ValueError("Unexpected FBX model nodes (instances or unsupported hierarchy)")
    return sorted(nodes, key=lambda n: n["sourceKey"])
