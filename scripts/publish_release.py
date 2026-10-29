"""Publish build assets without requiring account credentials or custom CI secrets."""
import json
import os
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
manifest = json.loads((root / "dist/release-manifest.json").read_text())
repository = os.environ["GITHUB_REPOSITORY"]
sha = os.environ["GITHUB_SHA"]
assert manifest["commit"] == sha
tag = "v" + manifest["version"]
body = f"""CLI and agent skill built together from `{sha}` on `master`.

Install the CLI wheel from the assets below with `python -m pip install <wheel>`.
Install the matching `zoezi-cli.skill` (or `zoezi-cli-skill.zip`) in your agent.
The skill includes its version, pinned source commit and generated CLI command contract.

Assets include the wheel, source distribution, skill archives, provenance manifest and SHA256 checksums.
No Zoezi credentials are used by CI. See README.md for setup and unattended authentication.
"""
assets = [str(p) for p in sorted((root / "dist").iterdir()) if p.is_file()]
existing = subprocess.run(["gh", "release", "view", tag, "--repo", repository, "--json", "targetCommitish"], capture_output=True, text=True)
if existing.returncode == 0:
    reference = json.loads(subprocess.check_output(["gh", "api", f"repos/{repository}/git/ref/tags/{tag}"], text=True))["object"]
    for _ in range(5):
        if reference["type"] != "tag":
            break
        reference = json.loads(subprocess.check_output(["gh", "api", f"repos/{repository}/git/tags/{reference['sha']}"], text=True))["object"]
    assert reference["type"] == "commit" and reference["sha"] == sha, "Existing release tag targets another commit"
    subprocess.run(["gh", "release", "upload", tag, "--repo", repository, "--clobber", *assets], check=True)
else:
    head = subprocess.check_output(["gh", "api", f"repos/{repository}/git/ref/heads/master", "--jq", ".object.sha"], text=True).strip()
    with tempfile.NamedTemporaryFile("w", suffix=".md") as notes:
        notes.write(body)
        notes.flush()
        subprocess.run(["gh", "release", "create", tag, "--repo", repository, "--target", sha,
                        "--title", tag, "--notes-file", notes.name, "--latest=" + ("true" if head == sha else "false"), *assets], check=True)
