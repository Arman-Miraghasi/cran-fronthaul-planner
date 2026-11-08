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
        environment = dict(os.environ, PYTHONIOENCODING="utf-8")
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
        summary = self.read_csv(self.project / "output/tables/summary.csv")
        self.assertEqual([int(row["k"]) for row in summary], [1, 2])
        self.assertTrue(all(int(row["rrh_count"]) == 5 for row in summary))
        self.run_python(self.project / "plot_bbu_results.py")
        images = self.project / "output/images"
        self.assertEqual(
            {path.name for path in images.glob("*.png")},
            {"base.png", "pools_1.png", "pools_2.png", "distances.png", "cdf.png"},
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


if __name__ == "__main__":
    unittest.main()
