# bots
**The approved bot registry for [Deimos](https://github.com/Deimos-Wizard101/Deimos-Wizard101).**

## Structure
- All bots are located in [bots/](https://github.com/Deimos-Wizard101/bots/tree/main/bots).
- Subfolders are created to match the internal zone path of the zone the bot belongs to.

## Creating bots
Bots are `.txt` files that follow two formats:
- [Bot Syntax](https://github.com/Deimos-Wizard101/Deimos-Wizard101/wiki/Bots)
- [Expertmode Bot Syntax](https://github.com/Deimos-Wizard101/Deimos-Wizard101/wiki/Expertmode)

### Required metadata header
Every bot **must** begin with a metadata header. These are `#` comment lines,
so the Deimos interpreter ignores them — they exist purely to make the registry
searchable.

For **expert-mode** bots the `###deimos_expertmode` marker must remain the very
first line (Deimos detects it with `startswith`); put the header on the lines
just below it. Both lines are comments, so the interpreter skips them.

```
###deimos_expertmode
# @name: Golem Tower Farmer
# @zone: WizardCity/WC_Streets/WC_Golem_Tower/WC_Golem_Tower_3
# @author: YourName
# @format: expertmode
# @clients: == 4
# @description: Farms the Golem Tower boss for gear.
# Supports **Markdown** and may span several lines.
# A blank line (or the first command) ends the description.

call setup
loop {
    call farm
}
```

| Field          | Required | Notes |
| -------------- | -------- | ----- |
| `@name`        | yes      | Display name in the registry. |
| `@zone`        | yes      | Must be a real game zone (see `zones.json`) **and** match the folder the file lives in. |
| `@author`      | yes      | Bot author. |
| `@format`      | yes      | `bot` or `expertmode`. |
| `@clients`     | no       | Equality/comparison statement for the client count, e.g. `== 4`, `>= 1`, `<= 4`. |
| `@description` | no       | Summary. May span multiple `#` lines and contain Markdown; keep it last and end it with a blank line. |

Place the file at `bots/<@zone>/<your_bot>.txt`. A bot whose `@zone` is missing,
isn't a known zone, or doesn't match its folder — or whose `@clients` isn't a
comparison statement — **will be rejected by CI** and cannot be published.

### World-agnostic bots
Bots that don't belong to any particular world go under the reserved
[`bots/General/`](bots/General) namespace (no game world is named `General`).
Set `@zone: General` (or a subcategory like `@zone: General/Fishing`) and place
the file in the matching folder. `General` zones are exempt from the
`zones.json` allowlist but still must match their folder location.

## Searching / the registry
The registry is generated at three granularities so clients fetch only what
they need instead of one ever-growing file:

- **`bots/<zone>/registry.json`** — the per-zone registry. A client that knows
  the player's current zone fetches just this file (a few KB), e.g.
  `bots/Darkmoor/DM_Z04_BlackLagoon/registry.json`. Contains full per-bot
  detail, including the Markdown description.
- [`index.json`](index.json) — a slim global index (no descriptions) for
  cross-zone search by name/author/format. Also carries a `zones` map of
  zone → bot count.
- [`REGISTRY.md`](REGISTRY.md) — human-browsable, grouped by world → zone, one
  summary line per bot.

These are regenerated automatically on every push to `main`, so a newly merged
bot auto-populates the registry (and only its zone's file changes). Contributors
do **not** need to regenerate them — just add a valid bot file. To rebuild
locally:

```
python scripts/registry.py validate   # check all bots (the CI gate)
python scripts/registry.py build       # regenerate all registry artifacts
```

`zones.json` is the authoritative allowlist of valid zone paths, generated from
the game's `AccessPass.xml`.

# Licensing
- GNU GPL v3. Please see [LICENSE](https://github.com/Deimos-Wizard101/bots/blob/main/LICENSE).
