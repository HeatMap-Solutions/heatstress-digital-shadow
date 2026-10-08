import csv
import http.server
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bench
import probe
import report


def record(identifier="a", status="completed", seconds=10, machine="machine-a", **kw):
    return dict(
        run_id=identifier,
        scenario_id="one-tree",
        machine_id=machine,
        scenario_fingerprint="scenario-a",
        input_fingerprint="data-a",
        environment_fingerprint="environment-a",
        measurement_version="v1",
        label="baseline",
        status=status,
        request={"elapsed_seconds": seconds},
        **kw,
    )


class BenchmarkTests(unittest.TestCase):
    def test_duplicate_attempt_cannot_inflate_sample_count(self):
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            report.compare_records([record(), record()])

    def test_cross_machine_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Cross-machine"):
            report.compare_records([record(), record("b", machine="machine-b")])

    def test_changed_input_environment_and_scenario_are_flagged(self):
        first = record()
        second = record("b")
        for key in (
            "input_fingerprint",
            "environment_fingerprint",
            "scenario_fingerprint",
        ):
            second[key] = "changed"
        _, warnings = report.compare_records([first, second])
        self.assertEqual(len(warnings), 3)

    def test_failed_duration_not_in_successful_statistics(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = []
            for r in (
                record(),
                record("b", seconds=20),
                record("c", status="failed", seconds=0.01),
            ):
                p = root / r["run_id"]
                p.mkdir()
                probe.save(p / "run.json", r)
                paths.append(p)
            out = report.comparison_report(paths, root / "comparison")
            page = out.read_text()
            self.assertIn("15.00 s", page)
            self.assertIn("2 / 3", page)
            self.assertIn("0.01 s", page)  # failure remains visible in attempt table
            with (root / "comparison/comparison.csv").open() as f:
                self.assertEqual(len(list(csv.DictReader(f))), 3)

    def test_report_escapes_user_controlled_labels(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            r = record()
            r["label"] = "<script>alert(1)</script>"
            p = root / "run"
            p.mkdir()
            probe.save(p / "run.json", r)
            html = report.comparison_report([p], root / "out").read_text()
            self.assertNotIn("<script>", html)
            self.assertIn("&lt;script&gt;", html)

    def test_artifact_collection_preserves_sources_and_rewritten_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            work = root / ".work/data"
            work.mkdir(parents=True)
            (root / "outputs").mkdir()
            source = work / "input.tif"
            source.write_bytes(b"original")
            before = {
                "input.tif": {
                    "sha256": bench.file_hash(source),
                    "mtime_ns": source.stat().st_mtime_ns,
                }
            }
            (work / "new.tif").write_bytes(b"generated")
            artifacts = bench.collect(work, root, before, save_maps=True)
            self.assertEqual([a["path"] for a in artifacts], ["outputs/new.tif"])
            self.assertEqual(source.read_bytes(), b"original")
            source.write_bytes(b"changed")
            bench.collect(work, root, before, save_maps=True)
            self.assertEqual((root / "outputs/input.tif").read_bytes(), b"changed")

    def test_lightweight_collection_inventories_maps_without_saving_them(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            work = root / ".work/data"
            work.mkdir(parents=True)
            (work / "map.tif").write_bytes(b"map")
            (work / "points.gpkg").write_bytes(b"points")
            (work / "settings.json").write_text('{"value": 1}')
            (work / "large.log").write_bytes(b"x" * (1024 * 1024 + 1))
            artifacts = bench.collect(work, root, {})
            retained = {a["path"] for a in artifacts if a["retained"]}
            self.assertEqual(retained, {"outputs/settings.json"})
            self.assertFalse((root / "outputs/map.tif").exists())
            self.assertFalse((root / "outputs/points.gpkg").exists())
            self.assertFalse((root / "outputs/large.log").exists())
            self.assertEqual((work / "map.tif").read_bytes(), b"map")
            self.assertEqual(len(artifacts), 4)
            self.assertTrue(all(a["sha256"] for a in artifacts))

    def test_missing_retained_maps_reports_unavailable_without_docker(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = root / "run"
            run.mkdir()
            probe.save(run / "run.json", record())
            probe.save(run / "validation.json", {"checks": [
                {"path": "final.tif", "ok": True, "raster": {"size": [1, 1]}}
            ]})
            destination = root / "comparison"
            report.comparison_report([run], destination)
            with patch.object(bench, "docker") as docker:
                bench.compare_rasters([run], destination)
            docker.assert_not_called()
            result = json.loads((destination / "raster_differences.json").read_text())[0]
            self.assertEqual(result["status"], "unavailable")
            self.assertIn("--save-maps", result["reason"])

    def test_validation_excludes_unchanged_source_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "old.json").write_text('{"old": true}')
            result = probe.validate(root, ["old.json"], generated_paths=set())
            self.assertFalse(result["ok"])
            self.assertIn("No newly generated", result["checks"][0]["reason"])

    def test_input_identity_detects_content_change_and_missing_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            p = root / "x"
            p.write_text("before")
            first = bench.inventory_inputs(root, ["x", "absent"])
            p.write_text("after")
            second = bench.inventory_inputs(root, ["x", "absent"])
            self.assertNotEqual(bench.fingerprint(first), bench.fingerprint(second))
            self.assertFalse(first["absent"]["exists"])

    def test_adjacent_gdal_metadata_is_included(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "x.tif").write_text("x")
            (root / "x.tif.aux.xml").write_text("metadata")
            self.assertEqual(
                bench.input_paths(root, ["x.tif"]), ["x.tif", "x.tif.aux.xml"]
            )

    def test_missing_inputs_keep_failed_evidence_without_starting_containers(self):
        from types import SimpleNamespace

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = root / "data"
            data.mkdir()
            definition = {
                "id": "missing",
                "title": "Missing input",
                "description": "Test",
                "inputs": ["missing.tif"],
                "boundary": "Request",
                "excludes": "Setup",
                "expected_outputs": ["out.tif"],
            }
            args = SimpleNamespace(data=data, keep_work=False, save_maps=False, timeout=1)
            code = {"application_source_fingerprint": "x"}
            with patch.object(bench, "BENCH", root / "benchmark"), patch.object(
                bench, "runtime", return_value={}
            ), patch.object(bench, "code_record", return_value=code), patch.object(
                bench, "machine_id", return_value="test"
            ), patch.object(
                bench, "docker"
            ) as docker:
                path, run = bench.execute(definition, "test", args)
            self.assertEqual(run["status"], "blocked")
            docker.assert_not_called()
            self.assertTrue((path / "report.html").is_file())
            self.assertTrue((path / "run.json").is_file())
            self.assertEqual(list(data.iterdir()), [])

    def test_interruption_preserves_record(self):
        from types import SimpleNamespace

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = root / "data"
            data.mkdir()
            definition = {
                "id": "interrupt",
                "title": "Interrupt",
                "description": "Test",
                "inputs": [],
                "boundary": "Request",
                "excludes": "Setup",
                "expected_outputs": ["out.tif"],
            }
            args = SimpleNamespace(data=data, keep_work=False, save_maps=False, timeout=1)
            with patch.object(bench, "BENCH", root / "benchmark"), patch.object(
                bench, "code_record", side_effect=KeyboardInterrupt
            ), patch.object(bench, "machine_id", return_value="test"):
                path, run = bench.execute(definition, "test", args)
            self.assertEqual(run["status"], "interrupted")
            self.assertTrue((path / "report.html").exists())

    def test_unsafe_input_paths_are_rejected(self):
        for path in ("../data", "/etc/passwd"):
            with self.assertRaises(ValueError):
                bench.safe_relative(path)

    def test_symlink_input_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "actual").write_text("x")
            (root / "link").symlink_to(root / "actual")
            with self.assertRaises(ValueError):
                bench.inventory_inputs(root, ["link"])

    def test_resource_units_and_cpu_values(self):
        self.assertEqual(bench.byte_count("1.5GiB"), int(1.5 * 1024**3))
        self.assertEqual(bench.byte_count("12.5MB"), 12500000)
        with self.assertRaises(ValueError):
            bench.byte_count("unavailable")

    def test_docker_terminal_escapes_and_unavailable_samples(self):
        sample = "\x1b[2J\x1b[H" + json.dumps(
            {"Name": "qgis", "CPUPerc": "240.5%", "MemUsage": "1.5GiB / 8GiB"}
        )
        self.assertEqual(bench.stats_row(sample), ("qgis", 240.5, int(1.5 * 1024**3)))
        self.assertIsNone(
            bench.stats_row(
                json.dumps({"Name": "qgis", "CPUPerc": "--", "MemUsage": "-- / --"})
            )
        )

    def test_missing_expected_output_is_not_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = probe.validate(tmp, ["**/*_filled.tif"])
            self.assertFalse(result["ok"])

    def test_scenarios_have_explicit_boundaries(self):
        scenarios = bench.definitions()
        for name, s in scenarios.items():
            self.assertEqual(s["id"], name)
            for field in ("inputs", "boundary", "excludes", "expected_outputs"):
                self.assertTrue(s[field])
        self.assertEqual(len(scenarios["one-tree"]["payload"]["points"]), 1)
        self.assertEqual(len(scenarios["ten-trees"]["payload"]["points"]), 10)
        self.assertEqual(
            scenarios["one-tree"]["payload"]["points"][0],
            scenarios["ten-trees"]["payload"]["points"][0],
        )


class HTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == "/fail":
                    self.send_response(500)
                    body = b'{"detail":"u_1_2"}'
                elif self.path == "/false-success":
                    self.send_response(200)
                    body = b'{"status":"failed"}'
                else:
                    self.send_response(200)
                    body = b'{"status":"success"}'
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def test_real_http_response_classification_and_timing(self):
        with tempfile.TemporaryDirectory() as tmp:
            for path, status in [
                ("/ok", "completed"),
                ("/fail", "failed"),
                ("/false-success", "failed"),
            ]:
                output = Path(tmp) / "response.json"
                probe.request(
                    f"http://127.0.0.1:{self.server.server_port}" + path,
                    {"method": "GET"},
                    output,
                    3,
                )
                result = json.loads(output.read_text())
                self.assertEqual(result["status"], status)
                self.assertGreaterEqual(result["elapsed_seconds"], 0)
            self.assertIn("body", result)


if __name__ == "__main__":
    unittest.main()
