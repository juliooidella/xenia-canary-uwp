import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import gen_game_config as gen  # noqa: E402
import xenia_log_report as rep  # noqa: E402

SAMPLE_LOG = """i> 00000001 Loading module game:\\default.xex
i> 00000001 Module Hash: 8B80FF41FE0EF8F0
i> 00000001   XEX_HEADER_EXECUTION_INFO:
i> 00000001        Media ID: 7A2F4667
i> 00000001        Title ID: 5553080B
i> 00000001     Savegame ID: 5553080B
i> 00000002 Direct3D 12 device and OS features:
* Max GPU virtual address bits per resource: 40
* Non-zeroed heap creation: yes
* Pixel-shader-specified stencil reference: no
* Programmable sample positions: tier 0
* Rasterizer-ordered views: yes
* Scalar alpha blend factor: no
* Resource binding: tier 3
* Tiled resources: tier 3
* Unaligned block-compressed textures: no
i> 00000001 Title name: Tom Clancy's Splinter Cell Conviction
!> 00000003 Patcher: Applying patch for: Tom Clancy's Splinter Cell Conviction(5553080B) - Unlock FPS
i> 00000004 Translated 812 shaders in 1530 ms
i> 00000004 Pipeline cache loaded: 1200 created, 3 already exist, 1203 total stored
i> 00000004 Pipeline creation took 4210 milliseconds
d> 00000005 Skipping draw - pipeline not ready: VS 0000000000000001 mod 0000000000000000, PS 0000000000000002 mod 0000000000000000
d> 00000005 Skipping draw - pipeline not ready: VS 0000000000000001 mod 0000000000000000, PS 0000000000000002 mod 0000000000000000
d> 00000005 Creating graphics pipeline with VS 0000000000000001, PS 0000000000000002
w> 00000005 Shared memory: Failed to create the 512 MB tiled buffer
!> 00000005 D3D12 device removed (DXGI_ERROR_DEVICE_REMOVED)
"""


class LogReportTests(unittest.TestCase):
    def test_parse_sample(self):
        r = rep.parse_lines("sample", SAMPLE_LOG.splitlines(True))
        self.assertEqual(r.title_id, "5553080B")
        self.assertEqual(r.media_id, "7A2F4667")
        self.assertEqual(r.module_hash, "8B80FF41FE0EF8F0")
        self.assertEqual(r.title_name, "Tom Clancy's Splinter Cell Conviction")
        self.assertEqual(r.module_path, "game:\\default.xex")
        self.assertTrue(r.rov_supported)
        self.assertEqual(r.tiled_resources_tier, 3)
        self.assertEqual(r.d3d12_features["Resource binding"], "tier 3")
        self.assertEqual((r.shaders_translated, r.shaders_translate_ms), (812, 1530))
        self.assertEqual((r.pipelines_created_from_storage, r.pipelines_already_existing,
                          r.pipelines_total_stored), (1200, 3, 1203))
        self.assertEqual(r.pipeline_creation_ms, 4210)
        self.assertEqual(r.skipped_draws, 2)
        self.assertEqual(r.runtime_pipeline_creations, 1)
        self.assertTrue(r.gpu_debug_logging_seen)
        self.assertEqual(r.patches_applied, ["Unlock FPS"])
        self.assertEqual(r.errors, 2)
        self.assertEqual(r.warnings, 1)
        self.assertEqual(len(r.device_lost_lines), 1)
        self.assertEqual(len(r.allocation_failure_lines), 1)
        md = rep.to_markdown(r)
        self.assertIn("8B80FF41FE0EF8F0", md)
        self.assertIn("ROV True", md)
        d = rep.to_dict(r)
        json.dumps(d)  # must be serializable
        self.assertEqual(d["tiled_resources_tier"], 3)

    def test_no_gpu_debug_logging(self):
        r = rep.parse_lines("x", ["i> 00000001 Title name: Foo\n"])
        self.assertFalse(r.gpu_debug_logging_seen)
        self.assertIn("not available", rep.to_markdown(r))


class ConfigGenTests(unittest.TestCase):
    def test_toml_values(self):
        self.assertEqual(gen.toml_value(True), "true")
        self.assertEqual(gen.toml_value(2), "2")
        self.assertEqual(gen.toml_value(0.5), "0.5")
        self.assertEqual(gen.toml_value("rov"), '"rov"')
        self.assertEqual(gen.parse_value("false"), False)
        self.assertEqual(gen.parse_value("3"), 3)
        self.assertEqual(gen.parse_value("rtv"), "rtv")

    def test_generate_matrix(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td)
            index = gen.generate("5553080b", out, "measure",
                                 [("GPU", "render_target_path_d3d12", ["rtv", "rov"]),
                                  ("D3D12", "d3d12_pipeline_creation_threads", [2, 4])], [])
            self.assertEqual(len(index), 4)
            names = {e["variant"] for e in index}
            self.assertIn("rt_d3d12-rov_pipeline_creation_threads-4", names)
            p = out / "rt_d3d12-rov_pipeline_creation_threads-4" / "config" / "5553080B.config.toml"
            self.assertTrue(p.exists())
            text = p.read_text()
            self.assertIn("[GPU]", text)
            self.assertIn('render_target_path_d3d12 = "rov"', text)
            self.assertIn("d3d12_pipeline_creation_threads = 4", text)
            self.assertIn("log_level = 3", text)
            self.assertIn("log_mask = 8", text)
            # tomllib must accept the output
            import tomllib
            parsed = tomllib.loads(text)
            self.assertEqual(parsed["GPU"]["draw_resolution_scale_x"], 1)
            self.assertTrue((out / "variants.json").exists())

    def test_generate_base_only_and_set(self):
        with tempfile.TemporaryDirectory() as td:
            index = gen.generate("5553080B", Path(td), "play", [], [("GPU", "framerate_limit", 60)])
            self.assertEqual(index[0]["variant"], "base")
            text = Path(index[0]["path"]).read_text()
            self.assertIn("framerate_limit = 60", text)
            self.assertIn("log_level = 1", text)

    def test_bad_title(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(ValueError):
                gen.generate("5553", Path(td), "play", [], [])


class DevPortalTests(unittest.TestCase):
    def test_hex64(self):
        import devportal
        self.assertEqual(devportal.hex64("abc"), "YWJj")


if __name__ == "__main__":
    unittest.main()
