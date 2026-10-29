"""Build matching CLI distributions and agent skill; restore source versions."""
import argparse
import hashlib
import json
import re
import subprocess
import sys
import tomllib

from skill_package import ROOT, build


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--build-number", type=int)
    parser.add_argument("--commit", required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9a-f]{40}", args.commit):
        raise SystemExit("Pass the exact 40-character source commit SHA")
    project = ROOT / "pyproject.toml"
    module = ROOT / "src/zoezi_cli/__init__.py"
    original = {p: p.read_text() for p in (project, module)}
    version = tomllib.loads(original[project])["project"]["version"]
    if args.build_number is not None:
        version += ".post" + str(args.build_number)
    output = ROOT / "dist"
    output.mkdir(exist_ok=True)
    if any(output.iterdir()):
        raise SystemExit("Use an empty dist directory to avoid mixing releases")
    try:
        project.write_text(re.sub(r'^version = "[^"]+"', f'version = "{version}"', original[project], flags=re.M))
        module.write_text(re.sub(r'^__version__ = "[^"]+"', f'__version__ = "{version}"', original[module], flags=re.M))
        subprocess.run([sys.executable, "-m", "build", "--outdir", str(output)], cwd=ROOT, check=True)
        manifest = build(version, args.commit, output)
        manifest["artifacts"] = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(output.iterdir()) if p.is_file()}
        (output / "release-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        checksums = "".join(hashlib.sha256(p.read_bytes()).hexdigest() + "  " + p.name + "\n" for p in sorted(output.iterdir()) if p.is_file())
        (output / "SHA256SUMS").write_text(checksums)
        print(json.dumps({"version": version, "tag": "v" + version, "commit": args.commit}))
    finally:
        for path, content in original.items():
            path.write_text(content)


if __name__ == "__main__":
    main()
