"""Validate and build a deterministic, versioned agent skill archive."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
import zipfile

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from zoezi_cli.cli import parser  # noqa: E402


def command_manifest():
    def visit(node, path):
        result = []
        subcommands = []
        options = []
        for action in node._actions:
            if isinstance(action, argparse._SubParsersAction):
                seen = set()
                for name, child in action.choices.items():
                    if id(child) not in seen:
                        seen.add(id(child))
                        subcommands.append((name, child))
            elif action.option_strings:
                options.append({"flags": action.option_strings, "required": action.required,
                                "nargs": action.nargs, "help": action.help})
        result.append({"command": " ".join(path), "options": options})
        for name, child in subcommands:
            result.extend(visit(child, path + [name]))
        return result
    return {"commands": visit(parser(), ["zoezi"])}


def validate(source=ROOT / ".agent"):
    text = (source / "SKILL.md").read_text()
    if not text.startswith("---\n"):
        raise ValueError("Missing YAML frontmatter")
    metadata = yaml.safe_load(text.split("---", 2)[1])
    if set(metadata) != {"name", "description"} or metadata["name"] != "zoezi-cli":
        raise ValueError("Skill must have exactly name and description")
    if not isinstance(metadata["description"], str) or len(text.splitlines()) > 500 or "TODO" in text:
        raise ValueError("Incomplete or oversized skill")
    for link in re.findall(r"\]\(([^)]+)\)", text):
        if "://" not in link and not (source / link).is_file():
            raise ValueError("Missing skill reference: " + link)
    ui = yaml.safe_load((source / "agents/openai.yaml").read_text())
    if "$zoezi-cli" not in ui["interface"]["default_prompt"]:
        raise ValueError("UI default prompt must reference the skill")
    # Check the actual documented executable examples against argparse.
    for line in text.splitlines():
        if line.startswith("    zoezi "):
            import shlex

            example = line.strip().replace("<workout_id>", "101")
            parser().parse_args(shlex.split(example)[1:])
    return metadata


def build(version, commit, output=ROOT / "dist"):
    validate()
    output.mkdir(parents=True, exist_ok=True)
    manifest = {"name": "zoezi-cli", "version": version, "commit": commit,
                "repository": "https://github.com/mar-schmidt/zoezi-cli",
                "cli_install": "git+https://github.com/mar-schmidt/zoezi-cli.git@" + commit,
                "entrypoint": "zoezi", "python": ">=3.11"}
    files = {str(p.relative_to(ROOT / ".agent")): p.read_bytes() for p in (ROOT / ".agent").rglob("*") if p.is_file()}
    files["skill.json"] = json.dumps(manifest, indent=2).encode() + b"\n"
    files["references/commands.json"] = json.dumps(command_manifest(), indent=2).encode() + b"\n"
    archive = output / "zoezi-cli.skill"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as package:
        for name, data in sorted(files.items()):
            info = zipfile.ZipInfo("zoezi-cli/" + name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            package.writestr(info, data)
    (output / "zoezi-cli-skill.zip").write_bytes(archive.read_bytes())
    return manifest


if __name__ == "__main__":
    args = argparse.ArgumentParser()
    args.add_argument("--version", default="0.1.0")
    args.add_argument("--commit", default="development")
    args.add_argument("--check", action="store_true")
    options = args.parse_args()
    validate() if options.check else build(options.version, options.commit)
    print("Skill validated" if options.check else "Skill packaged")
