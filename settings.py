"""Load shared pipeline settings from config.yaml; edit the YAML file to configure."""

from math import isfinite
from pathlib import Path

import yaml


PROJECT_DIR = Path(__file__).resolve().parent
CONFIG_FILE = PROJECT_DIR / "config.yaml"

with CONFIG_FILE.open(encoding="utf-8") as handle:
    _settings = yaml.safe_load(handle)
if not isinstance(_settings, dict):
    raise ValueError("config.yaml must contain a mapping of configuration sections.")


def _project_path(value):
    """Resolve directory settings independently of the working directory."""
    return PROJECT_DIR / Path(value)


def _filename(value):
    """Keep output filenames separate from their configured parent directories."""
    if not isinstance(value, str) or not value or value in {".", ".."} or any(
        separator in value for separator in ("/", "\\", ":")
    ):
        raise ValueError(f"Expected a plain output filename in config.yaml: {value!r}")
    return value


_scenario = _settings["scenario"]
STUDY_BOUNDS = tuple(_scenario["study_bounds"])
if (
    len(STUDY_BOUNDS) != 4
    or any(not isinstance(value, (int, float)) or not isfinite(value) for value in STUDY_BOUNDS)
    or not (-180 <= STUDY_BOUNDS[0] < STUDY_BOUNDS[2] <= 180)
    or not (-90 <= STUDY_BOUNDS[1] < STUDY_BOUNDS[3] <= 90)
):
    raise ValueError("scenario.study_bounds must be valid west, south, east, north coordinates.")
DEFAULT_SYNTHETIC_RRHS = _scenario["synthetic_rrhs"]
DEFAULT_SEED = _scenario["seed"]
DEFAULT_K_VALUES = tuple(_scenario["pool_counts"])
if type(DEFAULT_SYNTHETIC_RRHS) is not int or DEFAULT_SYNTHETIC_RRHS < 0:
    raise ValueError("scenario.synthetic_rrhs must be a nonnegative integer.")
if type(DEFAULT_SEED) is not int:
    raise ValueError("scenario.seed must be an integer.")
if not DEFAULT_K_VALUES or any(type(k) is not int or k < 1 for k in DEFAULT_K_VALUES):
    raise ValueError("scenario.pool_counts must contain positive integers.")

_paths = _settings["paths"]
DATASET_DIR = _project_path(_paths["dataset_dir"])
RAW_STATIONS_FILE = DATASET_DIR / _filename(_paths["raw_stations_file"])
PROCESSED_STATIONS_FILE = DATASET_DIR / _filename(_paths["processed_stations_file"])
RESULTS_DIR = _project_path(_paths["results_dir"])
FIGURES_SUBDIR = Path(_paths["figures_dir"])
RRHS_FILE = _filename(_paths["rrhs_file"])
POOLS_FILE = _filename(_paths["pools_file"])
ASSIGNMENTS_FILE = _filename(_paths["assignments_file"])
COMPARISON_FILE = _filename(_paths["comparison_file"])
RESULTS_FILES = (RRHS_FILE, POOLS_FILE, ASSIGNMENTS_FILE, COMPARISON_FILE)
if len(set(RESULTS_FILES)) != len(RESULTS_FILES):
    raise ValueError("The four result CSV filenames in config.yaml must be distinct.")
SCENARIO_FIGURE = _filename(_paths["scenario_figure"])
PLACEMENT_FIGURE_TEMPLATE = _paths["placement_figure_template"]
_filename(PLACEMENT_FIGURE_TEMPLATE.format(k=1))
DISTANCE_SUMMARY_FIGURE = _filename(_paths["distance_summary_figure"])
DISTANCE_DISTRIBUTION_FIGURE = _filename(_paths["distance_distribution_figure"])

_extraction = _settings["extraction"]
BASE_URL = _extraction["base_url"].rstrip("/")
API_URL = f"{BASE_URL}/{_extraction['api_path'].lstrip('/')}"
API_ZOOM = _extraction["api_zoom"]
TILE_SIZE_DEGREES = _extraction["tile_size_degrees"]
REQUEST_TIMEOUT = _extraction["request_timeout"]
REQUEST_ATTEMPTS = _extraction["request_attempts"]
REQUEST_PAUSE = _extraction["request_pause"]
if not isinstance(TILE_SIZE_DEGREES, (int, float)) or not isfinite(TILE_SIZE_DEGREES) or TILE_SIZE_DEGREES <= 0:
    raise ValueError("extraction.tile_size_degrees must be a finite positive number.")
if type(REQUEST_ATTEMPTS) is not int or REQUEST_ATTEMPTS < 1:
    raise ValueError("extraction.request_attempts must be a positive integer.")

_model = _settings["model"]
FREQUENCY_PROXIES = _model["frequency_proxies"]
EARTH_RADIUS_KM = _model["earth_radius_km"]

_plot = _settings["plot"]
REAL_COLOR = _plot["real_color"]
SYNTHETIC_COLOR = _plot["synthetic_color"]
POOL_COLOR = _plot["pool_color"]
SCENARIO_FIGSIZE = tuple(_plot["scenario_figsize"])
CHART_FIGSIZE = tuple(_plot["chart_figsize"])
FIGURE_DPI = _plot["figure_dpi"]
MAP_PADDING_FRACTION = _plot["map_padding_fraction"]
MAP_MIN_PADDING_DEGREES = _plot["map_min_padding_degrees"]
MAP_ASPECT = _plot["map_aspect"]
REAL_RRH_SIZE = _plot["real_rrh_size"]
SYNTHETIC_RRH_SIZE = _plot["synthetic_rrh_size"]
POOL_MARKER_SIZE = _plot["pool_marker_size"]
