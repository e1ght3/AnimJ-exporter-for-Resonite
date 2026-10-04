"""Rebuild the legacy Blender addon ZIP using only Python's standard library."""
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 e1ght3
from pathlib import Path
import zipfile

root = Path(__file__).resolve().parents[1]
target = root / 'downloads/animj_exporter_for_resonite-0.3.1-preview.zip'
target.parent.mkdir(exist_ok=True)
files = {p.name: p for p in (root / 'addon/resoanimation_exporter').glob('*.py')}
files.update({name: root / name for name in ['LICENSE', 'TERMS.ja.md']})
files.update({name: root / 'docs' / name for name in ['USER-GUIDE.ja.md', 'LIMITATIONS.ja.md']})
with zipfile.ZipFile(target, 'w') as archive:
    for name, source in sorted(files.items()):
        entry = zipfile.ZipInfo('resoanimation_exporter/' + name, (2026, 10, 4, 0, 0, 0))
        entry.compress_type = zipfile.ZIP_DEFLATED
        entry.external_attr = 0o100644 << 16
        archive.writestr(entry, source.read_bytes())
print(target)
