#!/usr/bin/env python3
"""Validate Deimos bot files and build the searchable registry.

A bot is a ``.txt`` file under ``bots/`` whose folder location mirrors the
internal zone path it belongs to (e.g.
``bots/WizardCity/WC_Streets/WC_Golem_Tower/WC_Golem_Tower_3/my_bot.txt``).

Every bot must carry a metadata header made of ``#`` comment lines, which the
deimoslang interpreter ignores (``#`` starts a line comment):

    # @name: Golem Tower Farmer
    # @zone: WizardCity/WC_Streets/WC_Golem_Tower/WC_Golem_Tower_3
    # @author: Slackaduts
    # @format: expertmode        # or "bot"
    # @clients: 1-4
    # @description: Farms the Golem Tower boss for gear.

Generated artifacts:
    bots/<zone>/registry.json  full per-zone registry (what a client fetches)
    index.json                 slim global index for cross-zone search

Usage:
    python scripts/registry.py validate       # exit 1 on any error (CI gate)
    python scripts/registry.py build           # (re)generate all artifacts
    python scripts/registry.py build --check   # build, then fail if files drift
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BOTS_DIR = REPO_ROOT / "bots"
ZONES_FILE = REPO_ROOT / "zones.json"
INDEX_JSON = REPO_ROOT / "index.json"
ZONE_REGISTRY_NAME = "registry.json"  # per-zone file: bots/<zone>/registry.json

REQUIRED_FIELDS = ("name", "zone", "author", "format")
OPTIONAL_FIELDS = ("clients", "description")
VALID_FORMATS = ("bot", "expertmode")
# Reserved namespace for world-agnostic bots (no real world is named this).
GENERAL_WORLD = "General"

# Matches header lines like:  # @zone: WizardCity/WC_Ravenwood
HEADER_RE = re.compile(r"^\s*#\s*@(\w+)\s*:\s*(.*?)\s*$")
# @clients must be an equality/comparison statement, e.g. "== 4", ">= 1".
CLIENTS_RE = re.compile(r"^(==|!=|>=|<=|>|<)\s*\d+$")


def load_valid_zones() -> set[str]:
    if not ZONES_FILE.exists():
        raise SystemExit(f"missing {ZONES_FILE.name}; cannot validate zones")
    # utf-8-sig tolerates a stray BOM if the file was edited on Windows.
    return set(json.loads(ZONES_FILE.read_text(encoding="utf-8-sig")))


@dataclass
class Bot:
    path: Path  # absolute
    headers: dict[str, str] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    @property
    def rel(self) -> str:
        return self.path.relative_to(REPO_ROOT).as_posix()

    @property
    def folder_zone(self) -> str:
        """Zone path implied by where the file lives, e.g. bots/<zone>/file.txt."""
        return self.path.parent.relative_to(BOTS_DIR).as_posix()

    @property
    def world(self) -> str:
        return self.folder_zone.split("/", 1)[0]

    def to_entry(self) -> dict:
        return {
            "name": self.headers.get("name", ""),
            "zone": self.headers.get("zone", ""),
            "world": self.world,
            "author": self.headers.get("author", ""),
            "format": self.headers.get("format", ""),
            "clients": self.headers.get("clients", ""),
            "description": self.headers.get("description", ""),
            "path": self.rel,
        }


def parse_bot(path: Path) -> Bot:
    """Read the leading ``#`` comment block and pull out @field headers.

    ``@description`` may span multiple lines and contain Markdown: any plain
    ``#`` comment lines that follow it (until a blank line, another ``@field``,
    or the first command line) are appended as continuation. A leading
    ``###deimos_expertmode`` marker line is a comment, so it is skipped.
    """
    bot = Bot(path=path)
    in_desc = False
    desc_lines: list[str] = []
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = raw.strip()
        m = HEADER_RE.match(raw)
        if m:
            key, value = m.group(1).lower(), m.group(2)
            if key == "description":
                in_desc = True
                desc_lines = [value] if value else []
            else:
                in_desc = False
                # strip trailing inline comment on short fields: "# @x: v  # note"
                bot.headers[key] = value.split("#", 1)[0].strip()
            continue
        if in_desc:
            if stripped == "" or stripped == "#":
                break  # blank line ends the description / header block
            if stripped.startswith("#"):
                text = raw.lstrip()[1:]  # drop the leading '#'
                desc_lines.append(text[1:] if text.startswith(" ") else text)
                continue
            break  # a command line ends the block
        if stripped == "" or stripped.startswith("#"):
            continue  # skip preamble comments/blanks (e.g. ###deimos_expertmode)
        break  # first real command line ends the header block
    desc = "\n".join(desc_lines).rstrip()
    if desc:
        bot.headers["description"] = desc
    return bot


def validate_bot(bot: Bot, valid_zones: set[str]) -> None:
    for f in REQUIRED_FIELDS:
        if not bot.headers.get(f):
            bot.errors.append(f"missing required header '@{f}'")

    zone = bot.headers.get("zone", "")
    if zone:
        is_general = zone == GENERAL_WORLD or zone.startswith(GENERAL_WORLD + "/")
        if not is_general and zone not in valid_zones:
            bot.errors.append(
                f"@zone '{zone}' is not a known game zone (not in zones.json) "
                f"and is not under the reserved '{GENERAL_WORLD}' namespace"
            )
        if zone != bot.folder_zone:
            bot.errors.append(
                f"@zone '{zone}' does not match folder location '{bot.folder_zone}'"
            )

    fmt = bot.headers.get("format", "")
    if fmt and fmt not in VALID_FORMATS:
        bot.errors.append(
            f"@format '{fmt}' invalid; must be one of {', '.join(VALID_FORMATS)}"
        )

    clients = bot.headers.get("clients", "")
    if clients and not CLIENTS_RE.match(clients):
        bot.errors.append(
            f"@clients '{clients}' must be an equality/comparison statement, "
            "e.g. '== 4', '>= 1', '<= 4'"
        )


def collect_bots() -> list[Bot]:
    return [parse_bot(p) for p in sorted(BOTS_DIR.rglob("*.txt"))]


def cmd_validate() -> int:
    valid_zones = load_valid_zones()
    bots = collect_bots()
    failed = 0
    for bot in bots:
        validate_bot(bot, valid_zones)
        if bot.errors:
            failed += 1
            print(f"FAIL {bot.rel}")
            for e in bot.errors:
                print(f"     - {e}")
    print(f"\nChecked {len(bots)} bot(s); {failed} failed.")
    return 1 if failed else 0


def build_registry() -> list[dict]:
    valid_zones = load_valid_zones()
    bots = collect_bots()
    errors = 0
    entries = []
    for bot in bots:
        validate_bot(bot, valid_zones)
        if bot.errors:
            errors += 1
            print(f"FAIL {bot.rel}: {'; '.join(bot.errors)}", file=sys.stderr)
            continue
        entries.append(bot.to_entry())
    if errors:
        raise SystemExit(f"refusing to build registry: {errors} invalid bot(s)")
    entries.sort(key=lambda e: (e["world"], e["zone"], e["name"].lower()))
    return entries


def render_zone_json(zone: str, world: str, entries: list[dict]) -> str:
    """Full per-zone registry: the artifact a client fetches for its zone."""
    doc = {"zone": zone, "world": world, "count": len(entries), "bots": entries}
    return json.dumps(doc, indent=2, ensure_ascii=False) + "\n"


def render_index(entries: list[dict]) -> str:
    """Slim global index for cross-zone search (no descriptions)."""
    slim_keys = ("name", "zone", "world", "author", "format", "clients", "path")
    zones: dict[str, int] = {}
    for e in entries:
        zones[e["zone"]] = zones.get(e["zone"], 0) + 1
    doc = {
        "generated_by": "scripts/registry.py",
        "count": len(entries),
        "worlds": sorted({e["world"] for e in entries}),
        "zones": dict(sorted(zones.items())),
        "bots": [{k: e[k] for k in slim_keys} for e in entries],
    }
    return json.dumps(doc, indent=2, ensure_ascii=False) + "\n"


def generate_outputs(entries: list[dict]) -> dict[Path, str]:
    """Map every generated file path to its desired content."""
    outputs: dict[Path, str] = {
        INDEX_JSON: render_index(entries),
    }
    by_zone: dict[str, list[dict]] = {}
    for e in entries:
        by_zone.setdefault(e["zone"], []).append(e)
    for zone, zentries in by_zone.items():
        path = BOTS_DIR / zone / ZONE_REGISTRY_NAME
        outputs[path] = render_zone_json(zone, zentries[0]["world"], zentries)
    return outputs


def stale_files(outputs: dict[Path, str]) -> set[Path]:
    """Generated files that exist on disk but should no longer (moved/removed bots)."""
    expected = {p for p in outputs if p.name == ZONE_REGISTRY_NAME}
    stale = set(BOTS_DIR.rglob(ZONE_REGISTRY_NAME)) - expected
    for legacy in (REPO_ROOT / "registry.json", REPO_ROOT / "REGISTRY.md"):
        if legacy.exists():  # old single-file layout / dropped human index
            stale.add(legacy)
    return stale


def cmd_build(check: bool) -> int:
    entries = build_registry()
    outputs = generate_outputs(entries)
    stale = stale_files(outputs)

    if check:
        drift = []
        for path, content in outputs.items():
            if not path.exists() or path.read_text(encoding="utf-8") != content:
                drift.append(path.relative_to(REPO_ROOT).as_posix())
        drift += [f"{p.relative_to(REPO_ROOT).as_posix()} (stale)" for p in stale]
        if drift:
            print("registry out of date:")
            for d in sorted(drift):
                print(f"  - {d}")
            print("run: python scripts/registry.py build")
            return 1
        print(f"registry is up to date ({len(entries)} bot(s)).")
        return 0

    for path in sorted(stale):
        path.unlink()
    for path, content in outputs.items():
        path.write_text(content, encoding="utf-8")
    zone_files = sum(1 for p in outputs if p.name == ZONE_REGISTRY_NAME)
    print(
        f"Wrote index.json and {zone_files} per-zone "
        f"registry file(s) for {len(entries)} bot(s)."
    )
    if stale:
        print(f"Removed {len(stale)} stale file(s).")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Validate bots and build the registry.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("validate", help="validate all bots (CI gate)")
    b = sub.add_parser("build", help="write registry.json + REGISTRY.md")
    b.add_argument("--check", action="store_true", help="fail if generated files would change")
    args = ap.parse_args()

    if args.cmd == "validate":
        return cmd_validate()
    if args.cmd == "build":
        return cmd_build(args.check)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
