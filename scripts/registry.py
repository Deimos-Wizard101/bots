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

``@zone`` can be a single zone or a comma-separated list of zones (e.g.
``WizardCity/WC_Streets, Celestia/CL_Hub``). The first zone defines the primary
folder location where the file lives on disk. Any additional zones make the
bot discoverable from those zones without creating extra copies of the bot file.
Zones may name broad umbrella prefixes (e.g. ``WizardCity/WC_Streets`` or
``WizardCity``) as long as they are valid ancestors in zones.json.

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
    """Zone names from zones.json, plus every ancestor prefix of each.

    A leaf like "WizardCity/WC_Streets/WC_Golem_Tower" also makes
    "WizardCity" and "WizardCity/WC_Streets" valid, since @zone entries are
    allowed to name a broad umbrella zone instead of one specific instance
    (e.g. a bot meant to work throughout all of WC_Streets' sigils).
    """
    if not ZONES_FILE.exists():
        raise SystemExit(f"missing {ZONES_FILE.name}; cannot validate zones")
    # utf-8-sig tolerates a stray BOM if the file was edited on Windows.
    zones = set(json.loads(ZONES_FILE.read_text(encoding="utf-8-sig")))
    prefixes: set[str] = set()
    for z in zones:
        parts = z.split("/")
        for i in range(1, len(parts)):
            prefixes.add("/".join(parts[:i]))
    return zones | prefixes


def is_known_zone(zone: str, valid_zones: set[str]) -> bool:
    """True if `zone` is a real (or umbrella-prefix) game zone, or under General."""
    return zone == GENERAL_WORLD or zone.startswith(GENERAL_WORLD + "/") or zone in valid_zones


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
    def zones(self) -> list[str]:
        """Parsed comma-separated @zone entries: first is primary/folder zone."""
        raw = self.headers.get("zone", "")
        return [z.strip() for z in raw.split(",") if z.strip()]

    @property
    def primary_zone(self) -> str:
        return self.zones[0] if self.zones else ""

    @property
    def target_rel(self) -> str:
        """Repo-relative path the bot belongs at post-relocation."""
        if self.primary_zone:
            return f"bots/{self.primary_zone}/{self.path.name}"
        return self.rel

    @property
    def world(self) -> str:
        target_zone = self.primary_zone or self.folder_zone
        return target_zone.split("/", 1)[0]

    def to_entry(self) -> dict:
        return {
            "name": self.headers.get("name", ""),
            "zone": ", ".join(self.zones),
            "world": self.world,
            "author": self.headers.get("author", ""),
            "format": self.headers.get("format", ""),
            "clients": self.headers.get("clients", ""),
            "description": self.headers.get("description", ""),
            "path": self.target_rel,
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

    zones = bot.zones
    if not zones:
        return

    for z in zones:
        if not is_known_zone(z, valid_zones):
            bot.errors.append(
                f"@zone '{z}' is not a known game zone or umbrella prefix of "
                f"one (not in zones.json) and is not under the reserved "
                f"'{GENERAL_WORLD}' namespace"
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

    # Pre-merge validation: fail if relocating would collide with an existing file
    if bot.primary_zone and bot.folder_zone != bot.primary_zone:
        dest_path = BOTS_DIR / bot.primary_zone / bot.path.name
        if dest_path.exists() and dest_path.resolve() != bot.path.resolve():
            bot.errors.append(
                f"cannot relocate to primary zone: destination 'bots/{bot.primary_zone}/{bot.path.name}' already exists"
            )


def collect_bots() -> list[Bot]:
    return [parse_bot(p) for p in sorted(BOTS_DIR.rglob("*.txt"))]


def resolve_bot_files(raw_paths: list[str]) -> tuple[list[Path], list[str]]:
    """Turn CLI/file-list path strings into absolute bot paths under bots/.

    Returns (paths, warnings). Non-existent, non-.txt, or outside-bots/ entries
    are skipped with a warning rather than failing; a PR may list a file that
    was renamed away, and only real bot introductions should gate.
    """
    paths: list[Path] = []
    warnings: list[str] = []
    for raw in raw_paths:
        raw = raw.strip()
        if not raw:
            continue
        p = Path(raw)
        if not p.is_absolute():
            p = REPO_ROOT / p
        p = p.resolve()
        if p.suffix != ".txt" or BOTS_DIR not in p.parents:
            warnings.append(f"skipping '{raw}': not a .txt file under bots/")
            continue
        if not p.exists():
            warnings.append(f"skipping '{raw}': file does not exist")
            continue
        paths.append(p)
    return paths, warnings


def prune_empty_dirs(root: Path) -> None:
    """Remove empty directories bottom-up under root."""
    for d in sorted(root.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if d.is_dir() and not any(d.iterdir()):
            try:
                d.rmdir()
            except OSError:
                pass


def relocate_bots(bots: list[Bot], dry_run: bool = False) -> list[str]:
    """Ensure every bot file is stored in its primary zone folder.

    When an edit changes a bot's primary @zone, the file is automatically moved
    to the new primary zone folder on build and any old empty folder is pruned.
    """
    drift: list[str] = []
    for bot in bots:
        if not bot.primary_zone or bot.folder_zone == bot.primary_zone:
            continue
        dest_dir = BOTS_DIR / bot.primary_zone
        dest_path = dest_dir / bot.path.name
        if dest_path.exists() and dest_path.resolve() != bot.path.resolve():
            continue
        if dry_run:
            drift.append(f"{bot.rel} (needs relocation to {bot.target_rel})")
        else:
            dest_dir.mkdir(parents=True, exist_ok=True)
            old_path = bot.path
            old_path.rename(dest_path)
            bot.path = dest_path
    if not dry_run:
        prune_empty_dirs(BOTS_DIR)
    return drift


def cmd_validate(only: list[Path] | None = None) -> int:
    valid_zones = load_valid_zones()
    bots = [parse_bot(p) for p in only] if only is not None else collect_bots()
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


def build_registry(dry_run: bool = False) -> tuple[list[dict], list[str]]:
    valid_zones = load_valid_zones()
    bots = collect_bots()
    errors = 0
    for bot in bots:
        validate_bot(bot, valid_zones)
        if bot.errors:
            errors += 1
            print(f"FAIL {bot.rel}: {'; '.join(bot.errors)}", file=sys.stderr)
    if errors:
        raise SystemExit(f"refusing to build registry: {errors} invalid bot(s)")
    drift = relocate_bots(bots, dry_run=dry_run)
    entries = [bot.to_entry() for bot in bots]
    entries.sort(key=lambda e: (e["world"], e["zone"], e["name"].lower()))
    return entries, drift


def render_zone_json(zone: str, world: str, entries: list[dict]) -> str:
    """Full per-zone registry: the artifact a client fetches for its zone."""
    doc = {"zone": zone, "world": world, "count": len(entries), "bots": entries}
    return json.dumps(doc, indent=2, ensure_ascii=False) + "\n"


def render_index(entries: list[dict]) -> str:
    """Slim global index for cross-zone search (no descriptions)."""
    slim_keys = ("name", "zone", "world", "author", "format", "clients", "path")
    zones: dict[str, int] = {}
    for e in entries:
        for z in (s.strip() for s in e["zone"].split(",") if s.strip()):
            zones[z] = zones.get(z, 0) + 1
    doc = {
        "generated_by": "scripts/registry.py",
        "count": len(entries),
        "worlds": sorted({e["world"] for e in entries}),
        "zones": dict(sorted(zones.items())),
        "bots": [{k: e[k] for k in slim_keys} for e in entries],
    }
    return json.dumps(doc, indent=2, ensure_ascii=False) + "\n"


def generate_outputs(entries: list[dict]) -> dict[Path, str]:
    """Map every generated file path to its desired content.

    Each bot's entry is written into its primary zone's registry.json, plus
    a copy into every additional zone listed in its comma-separated @zone header
    (deduped). This is how a bot becomes discoverable from extra zones without
    a second copy of the bot file existing anywhere in the repo.
    """
    outputs: dict[Path, str] = {
        INDEX_JSON: render_index(entries),
    }
    by_zone: dict[str, list[dict]] = {}
    for e in entries:
        seen = set()
        for z in (s.strip() for s in e.get("zone", "").split(",") if s.strip()):
            if z in seen:
                continue
            seen.add(z)
            by_zone.setdefault(z, []).append(e)
    for zone, zentries in by_zone.items():
        world = zone.split("/", 1)[0]
        path = BOTS_DIR / zone / ZONE_REGISTRY_NAME
        outputs[path] = render_zone_json(zone, world, zentries)
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
    entries, relocation_drift = build_registry(dry_run=check)
    outputs = generate_outputs(entries)
    stale = stale_files(outputs)

    if check:
        drift = list(relocation_drift)
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
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    prune_empty_dirs(BOTS_DIR)
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
    v = sub.add_parser("validate", help="validate bots (CI gate)")
    v.add_argument(
        "--files",
        nargs="*",
        default=[],
        metavar="PATH",
        help="validate only these bot files (default: every bot in the repo)",
    )
    v.add_argument(
        "--files-from",
        metavar="PATH",
        help="read a newline-delimited list of bot files to validate",
    )
    b = sub.add_parser("build", help="write index.json and per-zone registry files")
    b.add_argument(
        "--check", action="store_true", help="fail if generated files would change"
    )
    args = ap.parse_args()

    if args.cmd == "validate":
        raw = list(args.files)
        if args.files_from:
            raw += Path(args.files_from).read_text(encoding="utf-8").splitlines()
        if raw:
            only, warnings = resolve_bot_files(raw)
            for w in warnings:
                print(w)
            if not only:
                print("No bot files to validate.")
                return 0
            return cmd_validate(only)
        return cmd_validate()
    if args.cmd == "build":
        return cmd_build(args.check)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
