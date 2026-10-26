#!/usr/bin/env python3
"""Summarize a Xenia Canary xenia.log for Xbox Series X Dev Mode A/B testing.

Extracts, from the log lines emitted by the emulator source at HEAD 534aeb64e:
  * title id / title name / module hash   (user_module.cc:429,857; emulator.cc:1686)
  * D3D12 feature report                   (ui/d3d12/d3d12_provider.cc:480-499)
  * shader storage timings                 (gpu/d3d12/pipeline_cache.cc:522,536,1256)
  * skipped draws (pipeline not ready)     (gpu/d3d12/d3d12_command_processor.cc:2840,
                                            only with log_level=3 and log_mask=8)
  * runtime pipeline creations             (pipeline_cache.cc:2862/2866, same gating)
  * applied patches                        (patcher/patcher.cc:31)
  * error/warning counts and D3D12 device-lost / allocation failures

Usage:
  xenia_log_report.py xenia.log            # Markdown summary on stdout
  xenia_log_report.py xenia.log --json     # JSON on stdout
  xenia_log_report.py a.log b.log --json   # one record per log
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Iterable

# "i> 00001234 message" -- prefix char, '>', space, 8 hex thread id, space.
LINE_RE = re.compile(r"^(?P<level>[!wid?])> (?P<thread>[0-9A-Fa-f]{8}) (?P<msg>.*)$")

RE_TITLE_ID = re.compile(r"^\s*Title ID: ([0-9A-F]{8})\s*$")
RE_MEDIA_ID = re.compile(r"^\s*Media ID: ([0-9A-F]{8})\s*$")
RE_MODULE_HASH = re.compile(r"^Module Hash: ([0-9A-F]{16})\s*$")
RE_TITLE_NAME = re.compile(r"^Title name: (.+)$")
RE_LOADING_MODULE = re.compile(r"^Loading module (.+)$")
RE_FEATURES_HEADER = re.compile(r"^Direct3D 12 device and OS features:")
RE_FEATURE_LINE = re.compile(r"^\* (?P<key>[^:]+): (?P<value>.+)$")
RE_TRANSLATED = re.compile(r"^Translated (\d+) shaders in (\d+) ms$")
RE_PIPELINE_LOADED = re.compile(
    r"^Pipeline cache loaded: (\d+) created, (\d+) already exist, (\d+) total stored$")
RE_PIPELINE_TOOK = re.compile(r"^Pipeline creation took (\d+) milliseconds$")
RE_SKIPPED_DRAW = re.compile(r"^Skipping draw - pipeline not ready: ")
RE_CREATING_PIPELINE = re.compile(r"^Creating graphics pipeline with VS ")
RE_PATCH_APPLIED = re.compile(r"^Patcher: Applying patch for: (.+?)\(([0-9A-F]{8})\) - (.+)$")
RE_DEVICE_LOST = re.compile(r"device (lost|removed)|DXGI_ERROR_DEVICE", re.IGNORECASE)
RE_ALLOC_FAIL = re.compile(
    r"Failed to create|E_OUTOFMEMORY|out of memory|failed to alloc", re.IGNORECASE)


@dataclass
class LogReport:
    path: str
    title_id: str | None = None
    media_id: str | None = None
    title_name: str | None = None
    module_hash: str | None = None
    module_path: str | None = None
    d3d12_features: dict[str, str] = field(default_factory=dict)
    shaders_translated: int | None = None
    shaders_translate_ms: int | None = None
    pipelines_created_from_storage: int | None = None
    pipelines_already_existing: int | None = None
    pipelines_total_stored: int | None = None
    pipeline_creation_ms: int | None = None
    skipped_draws: int = 0
    runtime_pipeline_creations: int = 0
    patches_applied: list[str] = field(default_factory=list)
    errors: int = 0
    warnings: int = 0
    device_lost_lines: list[str] = field(default_factory=list)
    allocation_failure_lines: list[str] = field(default_factory=list)
    total_lines: int = 0
    gpu_debug_logging_seen: bool = False

    @property
    def rov_supported(self) -> bool | None:
        v = self.d3d12_features.get("Rasterizer-ordered views")
        return None if v is None else v.strip().lower() == "yes"

    @property
    def tiled_resources_tier(self) -> int | None:
        v = self.d3d12_features.get("Tiled resources")
        if v is None:
            return None
        m = re.search(r"tier (\d+)", v)
        return int(m.group(1)) if m else None


def parse_lines(path: str, lines: Iterable[str]) -> LogReport:
    r = LogReport(path=path)
    in_features = False
    for raw in lines:
        line = raw.rstrip("\r\n")
        r.total_lines += 1
        m = LINE_RE.match(line)
        if m:
            level, msg = m.group("level"), m.group("msg")
            in_features = False
        else:
            # Continuation line of a multi-line message (no prefix).
            level, msg = "", line
        if level == "!":
            r.errors += 1
        elif level == "w":
            r.warnings += 1

        if in_features or RE_FEATURES_HEADER.match(msg):
            if RE_FEATURES_HEADER.match(msg):
                in_features = True
                continue
            fm = RE_FEATURE_LINE.match(msg.strip())
            if fm:
                r.d3d12_features[fm.group("key").strip()] = fm.group("value").strip()
                continue
            in_features = False

        if (mm := RE_TITLE_ID.match(msg)) and r.title_id is None:
            r.title_id = mm.group(1)
        elif (mm := RE_MEDIA_ID.match(msg)) and r.media_id is None:
            r.media_id = mm.group(1)
        elif (mm := RE_MODULE_HASH.match(msg)) and r.module_hash is None:
            r.module_hash = mm.group(1)
        elif mm := RE_TITLE_NAME.match(msg):
            r.title_name = mm.group(1).strip()
        elif (mm := RE_LOADING_MODULE.match(msg)) and r.module_path is None:
            r.module_path = mm.group(1).strip()
        elif mm := RE_TRANSLATED.match(msg):
            r.shaders_translated = int(mm.group(1))
            r.shaders_translate_ms = int(mm.group(2))
        elif mm := RE_PIPELINE_LOADED.match(msg):
            r.pipelines_created_from_storage = int(mm.group(1))
            r.pipelines_already_existing = int(mm.group(2))
            r.pipelines_total_stored = int(mm.group(3))
        elif mm := RE_PIPELINE_TOOK.match(msg):
            r.pipeline_creation_ms = int(mm.group(1))
        elif RE_SKIPPED_DRAW.match(msg):
            r.skipped_draws += 1
            r.gpu_debug_logging_seen = True
        elif RE_CREATING_PIPELINE.match(msg):
            r.runtime_pipeline_creations += 1
            r.gpu_debug_logging_seen = True
        elif mm := RE_PATCH_APPLIED.match(msg):
            r.patches_applied.append(mm.group(3).strip())

        if RE_DEVICE_LOST.search(msg) and len(r.device_lost_lines) < 20:
            r.device_lost_lines.append(msg.strip())
        if RE_ALLOC_FAIL.search(msg) and len(r.allocation_failure_lines) < 20:
            r.allocation_failure_lines.append(msg.strip())
    return r


def parse_file(path: str | Path) -> LogReport:
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return parse_lines(str(path), f)


def to_dict(r: LogReport) -> dict:
    d = asdict(r)
    d["rov_supported"] = r.rov_supported
    d["tiled_resources_tier"] = r.tiled_resources_tier
    return d


def to_markdown(r: LogReport) -> str:
    def v(x):
        return "n/a" if x is None else str(x)

    out = [f"## {r.path}", ""]
    out.append(f"- Title: {v(r.title_name)} ({v(r.title_id)}), media {v(r.media_id)}")
    out.append(f"- Module: {v(r.module_path)}, hash `{v(r.module_hash)}` (use as `hash` in patch .toml)")
    out.append(f"- D3D12: ROV {v(r.rov_supported)}, tiled resources tier {v(r.tiled_resources_tier)}, "
               f"binding {v(r.d3d12_features.get('Resource binding'))}, "
               f"VA bits/resource {v(r.d3d12_features.get('Max GPU virtual address bits per resource'))}")
    out.append(f"- Shader storage: translated {v(r.shaders_translated)} shaders in {v(r.shaders_translate_ms)} ms; "
               f"pipelines created {v(r.pipelines_created_from_storage)} / stored {v(r.pipelines_total_stored)} "
               f"in {v(r.pipeline_creation_ms)} ms")
    if r.gpu_debug_logging_seen:
        out.append(f"- In-game: {r.skipped_draws} skipped draws (pipeline not ready), "
                   f"{r.runtime_pipeline_creations} runtime pipeline creations")
    else:
        out.append("- In-game pipeline stats: not available (needs log_level = 3 and log_mask = 8)")
    out.append(f"- Patches applied: {', '.join(r.patches_applied) if r.patches_applied else 'none'}")
    out.append(f"- Errors: {r.errors}, warnings: {r.warnings}, lines: {r.total_lines}")
    if r.device_lost_lines:
        out.append("- Device lost / removed lines:")
        out += [f"  - {l}" for l in r.device_lost_lines]
    if r.allocation_failure_lines:
        out.append("- Allocation failures:")
        out += [f"  - {l}" for l in r.allocation_failure_lines]
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("logs", nargs="+", help="xenia.log file(s)")
    ap.add_argument("--json", action="store_true", help="emit JSON instead of Markdown")
    args = ap.parse_args(argv)
    reports = [parse_file(p) for p in args.logs]
    if args.json:
        json.dump([to_dict(r) for r in reports], sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        for r in reports:
            sys.stdout.write(to_markdown(r))
    return 0


if __name__ == "__main__":
    sys.exit(main())
