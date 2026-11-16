"""Offline integration checks for YAML defaults, shared paths, and CLI overrides."""

import csv
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

import yaml


PROJECT_DIR = Path(__file__).resolve().parents[1]


class YamlConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="cran-config-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.project = self.root / "project"
        self.project.mkdir()
        for name in (
            "settings.py", "extract_infoantenas.py", "process_stations.py",
            "select_bbu_pools.py", "plot_bbu_results.py",
        ):
            shutil.copy2(PROJECT_DIR / name, self.project / name)
        self.config = yaml.safe_load((PROJECT_DIR / "config.yaml").read_text(encoding="utf-8"))
        self.config["scenario"].update(
            study_bounds=[0, 0, 1, 1], synthetic_rrhs=3, seed=7, pool_counts=[1, 2],
        )
        self.config["paths"].update(
            dataset_dir="input data", raw_stations_file="source.csv",
            processed_stations_file="processed.csv", results_dir="output/tables",
            min_delay_subdir="min_delay",
            figures_dir="../images", rrhs_file="rrhs.csv", pools_file="pools.csv",
            assignments_file="assignments.csv", comparison_file="summary.csv",
            scenario_figure="base.png", placement_figure_template="pools_{k}.png",
            distance_summary_figure="distances.png", distance_distribution_figure="cdf.png",
        )
        self.config["plot"]["figure_dpi"] = 40
        self.write_config()

    def write_config(self):
        (self.project / "config.yaml").write_text(yaml.safe_dump(self.config), encoding="utf-8")

    def run_python(self, *arguments, success=True):
        environment = dict(os.environ, PYTHONIOENCODING="utf-8", MPLBACKEND="Agg")
        result = subprocess.run(
            [sys.executable, *map(str, arguments)], cwd=self.root,
            capture_output=True, encoding="utf-8", env=environment, timeout=60,
        )
        if success:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0)
        return result

    def extract_offline(self):
        # Mock only the network. Extraction still filters against YAML bounds and
        # writes to the configured raw path, from a different working directory.
        code = """
import sys
from unittest.mock import patch
sys.path.insert(0, sys.argv[1])
import extract_infoantenas as extractor
features = [
    {'geometry': {'type': 'Point', 'coordinates': point},
     'properties': {'Gis_ID': str(index), 'operator': 'Demo', 'technology': '4G'}}
    for index, point in enumerate(((0.1, 0.1), (0.8, 0.8), (2, 2)))
]
with patch.object(extractor, 'fetch_tile', return_value=features), \
     patch.object(extractor, 'add_frequency_bands'), \
     patch.object(extractor.time, 'sleep'):
    extractor.main()
"""
        self.run_python("-c", code, self.project)

    def read_csv(self, path):
        with path.open(encoding="utf-8-sig", newline="") as handle:
            return list(csv.DictReader(handle))

    def test_shared_yaml_defaults_across_entire_pipeline(self):
        self.extract_offline()
        raw = self.project / "input data/source.csv"
        self.assertEqual(len(self.read_csv(raw)), 2)
        self.run_python(self.project / "process_stations.py")
        processed = self.read_csv(self.project / "input data/processed.csv")
        self.assertEqual(len(processed), 5)
        self.assertEqual(sum(row["is_synthetic"] == "True" for row in processed), 3)
        for row in processed:
            self.assertTrue(0 <= float(row["longitude"]) <= 1)
            self.assertTrue(0 <= float(row["latitude"]) <= 1)
        self.run_python(self.project / "select_bbu_pools.py")
        results = self.project / "output/tables/min_delay"
        summary = self.read_csv(results / "summary.csv")
        self.assertEqual([row["stage"] for row in summary], ["initial", "final"])
        self.assertTrue(all(int(row["rrh_count"]) == 5 for row in summary))
        self.run_python(self.project / "plot_bbu_results.py")
        self.assertTrue(all(int(row["served_count"]) == 5 for row in summary))
        self.assertTrue(all(row["traffic_profile"] in {"residential", "office", "mixed"} for row in processed))
        self.assertEqual(int(summary[0]["k"]), 5)
        self.assertEqual(int(summary[-1]["k"]), 5)
        self.assertEqual(
            {path.name for path in results.glob("*.csv")},
            {"rrhs.csv", "pools.csv", "assignments.csv", "summary.csv",
             "feasible_links.csv", "k_search.csv"},
        )
        images = self.project / "output/tables/images"
        self.assertEqual(
            {path.name for path in images.glob("*.png")},
            {"min_delay_assignments_initial.png", "min_delay_assignments_final.png",
             "min_delay_pool_categories_initial.png", "min_delay_pool_categories_final.png",
             "min_delay_rrh_options_initial.png", "min_delay_rrh_options_final.png",
             "min_delay_coverage.png", "cdf.png", "base.png", "distances.png"},
        )

    def test_cli_overrides_yaml_defaults(self):
        self.extract_offline()
        custom_processed = self.root / "override.csv"
        self.run_python(
            self.project / "process_stations.py", "--synthetic", "0", "--seed", "9",
            "--output", custom_processed,
        )
        self.assertEqual(len(self.read_csv(custom_processed)), 2)
        self.run_python(
            self.project / "select_bbu_pools.py", custom_processed,
            "--k", "1", "--results-dir", "custom-results",
        )
        summary = self.read_csv(self.root / "custom-results/summary.csv")
        self.assertEqual([int(row["k"]) for row in summary], [1])
        self.run_python(
            self.project / "plot_bbu_results.py", "--results-dir", "custom-results",
            "--figures-dir", "custom-images",
        )
        self.assertEqual(len(list((self.root / "custom-images").glob("*.png"))), 4)
        self.assertFalse((self.project / "output/tables").exists())

    def test_invalid_bounds_fail_before_writing_outputs(self):
        self.config["scenario"]["study_bounds"] = [1, 0, 0, 1]
        self.write_config()
        result = self.run_python(self.project / "select_bbu_pools.py", success=False)
        self.assertIn("scenario.study_bounds", result.stderr)
        self.assertFalse((self.project / "output").exists())

    def test_min_delay_overrides_expand_to_cover_all_rrhs(self):
        self.extract_offline()
        self.run_python(self.project / "process_stations.py", "--synthetic", "0")
        self.run_python(
            self.project / "select_bbu_pools.py", "--candidate-pools", "1",
            "--results-dir", "delay-results",
        )
        directory = self.root / "delay-results"
        summary = self.read_csv(directory / "summary.csv")
        self.assertEqual([row["stage"] for row in summary], ["initial", "expanded", "final"])
        self.assertEqual([int(row["served_count"]) for row in summary], [1, 2, 2])
        history = self.read_csv(directory / "k_search.csv")
        accepted = [row for row in history if row["accepted"] == "True"]
        self.assertEqual([int(row["k"]) for row in history], [1, 2])
        self.assertEqual([int(row["k"]) for row in accepted], [2])
        rejected = [row for row in history if row["accepted"] == "False"]
        self.assertEqual(len(rejected), 1)
        self.assertTrue(all(int(row["unserved_count"]) > 0 for row in rejected))
        assignments = self.read_csv(directory / "assignments.csv")
        for row in assignments:
            if row["status"] == "served":
                distance = float(row["distance_km"])
                delay = float(row["round_trip_delay_us"])
                self.assertLessEqual(distance, self.config["min_delay"]["max_link_distance_km"])
                self.assertLess(delay, self.config["min_delay"]["max_round_trip_delay_us"])
                self.assertAlmostEqual(delay, 2 * distance * 1e9 / self.config["min_delay"]["propagation_speed_m_per_s"])
                self.assertIn(row["pool_id"], row["feasible_pool_ids"].split(";"))
            else:
                self.assertEqual(row["pool_id"], "")
                self.assertEqual(int(row["feasible_pool_count"]), 0)
        self.run_python(self.project / "plot_bbu_results.py", "--results-dir", "delay-results", "--figures-dir", "delay-images")
        self.assertEqual(len(list((self.root / "delay-images").glob("*.png"))), 10)

    def test_large_start_reduces_one_pool_per_step(self):
        self.config["min_delay"]["max_link_distance_km"] = 200
        self.config["min_delay"]["max_round_trip_delay_us"] = 2000
        self.write_config()
        self.extract_offline()
        self.run_python(self.project / "process_stations.py")
        self.run_python(self.project / "select_bbu_pools.py", "--candidate-pools", "5")
        directory = self.project / "output/tables/min_delay"
        history = self.read_csv(directory / "k_search.csv")
        accepted = [row for row in history if row["accepted"] == "True"]
        self.assertEqual([int(row["k"]) for row in accepted], [5, 4, 3, 2, 1])
        self.assertTrue(all(int(row["unserved_count"]) == 0 for row in accepted))
        for previous, current in zip(accepted, accepted[1:]):
            before = set(previous["active_pool_ids"].split(";"))
            after = set(current["active_pool_ids"].split(";"))
            self.assertTrue(after < before)
            self.assertEqual(len(before - after), 1)
        self.assertEqual([int(row["k"]) for row in history], [5, 4, 3, 2, 1])
        self.assertEqual(history[-1]["accepted"], "True")
        self.assertEqual(int(history[-1]["k"]), 1)
        self.run_python(self.project / "plot_bbu_results.py")
        self.assertEqual(len(list((self.project / "output/tables/images").glob("*.png"))), 10)

    def test_random_profiles_are_configurable_and_reproducible(self):
        self.config["traffic_profiles"]["random_percentages"] = {
            "residential": 100, "office": 0, "mixed": 0,
        }
        self.write_config()
        self.extract_offline()
        self.run_python(self.project / "process_stations.py")
        output = self.project / "input data/processed.csv"
        original = output.read_bytes()
        self.assertEqual({row["traffic_profile"] for row in self.read_csv(output)}, {"residential"})
        self.run_python(self.project / "process_stations.py")
        self.assertEqual(output.read_bytes(), original)

    def test_geographic_profiles_use_first_zone_and_mixed_fallback(self):
        self.config["traffic_profiles"].update(
            mode="geographic", geographic_zones=[
                {"profile": "office", "bounds": [0, 0, 0.2, 0.2]},
                {"profile": "residential", "bounds": [0, 0, 0.5, 0.5]},
            ],
        )
        self.write_config()
        self.extract_offline()
        self.run_python(self.project / "process_stations.py", "--synthetic", "0")
        rows = self.read_csv(self.project / "input data/processed.csv")
        self.assertEqual({float(row["longitude"]): row["traffic_profile"] for row in rows}, {0.1: "office", 0.8: "mixed"})

    def test_invalid_phase_settings_fail_before_writing_outputs(self):
        cases = [
            ("min_delay", "max_round_trip_delay_us", 0),
            ("min_delay", "propagation_speed_m_per_s", float("inf")),
            ("min_delay", "max_link_distance_km", -1),
            ("min_delay", "candidate_pool_count", True),
            ("traffic_profiles", "mode", "unsupported"),
            ("traffic_profiles", "random_percentages", {"residential": 20, "office": 30, "mixed": 40}),
            ("traffic_profiles", "geographic_zones", [{"profile": "office", "bounds": [1, 0, 0, 1]}]),
            ("paths", "min_delay_subdir", "../escape"),
        ]
        for section, key, value in cases:
            with self.subTest(section=section, key=key):
                previous = self.config[section][key]
                self.config[section][key] = value
                self.write_config()
                result = self.run_python(self.project / "select_bbu_pools.py", success=False)
                self.assertIn(f"{section}.{key}", result.stderr)
                self.assertFalse((self.project / "output").exists())
                self.config[section][key] = previous


if __name__ == "__main__":
    unittest.main()
