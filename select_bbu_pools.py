"""Select BBU pools from stations_for_simulation.csv for one or more values of K."""

import argparse
import csv
import math
import tempfile
from dataclasses import dataclass
from pathlib import Path
from statistics import mean

from settings import (
    ASSIGNMENTS_FILE,
    COMPARISON_FILE,
    DEFAULT_K_VALUES,
    EARTH_RADIUS_KM,
    POOLS_FILE,
    PROCESSED_STATIONS_FILE,
    RESULTS_DIR,
    RESULTS_FILES,
    RRHS_FILE,
)


@dataclass(frozen=True)
class RRH:
    record_id: str
    point: tuple
    is_synthetic: bool


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
            rrhs.append(RRH(record_id, (lon, lat), flag in {"true", "1"}))
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "input",
        type=Path,
        nargs="?",
        default=PROCESSED_STATIONS_FILE,
    )
    parser.add_argument(
        "--k",
        type=int,
        nargs="+",
        default=DEFAULT_K_VALUES,
        help=f"Pool counts (default: {' '.join(map(str, DEFAULT_K_VALUES))})",
    )
    parser.add_argument("--results-dir", type=Path, default=RESULTS_DIR)
    args = parser.parse_args()
    if not args.k or min(args.k) < 1:
        parser.error("Each K must be a positive integer.")

    try:
        rrhs = read_rrhs(args.input)
        candidates = candidate_indices(rrhs)
        k_values = set(args.k)
        if max(k_values) > len(candidates):
            raise ValueError(
                f"K={max(k_values)} exceeds {len(candidates)} distinct RRH locations."
            )
        output_paths = {(args.results_dir / name).resolve() for name in RESULTS_FILES}
        if args.input.resolve() in output_paths:
            raise ValueError(
                "Choose a results directory that will keep the input CSV separate."
            )
        synthetic_count = sum(rrh.is_synthetic for rrh in rrhs)
        print(
            f"{len(rrhs)} RRHs ({synthetic_count} synthetic) at {len(candidates)} candidate locations"
        )
        write_results(args.results_dir, rrhs, candidates, k_values)
    except (OSError, ValueError, csv.Error) as error:
        parser.exit(1, f"Placement failed. {error}\n")
    print(f"Saved results to {args.results_dir.resolve()}")


if __name__ == "__main__":
    main()
