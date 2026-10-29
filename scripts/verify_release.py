import hashlib
import json
from pathlib import Path
import zipfile

root = Path(__file__).resolve().parents[1] / "dist"
manifest = json.loads((root / "release-manifest.json").read_text())
for filename, expected in manifest["artifacts"].items():
    assert hashlib.sha256((root / filename).read_bytes()).hexdigest() == expected, filename
with zipfile.ZipFile(root / "zoezi-cli.skill") as archive:
    skill = json.loads(archive.read("zoezi-cli/skill.json"))
    assert skill["version"] == manifest["version"]
    assert skill["commit"] == manifest["commit"]
    assert archive.testzip() is None
    assert "zoezi-cli/references/commands.json" in archive.namelist()
wheel = next(root.glob("*.whl"))
with zipfile.ZipFile(wheel) as archive:
    module = archive.read("zoezi_cli/__init__.py").decode()
    assert f'__version__ = "{manifest["version"]}"' in module
assert (root / "zoezi-cli.skill").read_bytes() == (root / "zoezi-cli-skill.zip").read_bytes()
print("CLI, skill, commit provenance and checksums verified")
