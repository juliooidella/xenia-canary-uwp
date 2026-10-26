# xsx-devmode: measurement tooling for Xenia Canary on Xbox Series X (Dev Mode)

Stage 0 of the Series X plan: Python-only tools (3.11+, stdlib only) to run
config A/B tests on the console and turn `xenia.log` into comparable numbers.
The tools do not change the emulator; they target the community UWP port
(`danprice142/xenia-canary-uwp`, branch `Blades-UWP`, or `SirMangler/xenia`),
which keeps its files in the package `LocalState` folder.

## Scripts

| Script | Purpose |
|---|---|
| `gen_game_config.py` | Writes `config/<TITLEID>.config.toml` variants (cartesian product of `--vary` axes) from a baseline profile. Profile `measure` adds `log_level = 3`, `log_mask = 8` so skipped-draw lines are logged. |
| `xenia_log_report.py` | Summarizes one or more `xenia.log` files: title id, module hash (for patch files), D3D12 feature report (ROV, tiled resources tier), shader storage timings, skipped draws, applied patches, device-lost and allocation failures. Markdown or `--json`. |
| `devportal.py` | Xbox Device Portal client: list packages, push/pull files in `LocalState`, launch/stop the app, and `run-variant` (push config, restart, wait, pull log). |
| `memwatch.py` | Polls the portal's system commit charge and the `xeniacanary.exe` working set into a CSV (`--until-exit`, `--duration`, `--summary`). The system commit is the counter that fills up on Series S. |

## Documents

| File | Purpose |
|---|---|
| `PLAN-memory-series-s.md` | Measured memory ceiling on Xbox Series S (frontend alone commits ~6 GB of ~8.3 GB), code locations, and the staged plan to fix it (section size, per-heap sections, lazy physical commit, JIT reservations). |
| `GAMES-series-s.md` | XBLA/disc titles from the Canary compatibility tracker that fit the Series S memory headroom, grouped by profile, with per-title config notes. |

## Workflow

```sh
cd tools/xsx-devmode
python3 -m unittest discover -s tests          # self-test

# 1. generate the A/B matrix for Splinter Cell: Conviction (title id 5553080B)
python3 gen_game_config.py --title 5553080B --out ./variants --profile measure \
    --vary GPU.render_target_path_d3d12=rtv,rov \
    --vary GPU.readback_resolve=fast,none \
    --vary D3D12.d3d12_pipeline_creation_threads=2,4

# 2. find the package and app id on the console
python3 devportal.py --host <ip> --user <u> --password <p> find xenia

# 3. run one variant (start the game from the Xenia UI during the wait window)
python3 devportal.py --host <ip> --user <u> --password <p> run-variant \
    --package <PackageFullName> --appid <AppId> --title 5553080B \
    --config ./variants/base/config/5553080B.config.toml --wait 300 --out ./runs/base.log

# 4. compare
python3 xenia_log_report.py ./runs/*.log
```

## What the log lines mean (Xenia Canary HEAD 534aeb64e)

- `Direct3D 12 device and OS features:` + `* Rasterizer-ordered views: yes|no`,
  `* Tiled resources: tier N` — `src/xenia/ui/d3d12/d3d12_provider.cc:480-499`.
  ROV "no" means `render_target_path_d3d12 = "rov"` silently falls back to RTV;
  tiled resources "tier 0" means resolution scaling is forced to 1x1.
- `Translated N shaders in T ms`, `Pipeline cache loaded: ...`,
  `Pipeline creation took T milliseconds` — `src/xenia/gpu/d3d12/pipeline_cache.cc:522-536,1256`.
  Startup cost of the shader storage; the pipeline-library change in the plan targets this.
- `Skipping draw - pipeline not ready` — `src/xenia/gpu/d3d12/d3d12_command_processor.cc:2840`.
  Debug-level GPU log; needs `log_level = 3` and `log_mask = 8`. Counts draws dropped
  while `async_shader_compilation` is still building a pipeline.
- `Module Hash: XXXXXXXXXXXXXXXX` — `src/xenia/kernel/user_module.cc:429`; this is the
  `hash` value a `patches/<TITLEID> - name.patch.toml` file must declare.
- `Patcher: Applying patch for: ...` — `src/xenia/patcher/patcher.cc:31`.

## Notes

- The Device Portal uses a self-signed certificate and CSRF tokens; the client
  disables certificate verification and performs the token handshake.
- `run-variant` cannot start the game inside Xenia; use the port's auto-launch
  option or start it manually during the wait window.
- Keep a "play" profile (`log_level = 1`) for normal use; debug GPU logging is
  heavy on the UWP file API.
