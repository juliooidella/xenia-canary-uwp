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
| `devportal.py` | Xbox Device Portal client: list and install packages, restart the console, push/pull files in `LocalState`, launch/stop the app, and `run-variant` (push config, restart, wait, pull log). |
| `xiso_extract.py` | Unpacks an Xbox 360 disc image (plain XISO or XGD1/2/3) into a folder with `default.xex`, which is the only disc layout the UWP frontend lists from a USB drive. Pure Python, `--dry-run` to validate. |
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

## Building and installing the .appx from CI

The workflow `.github/workflows/UWP_appx.yml` builds a signed test package on
GitHub Actions (see `docs/building-uwp.md`, "Continuous integration"). A full
build takes tens of minutes; the job limit is 5 h.

```sh
# 1. trigger: push to uwp-ci (or xsx-devmode) on the fork, or dispatch manually
git push fork uwp-ci
gh workflow run UWP_appx.yml -R <owner>/xenia-canary-uwp --ref uwp-ci   # needs the workflow on the default branch
gh run list -R <owner>/xenia-canary-uwp --workflow UWP_appx.yml --limit 3
gh run watch <run-id> -R <owner>/xenia-canary-uwp

# 2. download the package and its certificate
gh run download <run-id> -R <owner>/xenia-canary-uwp -n xenia-uwp-appx-<version>-<sha> -D ./appx

# 3. install through the Device Portal, restart, start the app, measure
python3 devportal.py --host <ip> --user <u> --password <p> install \
    ./appx/build/uwp/release/Xenia-Netplay-<version>.appx --cert ./appx/ci-cert/Xenia-Netplay-<version>-CI.cer
# on the console: Dev Home > Xenia Netplay > View details > App type = Game
python3 devportal.py ... restart
python3 devportal.py ... find xenia     # new PackageFullName ends in _<version>_x64__1qrqqmyv3f0wj
python3 devportal.py ... launch --package <PackageFullName> --appid <AppId>
python3 memwatch.py --host <ip> --user <u> --password <p> --out runs/ci-boot.csv --duration 180
```

Every install registers the package as an App, so set it back to Game on
the console after each install. As an App, Xenia logs
`UWP app memory usage: ... limit: 1024 MiB`, fails with `XmaContext ...:
Couldn't allocate context` and exits about 15 s after launch. As a Game the
limit is 5120 MiB. The portal's `systemperf` counters follow the same
partition: memwatch reports a 3168 MiB commit limit for an App and 8288 MiB
for a Game.

`restart` sends an empty POST body because the portal answers HTTP 411
without a Content-Length. The console reboots in under 20 s, often before
it answers, so a read timeout counts as success.

On a fork, open the repository's Actions tab once and enable workflows;
until then GitHub registers no workflows and a push starts no runs.

`install` uploads the package and any `--cert` or `--dependency` files in one
multipart request (`POST /api/app/packagemanager/package`). It then polls
`/api/app/packagemanager/state` until the portal returns the deployment result,
and fails if `Success` is false. The CI package keeps the identity name and
publisher `CN=DanP142` and uses a higher version, so it updates the installed
package in place. On 2026-10-26 the console accepted 0.6.0.3 over 0.6.0.2 in
12 s, even though the CI certificate differs from the original one, and kept
`LocalState`. If an update is ever rejected, uninstall the old package first.
Uninstalling deletes its `LocalState` (configs, logs, profiles), so pull what
you need first.
