"""Select BBU pools and assign RRHs within the wireless distance and delay limits."""

import argparse
import csv
import math
import tempfile
from collections import Counter
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from statistics import mean

from settings import (
    ASSIGNMENTS_FILE,
    CANDIDATE_POOL_COUNT,
    COMPARISON_FILE,
    EARTH_RADIUS_KM,
    MAX_LINK_DISTANCE_KM,
    MAX_ROUND_TRIP_DELAY_US,
    MIN_DELAY_RESULTS_DIR,
    POOLS_FILE,
    PROCESSED_STATIONS_FILE,
    PROPAGATION_SPEED_M_PER_S,
    RESULTS_DIR,
    RESULTS_FILES,
    RRHS_FILE,
    TRAFFIC_PROFILE_TYPES,
)

FEASIBLE_LINKS_FILE = "feasible_links.csv"
K_SEARCH_FILE = "k_search.csv"
POOL_WORKBOOK_FILE = "bbu_pool_rrhs.xlsx"
POOL_CATEGORIES = ("only_option", "preferred_with_alternatives", "backup")
POOL_CATEGORY_LABELS = {
    "only_option": "1. Only feasible pool",
    "preferred_with_alternatives": "2. Optimal pool with alternatives",
    "backup": "3. Feasible but not optimal",
}


@dataclass(frozen=True)
class RRH:
    record_id: str
    point: tuple
    is_synthetic: bool
    traffic_profile: str = "mixed"


@dataclass(frozen=True)
class Link:
    pool_number: int
    distance_km: float
    round_trip_delay_us: float
    feasible: bool


def read_rrhs(path):
    rrhs = []
    seen = set()
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, strict=True)
        required = {"record_id", "longitude", "latitude"}
        if not required.issubset(reader.fieldnames or []):
            raise ValueError(
                "The input needs record_id, longitude and latitude columns."
            )
        for row in reader:
            if None in row or any(value is None for value in row.values()):
                raise ValueError(
                    f"Malformed CSV record ending on line {reader.line_num}."
                )
            record_id = row["record_id"].strip()
            if not record_id or record_id in seen:
                raise ValueError(
                    f"Blank or duplicate record_id on line {reader.line_num}."
                )
            lon, lat = float(row["longitude"]), float(row["latitude"])
            if not (-180 <= lon <= 180 and -90 <= lat <= 90):
                raise ValueError(f"Invalid coordinates for {record_id}.")
            flag = row.get("is_synthetic", "false").strip().casefold()
            if flag not in {"true", "false", "1", "0"}:
                raise ValueError(f"Invalid is_synthetic flag for {record_id}.")
            profile = row.get("traffic_profile", "mixed").strip().casefold()
            if profile not in TRAFFIC_PROFILE_TYPES:
                raise ValueError(f"Invalid traffic_profile for {record_id}.")
            rrhs.append(RRH(record_id, (lon, lat), flag in {"true", "1"}, profile))
            seen.add(record_id)
    if not rrhs:
        raise ValueError("The simulation dataset contains no RRHs.")
    return sorted(rrhs, key=lambda rrh: rrh.record_id)


def distance_km(a, b):
    """Spherical great-circle distance for two (longitude, latitude) pairs."""
    lon1, lat1, lon2, lat2 = map(math.radians, (*a, *b))
    h = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    )
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(max(0, min(1, h))))


def candidate_indices(rrhs):
    """Use one candidate per coordinate pair, preferring a real RRH at shared sites."""
    by_position = {}
    for index in sorted(range(len(rrhs)), key=lambda i: rrhs[i].is_synthetic):
        by_position.setdefault(rrhs[index].point, index)
    return set(by_position.values())


def farthest_first(rrhs, candidates, k_values):
    """Yield nested pool sets and nearest-pool assignments at the requested K values."""
    remaining = candidates.copy()
    points = [rrh.point for rrh in rrhs]
    centroid = (
        mean(points[i][0] for i in candidates),
        mean(points[i][1] for i in candidates),
    )
    next_pool = min(remaining, key=lambda i: (distance_km(points[i], centroid), i))
    selected = []
    nearest_pool = [0] * len(rrhs)
    nearest_distance = [math.inf] * len(rrhs)
    max_k = max(k_values)

    for k in range(1, max_k + 1):
        remaining.remove(next_pool)
        selected.append(next_pool)
        pool_point = points[next_pool]
        for i, point in enumerate(points):
            distance = distance_km(point, pool_point)
            if distance < nearest_distance[i]:
                nearest_distance[i] = distance
                nearest_pool[i] = k
        if k in k_values:
            yield k, tuple(selected), tuple(nearest_pool), tuple(nearest_distance)
        if k < max_k:
            next_pool = max(remaining, key=lambda i: (nearest_distance[i], -i))


def pool_id(number):
    return f"BBU_{number:02d}"


def write_results(results_dir, rrhs, candidates, k_values):
    """Stage the CSV files before replacing the outputs from a previous run."""
    results_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=results_dir) as staging:
        stage = Path(staging)
        with (stage / RRHS_FILE).open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(("record_id", "longitude", "latitude", "is_synthetic"))
            writer.writerows(
                (rrh.record_id, *rrh.point, rrh.is_synthetic) for rrh in rrhs
            )

        with (
            (stage / POOLS_FILE).open(
                "w", encoding="utf-8-sig", newline=""
            ) as pool_file,
            (stage / ASSIGNMENTS_FILE).open(
                "w", encoding="utf-8-sig", newline=""
            ) as assignment_file,
            (stage / COMPARISON_FILE).open(
                "w", encoding="utf-8-sig", newline=""
            ) as comparison_file,
        ):
            pools = csv.writer(pool_file)
            assignments = csv.writer(assignment_file)
            comparison = csv.writer(comparison_file)
            pools.writerow(
                (
                    "k",
                    "pool_id",
                    "candidate_record_id",
                    "longitude",
                    "latitude",
                    "is_synthetic",
                )
            )
            assignments.writerow(("k", "record_id", "pool_id", "distance_km"))
            comparison.writerow(("k", "rrh_count", "max_km", "p95_km", "mean_km"))

            p95_index = math.ceil(0.95 * len(rrhs)) - 1
            for k, selected, nearest, distances in farthest_first(
                rrhs, candidates, k_values
            ):
                for number, index in enumerate(selected, start=1):
                    rrh = rrhs[index]
                    pools.writerow(
                        (
                            k,
                            pool_id(number),
                            rrh.record_id,
                            *rrh.point,
                            rrh.is_synthetic,
                        )
                    )
                assignments.writerows(
                    (k, rrh.record_id, pool_id(number), distance)
                    for rrh, number, distance in zip(rrhs, nearest, distances)
                )
                maximum = max(distances)
                p95 = sorted(distances)[p95_index]
                average = mean(distances)
                comparison.writerow((k, len(rrhs), maximum, p95, average))
                print(
                    f"K={k:>3}   max {maximum:.3f} km   p95 {p95:.3f} km   mean {average:.3f} km"
                )

        for name in RESULTS_FILES:
            (stage / name).replace(results_dir / name)


def round_trip_delay_us(distance):
    """Round-trip propagation delay for a direct wireless link."""
    return 2 * distance * 1e9 / PROPAGATION_SPEED_M_PER_S


def fits_link_limits(distance, delay):
    return distance <= MAX_LINK_DISTANCE_KM and delay < MAX_ROUND_TRIP_DELAY_US


def select_candidate_sites(rrhs, candidates, starting_count, on_layout=None):
    """Start with the requested farthest-first sites and extend until all RRHs can connect."""
    initial_count = min(starting_count, len(candidates))
    if initial_count < starting_count:
        print(
            f"Using all {initial_count} distinct sites for the initial candidate set."
        )
    layouts = farthest_first(
        rrhs, candidates, range(initial_count, len(candidates) + 1)
    )
    for k, selected, _, distances in layouts:
        if on_layout is not None:
            on_layout(k, distances, initial_count)
        worst_distance = max(distances)
        if fits_link_limits(worst_distance, round_trip_delay_us(worst_distance)):
            return initial_count, selected
    raise RuntimeError(
        "No candidate set reaches all RRHs, including at their own sites."
    )


def build_links(rrhs, selected):
    """Precompute links, ordered from lowest delay to highest for each RRH."""
    links_by_rrh = []
    for rrh in rrhs:
        links = []
        for number, index in enumerate(selected, start=1):
            distance = distance_km(rrh.point, rrhs[index].point)
            delay = round_trip_delay_us(distance)
            links.append(
                Link(number, distance, delay, fits_link_limits(distance, delay))
            )
        links.sort(key=lambda link: (link.round_trip_delay_us, link.pool_number))
        links_by_rrh.append(links)
    return links_by_rrh


def prune_pools(links_by_rrh, pool_count, on_trial=None):
    """Switch off redundant pools while preserving every served RRH."""
    covered_by = {number: [] for number in range(1, pool_count + 1)}
    option_counts = []
    for index, links in enumerate(links_by_rrh):
        feasible_numbers = [link.pool_number for link in links if link.feasible]
        option_counts.append(len(feasible_numbers))
        for number in feasible_numbers:
            covered_by[number].append(index)

    active = set(covered_by)
    # Try pools that reach the fewest RRHs first, with stable tie-breaking.
    removal_order = sorted(
        covered_by, key=lambda number: (len(covered_by[number]), -number)
    )
    for number in removal_order:
        covered_rrhs = covered_by[number]
        removable = all(option_counts[index] > 1 for index in covered_rrhs)
        if on_trial is not None:
            on_trial(active - {number}, number, removable)
        if removable:
            active.remove(number)
            for index in covered_rrhs:
                option_counts[index] -= 1
    return active


def search_record(action, active, distances, accepted=True, changed_pool=None):
    """Describe an evaluated layout, including rejected single-pool removals."""
    served_distances = [
        distance for distance in distances
        if fits_link_limits(distance, round_trip_delay_us(distance))
    ]
    total = len(distances)
    return {
        "action": action,
        "accepted": accepted,
        "k": len(active),
        "active_pool_ids": ";".join(pool_id(number) for number in sorted(active)),
        "changed_pool_id": pool_id(changed_pool) if changed_pool else "",
        "rrh_count": total,
        "served_count": len(served_distances),
        "unserved_count": total - len(served_distances),
        "coverage_percent": 100 * len(served_distances) / total,
        "max_km": max(distances),
        "p95_km": sorted(distances)[math.ceil(.95 * total) - 1],
        "mean_km": mean(distances),
        "max_served_rtt_us": max(map(round_trip_delay_us, served_distances), default=""),
    }


def search_pool_counts(rrhs, candidates, starting_count):
    """Find the minimum feasible K in one deterministic nested placement sequence.

    Test the configured start first. Increase K until feasible, or decrease K
    until the first infeasible layout, retaining the preceding feasible K.
    """
    history = []
    initial_count = min(starting_count, len(candidates))
    if initial_count < starting_count:
        print(f"Using all {initial_count} distinct sites for the initial candidate set.")

    def record(k, distances, action):
        feasible = all(fits_link_limits(d, round_trip_delay_us(d)) for d in distances)
        changed = k if action == "add" else k + 1 if action == "remove" else None
        history.append(search_record(action, set(range(1, k + 1)), distances,
                                     accepted=feasible, changed_pool=changed))
        row = history[-1]
        print(f"Test K={k}: {row['served_count']}/{len(rrhs)} served, max {row['max_km']:.3f} km ({'feasible' if feasible else 'infeasible'})")
        return feasible

    # Prefix layouts share fixed sites, so coverage cannot worsen as K increases.
    # Lower prefixes are cached for a downward search; only tested Ks are logged.
    prefixes = {}
    layouts = farthest_first(rrhs, candidates, range(1, len(candidates) + 1))
    for k, selected, _, distances in layouts:
        if k < initial_count:
            prefixes[k] = distances
            continue
        if k == initial_count:
            if record(k, distances, "initial"):
                best_k = k
                for smaller in range(k - 1, 0, -1):
                    if not record(smaller, prefixes[smaller], "remove"):
                        break
                    best_k = smaller
                break
        elif record(k, distances, "add"):
            best_k = k
            break
    else:
        raise RuntimeError("No candidate count serves all RRHs.")

    final = set(range(1, best_k + 1))
    print(f"Minimum feasible K in the nested placement sequence: {best_k}")
    return initial_count, selected, build_links(rrhs, selected), final, history


def write_delay_stage(stage, rrhs, links_by_rrh, active, writers):
    nearest_distances = []
    served_delays = []
    for rrh, links in zip(rrhs, links_by_rrh):
        active_links = [link for link in links if link.pool_number in active]
        nearest_distances.append(active_links[0].distance_km)
        feasible = [link for link in active_links if link.feasible]
        chosen = feasible[0] if feasible else None
        writers[ASSIGNMENTS_FILE].writerow(
            (
                stage,
                rrh.record_id,
                "served" if chosen else "unserved",
                pool_id(chosen.pool_number) if chosen else "",
                chosen.distance_km if chosen else "",
                chosen.round_trip_delay_us if chosen else "",
                active_links[0].distance_km,
                len(feasible),
                ";".join(pool_id(link.pool_number) for link in feasible),
            )
        )
        if chosen:
            served_delays.append(chosen.round_trip_delay_us)

        for index, link in enumerate(feasible):
            if index > 0:
                category = "backup"
            elif len(feasible) == 1:
                category = "only_option"
            else:
                category = "preferred_with_alternatives"
            writers[FEASIBLE_LINKS_FILE].writerow(
                (
                    stage,
                    rrh.record_id,
                    pool_id(link.pool_number),
                    link.distance_km,
                    link.round_trip_delay_us,
                    category,
                )
            )

    total = len(rrhs)
    served = len(served_delays)
    p95_index = math.ceil(0.95 * total) - 1
    writers[COMPARISON_FILE].writerow(
        (
            stage,
            len(active),
            total,
            served,
            total - served,
            100 * served / total,
            max(nearest_distances),
            sorted(nearest_distances)[p95_index],
            mean(nearest_distances),
            max(served_delays, default=""),
            MAX_LINK_DISTANCE_KM,
            MAX_ROUND_TRIP_DELAY_US,
            PROPAGATION_SPEED_M_PER_S,
        )
    )
    print(
        f"{stage.capitalize()}: {len(active)} active pools, {served}/{total} RRHs served"
    )


def read_pool_categories(directory):
    """Read category totals from the Excel summary for plotting and validation."""
    from openpyxl import load_workbook

    book = load_workbook(directory / POOL_WORKBOOK_FILE, read_only=True, data_only=True)
    try:
        return [
            dict(zip(("stage", "pool_id", *POOL_CATEGORIES), row[:5]))
            for row in book["Summary"].iter_rows(min_row=2, values_only=True)
        ]
    finally:
        book.close()


def write_pool_workbook(directory):
    """Export the per-pool RRH lists and matching counts to native Excel."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    from openpyxl.worksheet.table import Table, TableStyleInfo

    def read_csv(name):
        with (directory / name).open(encoding="utf-8-sig", newline="") as handle:
            return list(csv.DictReader(handle))

    rrhs = {row["record_id"]: row for row in read_csv(RRHS_FILE)}
    assignments = {(row["stage"], row["record_id"]): row for row in read_csv(ASSIGNMENTS_FILE)}
    links = read_csv(FEASIBLE_LINKS_FILE)
    catalog = read_csv(POOLS_FILE)
    totals = Counter((row["stage"], row["pool_id"], row["category"]) for row in links)
    categories = []
    for stage in read_csv(COMPARISON_FILE):
        for pool in catalog:
            if stage["stage"] == "initial" and pool["is_initial"] != "True":
                continue
            if stage["stage"] == "final" and pool["is_active_final"] != "True":
                continue
            categories.append({
                "stage": stage["stage"], "pool_id": pool["pool_id"],
                **{key: totals[(stage["stage"], pool["pool_id"], key)] for key in POOL_CATEGORIES},
            })
    workbook = Workbook()
    notes = workbook.active
    notes.title = "Read me"
    for row in [
        ("BBU pool to RRH connectivity", ""),
        ("Category", "Meaning"),
        (POOL_CATEGORY_LABELS["only_option"], "This is the RRH's only feasible pool."),
        (POOL_CATEGORY_LABELS["preferred_with_alternatives"], "Closest feasible pool; other feasible pools also exist."),
        (POOL_CATEGORY_LABELS["backup"], "Feasible alternative; another pool is the closest choice."),
        ("Stages", "Feasibility and categories are recalculated for the active pools at each stage."),
        ("Pool sheets", "One row per feasible RRH link. Filter Stage and Category to inspect each list."),
        ("Alternative pool IDs", "All feasible pools other than the pool represented by this row."),
        ("Units", "Distances in km; round-trip propagation delays in microseconds."),
        ("Limits", "Every listed link satisfies both configured distance and strict delay limits."),
    ]:
        notes.append(row)
    notes.column_dimensions["A"].width = 40
    notes.column_dimensions["B"].width = 105
    summary = workbook.create_sheet("Summary")
    summary.append(["Stage", "Pool ID", *POOL_CATEGORY_LABELS.values(), "Total feasible RRHs"])
    stage_order = {}
    for row in categories:
        counts = [int(row[category]) for category in POOL_CATEGORIES]
        summary.append([row["stage"], row["pool_id"], *counts, sum(counts)])
        stage_order.setdefault(row["stage"], len(stage_order))

    headers = ["Stage", "Category", "RRH ID", "Traffic profile", "Longitude", "Latitude",
               "Distance (km)", "Round-trip delay (us)", "Optimal pool ID",
               "Feasible pool count", "Alternative pool IDs", "All feasible pool IDs"]
    pool_sheets = {}
    for pool in catalog:
        sheet = workbook.create_sheet(pool["pool_id"])
        sheet.append(headers)
        pool_sheets[pool["pool_id"]] = sheet
    category_order = {category: i for i, category in enumerate(POOL_CATEGORIES)}
    for link in sorted(links, key=lambda row: (row["pool_id"], stage_order[row["stage"]], category_order[row["category"]], row["record_id"])):
        rrh = rrhs[link["record_id"]]
        assigned = assignments[(link["stage"], link["record_id"])]
        options = assigned["feasible_pool_ids"].split(";")
        pool_sheets[link["pool_id"]].append([
            link["stage"], POOL_CATEGORY_LABELS[link["category"]], link["record_id"],
            rrh["traffic_profile"], float(rrh["longitude"]), float(rrh["latitude"]),
            float(link["distance_km"]), float(link["round_trip_delay_us"]),
            assigned["pool_id"], int(assigned["feasible_pool_count"]),
            ";".join(option for option in options if option != link["pool_id"]),
            assigned["feasible_pool_ids"],
        ])

    for index, sheet in enumerate([summary, *pool_sheets.values()]):
        sheet.freeze_panes = "C2"
        for cell in sheet[1]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="166A98")
        for column in sheet.columns:
            letter = column[0].column_letter
            sheet.column_dimensions[letter].width = min(55, max(18, max(len(str(cell.value or "")) for cell in column) + 2))
        if sheet.max_row > 1:
            table = Table(displayName=f"PoolData{index}", ref=sheet.dimensions)
            table.tableStyleInfo = TableStyleInfo(name="TableStyleMedium2", showRowStripes=True)
            sheet.add_table(table)
        for row in sheet.iter_rows(min_row=2):
            for cell in row:
                if isinstance(cell.value, str):
                    cell.data_type = "s"
                elif isinstance(cell.value, float):
                    cell.number_format = "0.000000"

    workbook.save(directory / POOL_WORKBOOK_FILE)
    workbook.close()


def write_min_delay_results(
    results_dir, rrhs, initial_count, selected, links_by_rrh, final, history
):
    headers = {
        RRHS_FILE: (
            "record_id",
            "longitude",
            "latitude",
            "is_synthetic",
            "traffic_profile",
        ),
        POOLS_FILE: (
            "pool_id",
            "candidate_record_id",
            "longitude",
            "latitude",
            "is_synthetic",
            "is_initial",
            "is_active_final",
        ),
        ASSIGNMENTS_FILE: (
            "stage",
            "record_id",
            "status",
            "pool_id",
            "distance_km",
            "round_trip_delay_us",
            "nearest_distance_km",
            "feasible_pool_count",
            "feasible_pool_ids",
        ),
        COMPARISON_FILE: (
            "stage",
            "k",
            "rrh_count",
            "served_count",
            "unserved_count",
            "coverage_percent",
            "max_km",
            "p95_km",
            "mean_km",
            "max_served_rtt_us",
            "max_link_distance_km",
            "max_round_trip_delay_us",
            "propagation_speed_m_per_s",
        ),
        FEASIBLE_LINKS_FILE: (
            "stage",
            "record_id",
            "pool_id",
            "distance_km",
            "round_trip_delay_us",
            "category",
        ),
        K_SEARCH_FILE: ("step", *history[0].keys()),
    }
    results_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=results_dir) as staging:
        stage_dir = Path(staging)
        with ExitStack() as files:
            writers = {}
            for name, header in headers.items():
                handle = files.enter_context(
                    (stage_dir / name).open("w", encoding="utf-8-sig", newline="")
                )
                writers[name] = csv.writer(handle)
                writers[name].writerow(header)

            for step, record in enumerate(history):
                writers[K_SEARCH_FILE].writerow((step, *record.values()))

            writers[RRHS_FILE].writerows(
                (rrh.record_id, *rrh.point, rrh.is_synthetic, rrh.traffic_profile)
                for rrh in rrhs
            )
            for number, index in enumerate(selected, start=1):
                rrh = rrhs[index]
                writers[POOLS_FILE].writerow(
                    (
                        pool_id(number),
                        rrh.record_id,
                        *rrh.point,
                        rrh.is_synthetic,
                        number <= initial_count,
                        number in final,
                    )
                )

            initial = set(range(1, initial_count + 1))
            write_delay_stage("initial", rrhs, links_by_rrh, initial, writers)
            if len(selected) > initial_count:
                expanded = set(range(1, len(selected) + 1))
                write_delay_stage("expanded", rrhs, links_by_rrh, expanded, writers)
            write_delay_stage("final", rrhs, links_by_rrh, final, writers)

        write_pool_workbook(stage_dir)
        for name in (*headers, POOL_WORKBOOK_FILE):
            (stage_dir / name).replace(results_dir / name)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "input",
        type=Path,
        nargs="?",
        default=PROCESSED_STATIONS_FILE,
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--k",
        type=int,
        nargs="+",
        help="Run the earlier distance-only comparison for these pool counts.",
    )
    mode.add_argument(
        "--candidate-pools",
        type=int,
        default=CANDIDATE_POOL_COUNT,
        help=f"Starting candidate count for minimum delay (default: {CANDIDATE_POOL_COUNT}).",
    )
    parser.add_argument(
        "--results-dir", type=Path, help="Override the results directory."
    )
    args = parser.parse_args()
    if args.k is not None and min(args.k) < 1:
        parser.error("Each K must be a positive integer.")
    if args.candidate_pools < 1:
        parser.error("--candidate-pools must be a positive integer.")

    legacy = args.k is not None
    results_dir = args.results_dir or (RESULTS_DIR if legacy else MIN_DELAY_RESULTS_DIR)
    filenames = (
        RESULTS_FILES
        if legacy
        else (*RESULTS_FILES, FEASIBLE_LINKS_FILE, K_SEARCH_FILE, POOL_WORKBOOK_FILE)
    )
    try:
        if len(set(filenames)) != len(filenames):
            raise ValueError("The result CSV filenames must be distinct.")
        output_paths = {(results_dir / name).resolve() for name in filenames}
        if args.input.resolve() in output_paths:
            raise ValueError(
                "Choose a results directory that will keep the input CSV separate."
            )
        if any(path.is_dir() for path in output_paths):
            raise ValueError("A result CSV path already names a directory.")

        rrhs = read_rrhs(args.input)
        candidates = candidate_indices(rrhs)
        synthetic_count = sum(rrh.is_synthetic for rrh in rrhs)
        print(
            f"{len(rrhs)} RRHs ({synthetic_count} synthetic) at {len(candidates)} candidate locations"
        )
        if legacy:
            k_values = set(args.k)
            if max(k_values) > len(candidates):
                raise ValueError(
                    f"K={max(k_values)} exceeds {len(candidates)} distinct RRH locations."
                )
            write_results(results_dir, rrhs, candidates, k_values)
        else:
            initial_count, selected, links_by_rrh, final, history = search_pool_counts(
                rrhs, candidates, args.candidate_pools
            )
            write_min_delay_results(
                results_dir, rrhs, initial_count, selected, links_by_rrh, final, history
            )
            print(f"Saved Excel pool lists to {results_dir / POOL_WORKBOOK_FILE}")
    except (OSError, ValueError, csv.Error, RuntimeError) as error:
        parser.exit(1, f"Placement failed. {error}\n")
    print(f"Saved results to {results_dir.resolve()}")


if __name__ == "__main__":
    main()
