# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 e1ght3
"""Versioned, bpy-independent metadata shared by exporter and future binders."""
import hashlib
import json
import math
from pathlib import Path
import struct

VERSION = "0.3.1"
PROFILE = "blender5-fbx-yup-minus-z-bones-yx-v1"
PROPERTIES = (("Position", "float3"), ("Rotation", "floatQ"), ("Scale", "float3"))


def canonical(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def file_digest(path):
    # Do not duplicate an entire embedded-texture FBX in memory just to hash it.
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def sample_frames(start, end, step):
    if not all(math.isfinite(v) for v in (start, end, step)) or end < start or step <= 0:
        raise ValueError("Invalid frame range or sample step")
    intervals = (end - start) / step
    if not math.isfinite(intervals) or intervals > 100000:
        raise ValueError("Too many samples; shorten the range or increase Sample Step")
    count = math.floor(intervals + 1e-9)
    frames = [start + i * step for i in range(count + 1)]
    if len(frames) == 1 and end != start:
        frames.append(end)
    elif abs(frames[-1] - end) < 1e-7:
        frames[-1] = end
    else:
        frames.append(end)
    if any(b <= a for a, b in zip(frames, frames[1:])):
        raise ValueError('Sample Step is too small to distinguish frames at this range')
    return frames


def sample_times(frames, fps):
    """Keep existing JSON times, but verify the engine's float32 time domain."""
    if not math.isfinite(fps) or fps <= 0:
        raise ValueError('Invalid scene frame rate')
    if not frames:
        raise ValueError('No sample frames')
    times = [(frame - frames[0]) / fps for frame in frames]
    try:
        stored = [struct.unpack('<f', struct.pack('<f', t))[0] for t in times]
    except (OverflowError, struct.error) as exc:
        raise ValueError('Clip time exceeds the finite float32 range') from exc
    if not all(math.isfinite(t) for t in stored):
        raise ValueError('Clip time exceeds the finite float32 range')
    if any(b <= a for a, b in zip(stored, stored[1:])):
        raise ValueError('Sample times coincide in Resonite float32 precision; increase Sample Step or change the range')
    return times


def model_signature(nodes):
    return digest([{'path': node['targetPathSegments'], 'rest': node['restTRS']} for node in nodes])


def load_reference_bundle(directory):
    """Verify a previous model bundle without executing or changing anything."""
    directory = Path(directory).expanduser().absolute()
    try:
        report = json.loads((directory / 'export-report.json').read_text(encoding='utf-8-sig'))
        binding_bytes = (directory / 'motion.bindings.json').read_bytes()
        bindings = json.loads(binding_bytes.decode('utf-8-sig'))
        expected_hashes = report['filesSHA256']
        if not isinstance(bindings, dict) or not isinstance(expected_hashes, dict):
            raise ValueError('Existing Model report or binding manifest has the wrong structure')
        for name in ('model.fbx', 'motion.bindings.json'):
            actual = file_digest(directory / name)
            if actual != expected_hashes.get(name):
                raise ValueError(f'Existing Model checksum mismatch: {name}')
        schema = bindings['schemaVersion']
        mode = 'NATIVE_NAMES' if schema == 4 else 'RA1_PATH' if schema == 3 else None
        if (mode is None or report.get('bindingMode', mode) != mode or
                report['profile'] != PROFILE or bindings['profile'] != PROFILE):
            raise ValueError('Existing Model uses an unsupported export profile or binding format')
        nodes = bindings['nodes']
        if not isinstance(nodes, list) or not nodes:
            raise ValueError('Existing Model has no animation targets')
        paths = [tuple(n['targetPathSegments']) for n in nodes]
        sources = [tuple(n['sourceKey']) for n in nodes]
        if len(set(paths)) != len(paths) or len(set(sources)) != len(sources):
            raise ValueError('Existing Model contains duplicate target paths or source identities')
        signature = model_signature(nodes)
        if signature != report['modelSignature'] or signature != bindings['modelSignature']:
            raise ValueError('Existing Model signature does not match its recorded rest pose')
        return {'directory': directory, 'report': report, 'bindings': bindings,
                'bindingMode': mode, 'modelSignature': signature,
                'fbxSHA256': expected_hashes['model.fbx']}
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise ValueError(f'Existing Model must be a complete, readable model .resobundle: {directory.name} ({exc})') from exc


def compare_reference(reference, nodes, binding_mode):
    """A deliberately strict TRS contract; not a skin/material/shape verifier."""
    if reference['bindingMode'] != binding_mode:
        raise ValueError('Existing Model Connection mode differs; use the same Connection setting')
    expected = reference['bindings']['nodes']
    old = {tuple(n['sourceKey']): n for n in expected}
    new = {tuple(n['sourceKey']): n for n in nodes}
    if old.keys() != new.keys():
        missing, added = sorted(old.keys() - new.keys()), sorted(new.keys() - old.keys())
        raise ValueError(f'Existing Model targets differ: missing={missing[:3]}, added={added[:3]}; keep the same export scope and names')
    for key, current in new.items():
        previous = old[key]
        if current['targetPathSegments'] != previous['targetPathSegments']:
            raise ValueError(f'Existing Model hierarchy differs: {key}')
        if current['restTRS'] != previous['restTRS']:
            raise ValueError(f'Existing Model rest pose differs: {key}; export a new FBX or restore the original start transforms')
    if model_signature(nodes) != reference['modelSignature']:
        raise ValueError('Existing Model target order differs; re-export a matching model bundle')
    return {'matched': True, 'bundleName': reference['directory'].name,
            'modelSignature': reference['modelSignature'], 'fbxSHA256': reference['fbxSHA256'],
            'scope': 'Exact target names, hierarchy, order and rest TRS; geometry, skin weights, materials and shapes are not compared'}


def node_token(path):
    # Native ProtoFlux can construct this while walking child Slots, then use
    # FindAnimationTrackIndex. No JSON parser or track enumeration is required.
    # Length is .NET UTF-16 code units, including surrogate pairs for emoji.
    return "RA1;" + "".join(f"{len(part.encode('utf-16-le')) // 2}:{part}" for part in path)


def native_name(source_key):
    # Source identity, not the FBX numeric ID or enumeration order, makes this
    # stable across clips. A readable suffix is diagnostic, never the identity.
    suffix = "".join(c if c.isalnum() or c in "._-" else "_" for c in source_key[-1])[:24]
    return "RA_" + digest(source_key)[:20] + "__" + suffix


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
