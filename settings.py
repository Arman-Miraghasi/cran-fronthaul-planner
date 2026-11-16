"""Load shared pipeline settings from config.yaml; edit the YAML file to configure."""

from math import isclose, isfinite
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
    if (
        not isinstance(value, str)
        or not value
        or value in {".", ".."}
        or any(separator in value for separator in ("/", "\\", ":"))
    ):
        raise ValueError(f"Expected a plain output filename in config.yaml: {value!r}")
    return value


def _bounds(values, name):
    if (
        not isinstance(values, (list, tuple))
        or len(values) != 4
        or any(
            type(value) not in (int, float) or not isfinite(value) for value in values
        )
        or not (-180 <= values[0] < values[2] <= 180)
        or not (-90 <= values[1] < values[3] <= 90)
    ):
        raise ValueError(f"{name} must be valid west, south, east, north coordinates.")
    return tuple(values)


def _positive_number(value, name):
    if type(value) not in (int, float) or not isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be a finite positive number.")
    return value


_scenario = _settings["scenario"]
STUDY_BOUNDS = _bounds(_scenario["study_bounds"], "scenario.study_bounds")
DEFAULT_SYNTHETIC_RRHS = _scenario["synthetic_rrhs"]
DEFAULT_SEED = _scenario["seed"]
if type(DEFAULT_SYNTHETIC_RRHS) is not int or DEFAULT_SYNTHETIC_RRHS < 0:
    raise ValueError("scenario.synthetic_rrhs must be a nonnegative integer.")
if type(DEFAULT_SEED) is not int:
    raise ValueError("scenario.seed must be an integer.")

_min_delay = _settings.get("min_delay", {})
if not isinstance(_min_delay, dict):
    raise ValueError("min_delay must be a mapping.")
MAX_ROUND_TRIP_DELAY_US = _positive_number(
    _min_delay.get("max_round_trip_delay_us", 200),
    "min_delay.max_round_trip_delay_us",
)
PROPAGATION_SPEED_M_PER_S = _positive_number(
    _min_delay.get("propagation_speed_m_per_s", 300_000_000),
    "min_delay.propagation_speed_m_per_s",
)
MAX_LINK_DISTANCE_KM = _positive_number(
    _min_delay.get("max_link_distance_km", 4.5), "min_delay.max_link_distance_km"
)
CANDIDATE_POOL_COUNT = _min_delay.get("candidate_pool_count", 25)
if type(CANDIDATE_POOL_COUNT) is not int or CANDIDATE_POOL_COUNT < 1:
    raise ValueError("min_delay.candidate_pool_count must be a positive integer.")

# The earlier placement script still imports this default for its --k argument.
DEFAULT_K_VALUES = tuple(_scenario.get("pool_counts", (CANDIDATE_POOL_COUNT,)))
if not DEFAULT_K_VALUES or any(type(k) is not int or k < 1 for k in DEFAULT_K_VALUES):
    raise ValueError("scenario.pool_counts must contain positive integers.")

TRAFFIC_PROFILE_TYPES = ("residential", "office", "mixed")
_traffic = _settings.get("traffic_profiles", {})
if not isinstance(_traffic, dict):
    raise ValueError("traffic_profiles must be a mapping.")
TRAFFIC_PROFILE_MODE = _traffic.get("mode", "random")
if TRAFFIC_PROFILE_MODE not in ("random", "geographic"):
    raise ValueError("traffic_profiles.mode must be random or geographic.")
TRAFFIC_RANDOM_PERCENTAGES = _traffic.get(
    "random_percentages", {"residential": 20, "office": 30, "mixed": 50}
)
if (
    not isinstance(TRAFFIC_RANDOM_PERCENTAGES, dict)
    or TRAFFIC_RANDOM_PERCENTAGES.keys() != set(TRAFFIC_PROFILE_TYPES)
    or any(
        type(value) not in (int, float) or not 0 <= value <= 100
        for value in TRAFFIC_RANDOM_PERCENTAGES.values()
    )
    or not isclose(
        sum(TRAFFIC_RANDOM_PERCENTAGES.values()), 100, rel_tol=0, abs_tol=1e-9
    )
):
    raise ValueError(
        "traffic_profiles.random_percentages must give residential, office, and mixed "
        "percentages between 0 and 100 that sum to 100."
    )

_geographic_zones = _traffic.get("geographic_zones", [])
if not isinstance(_geographic_zones, list):
    raise ValueError("traffic_profiles.geographic_zones must be a list.")
_parsed_zones = []
for _index, _zone in enumerate(_geographic_zones, start=1):
    _name = f"traffic_profiles.geographic_zones entry {_index}"
    if not isinstance(_zone, dict) or _zone.get("profile") not in TRAFFIC_PROFILE_TYPES:
        raise ValueError(f"{_name} must have a residential, office, or mixed profile.")
    _parsed_zones.append(
        (_zone["profile"], _bounds(_zone.get("bounds"), f"{_name} bounds"))
    )
TRAFFIC_GEOGRAPHIC_ZONES = tuple(_parsed_zones)

_paths = _settings["paths"]
DATASET_DIR = _project_path(_paths["dataset_dir"])
RAW_STATIONS_FILE = DATASET_DIR / _filename(_paths["raw_stations_file"])
PROCESSED_STATIONS_FILE = DATASET_DIR / _filename(_paths["processed_stations_file"])
RESULTS_DIR = _project_path(_paths["results_dir"])
_min_delay_subdir = Path(_paths.get("min_delay_subdir", "min_delay"))
if (
    _min_delay_subdir.anchor
    or not _min_delay_subdir.parts
    or ".." in _min_delay_subdir.parts
):
    raise ValueError("paths.min_delay_subdir must name a subdirectory of results_dir.")
MIN_DELAY_RESULTS_DIR = RESULTS_DIR / _min_delay_subdir
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
if (
    not isinstance(TILE_SIZE_DEGREES, (int, float))
    or not isfinite(TILE_SIZE_DEGREES)
    or TILE_SIZE_DEGREES <= 0
):
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
