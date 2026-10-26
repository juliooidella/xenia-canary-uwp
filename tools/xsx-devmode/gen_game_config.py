#!/usr/bin/env python3
"""Generate per-title Xenia Canary config variants for A/B testing on Xbox Series X.

Xenia loads `<storage_root>/config/<TITLEID>.config.toml` after the module loads
(src/xenia/config.cc LoadGameConfig, called from src/xenia/emulator.cc). Keys are
grouped by cvar category: `[GPU] draw_resolution_scale_x = 1`.

The generator takes a base profile plus `--vary CATEGORY.key=v1,v2,...` options and
writes the cartesian product as:

  <out>/<variant>/config/<TITLEID>.config.toml
  <out>/variants.json           (index: variant name -> values)

Example (Splinter Cell: Conviction, 5553080B):
  gen_game_config.py --title 5553080B --out ./variants \
      --vary GPU.render_target_path_d3d12=rtv,rov \
      --vary GPU.readback_resolve=fast,none \
      --vary D3D12.d3d12_pipeline_creation_threads=2,4

Profiles: `play` (default) and `measure` (log_level = 3, log_mask = 8 so that the
"Skipping draw - pipeline not ready" lines are written). Values are parsed as
int, float, bool (true/false) or string; quote strings in TOML automatically.
"""
from __future__ import annotations

import argparse
import itertools
import json
import re
import sys
from pathlib import Path

# Baseline from the analysis plan (section 4, nivel A). All values are test
# starting points, not measured optima.
BASE_PROFILE: dict[str, dict[str, object]] = {
    "GPU": {
        "draw_resolution_scale_x": 1,
        "draw_resolution_scale_y": 1,
        "render_target_path_d3d12": "rtv",
        "readback_resolve": "fast",
        "readback_memexport": False,
        "occlusion_query": "fast",
        "async_shader_compilation": True,
        "vsync": True,
        "framerate_limit": 30,
        "texture_cache_memory_limit_soft": 256,
        "texture_cache_memory_limit_hard": 512,
    },
    "D3D12": {
        "d3d12_pipeline_creation_threads": 2,
        "d3d12_bindless": True,
    },
    "Kernel": {
        "ignore_thread_affinities": True,
    },
    "Logging": {
        "log_level": 1,
    },
}

PROFILES: dict[str, dict[str, dict[str, object]]] = {
    "play": {},
    "measure": {"Logging": {"log_level": 3, "log_mask": 8}},
}

TITLE_RE = re.compile(r"^[0-9A-Fa-f]{8}$")


def parse_value(text: str) -> object:
    t = text.strip()
    if t.lower() in ("true", "false"):
        return t.lower() == "true"
    try:
        return int(t)
    except ValueError:
        pass
    try:
        return float(t)
    except ValueError:
        pass
    return t


def toml_value(v: object) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    s = str(v).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{s}"'


def render_toml(cfg: dict[str, dict[str, object]]) -> str:
    out = []
    for cat in sorted(cfg):
        if not cfg[cat]:
            continue
        out.append(f"[{cat}]")
        for key in sorted(cfg[cat]):
            out.append(f"{key} = {toml_value(cfg[cat][key])}")
        out.append("")
    return "\n".join(out)


def merge(base: dict[str, dict[str, object]], *overlays: dict[str, dict[str, object]]):
    cfg = {cat: dict(vals) for cat, vals in base.items()}
    for ov in overlays:
        for cat, vals in ov.items():
            cfg.setdefault(cat, {}).update(vals)
    return cfg


def parse_vary(spec: str) -> tuple[str, str, list[object]]:
    if "=" not in spec or "." not in spec.split("=", 1)[0]:
        raise argparse.ArgumentTypeError(f"--vary expects CATEGORY.key=v1,v2 (got {spec!r})")
    lhs, rhs = spec.split("=", 1)
    cat, key = lhs.split(".", 1)
    values = [parse_value(v) for v in rhs.split(",") if v.strip() != ""]
    if not values:
        raise argparse.ArgumentTypeError(f"--vary {spec!r} has no values")
    return cat, key, values


def variant_name(assignment: list[tuple[str, str, object]]) -> str:
    if not assignment:
        return "base"
    parts = []
    for _cat, key, val in assignment:
        short = key.replace("d3d12_", "").replace("draw_resolution_", "").replace("render_target_path_", "rt_")
        parts.append(f"{short}-{str(val).lower()}")
    return "_".join(parts)


def generate(title: str, out: Path, profile: str, vary: list[tuple[str, str, list[object]]],
             set_: list[tuple[str, str, object]]) -> list[dict]:
    if not TITLE_RE.match(title):
        raise ValueError(f"title id must be 8 hex digits, got {title!r}")
    title = title.upper()
    base = merge(BASE_PROFILE, PROFILES[profile])
    for cat, key, val in set_:
        base.setdefault(cat, {})[key] = val
    axes = [[(cat, key, v) for v in values] for cat, key, values in vary]
    index = []
    for combo in itertools.product(*axes) if axes else [()]:
        assignment = list(combo)
        name = variant_name(assignment)
        cfg = merge(base, {})
        for cat, key, val in assignment:
            cfg.setdefault(cat, {})[key] = val
        target = out / name / "config"
        target.mkdir(parents=True, exist_ok=True)
        (target / f"{title}.config.toml").write_text(render_toml(cfg), encoding="utf-8")
        index.append({"variant": name, "values": {f"{c}.{k}": v for c, k, v in assignment},
                      "path": str(target / f"{title}.config.toml")})
    (out / "variants.json").write_text(json.dumps(index, indent=2), encoding="utf-8")
    return index


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--title", required=True, help="title id, 8 hex digits (e.g. 5553080B)")
    ap.add_argument("--out", required=True, type=Path, help="output directory")
    ap.add_argument("--profile", choices=sorted(PROFILES), default="play")
    ap.add_argument("--vary", action="append", default=[], type=parse_vary, metavar="CAT.key=v1,v2")
    ap.add_argument("--set", action="append", default=[], metavar="CAT.key=value",
                    help="override a single base value in every variant")
    args = ap.parse_args(argv)
    sets = []
    for s in args.set:
        cat, key, vals = parse_vary(s)
        if len(vals) != 1:
            ap.error(f"--set takes exactly one value: {s}")
        sets.append((cat, key, vals[0]))
    index = generate(args.title, args.out, args.profile, args.vary, sets)
    for entry in index:
        print(f"{entry['variant']}: {entry['path']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
