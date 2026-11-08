"""Prepare stations_raw.csv for the C-RAN simulator.

The output contains classified real station records and optional synthetic
RRHs within STUDY_BOUNDS.
Explicit technology labels take precedence. When labels are missing, the
project's frequency rules produce estimates marked frequency_inferred.
"""

import argparse
import csv
import json
import random
import re
import tempfile
from collections import Counter
from math import isfinite
from pathlib import Path

# West, south, east, north in decimal degrees, matching the raw extractor.
STUDY_BOUNDS = (1.899402, 41.208051, 2.058360, 41.381086)
DEFAULT_SYNTHETIC_RRHS = 0
DEFAULT_SEED = 42
REQUIRED_COLUMNS = {"site_id", "longitude", "latitude", "operator"}
OUTPUT_COLUMNS = (
    "record_id",
    "site_id",
    "longitude",
    "latitude",
    "operator",
    "technology",
    "technology_source",
    "supported_technologies",
    "frequency_bands_mhz",
    "is_synthetic",
)

RADIO_NAMES = {
    "2G": {"2g", "gsm", "gprs", "edge"},
    "3G": {"3g", "umts", "wcdma", "hspa", "hsdpa", "hsupa"},
    "4G": {"4g", "lte", "ltea", "lteadvanced", "eutra", "4glte", "lte4g"},
    "5G": {"5g", "nr", "5gnr", "nr5g", "5gsa", "5gnsa"},
}
MISSING_LABELS = {"", "unknown", "desconocido", "null", "none", "n/a", "sin datos"}
LABEL_SEPARATOR = re.compile(r"[,;/|+&]+|\b(?:and|y)\b", re.IGNORECASE)
FREQUENCY_RANGE = re.compile(r"(\d+(?:[.,]\d+)?)\s*[-–—−]\s*(\d+(?:[.,]\d+)?)")

# Frequency proxies in MHz for this scenario. The 700 and 800 MHz windows
# are disjoint so one assigned interval cannot trigger both labels.
FREQUENCY_PROXIES = {
    "4G": ((791, 821), (832, 862)),
    "5G": ((703, 733), (758, 788), (3400, 3800)),
}


def generation_for_label(label):
    name = re.sub(r"[^a-z0-9]", "", label.casefold())
    for generation, aliases in RADIO_NAMES.items():
        if name in aliases:
            return generation
    return None


def reported_generations(raw_label):
    """Return the reported generations, or None when the source field is missing."""
    if raw_label.casefold() in MISSING_LABELS:
        return None

    try:
        value = json.loads(raw_label)
    except json.JSONDecodeError:
        value = raw_label

    if value is None or value == [] or value == {}:
        return None
    if isinstance(value, str) and value.strip().casefold() in MISSING_LABELS:
        return None

    generations = set()
    if isinstance(value, dict):
        # Support source fields such as {"LTE": true, "NR": false}.
        for label, enabled in value.items():
            if str(enabled).strip().casefold() in {"true", "1", "yes", "si", "sí"}:
                generation = generation_for_label(label)
                if generation:
                    generations.add(generation)
        return generations

    labels = value if isinstance(value, list) else [value]
    for label in labels:
        if isinstance(label, str):
            for token in LABEL_SEPARATOR.split(label):
                generation = generation_for_label(token)
                if generation:
                    generations.add(generation)
    return generations


def infer_generations(bands):
    generations = set()
    for lower, upper in FREQUENCY_RANGE.findall(bands):
        low = float(lower.replace(",", "."))
        high = float(upper.replace(",", "."))
        for generation, windows in FREQUENCY_PROXIES.items():
            if any(start <= low < high <= end for start, end in windows):
                generations.add(generation)
    return generations


def read_stations(input_path, counts):
    """Validate raw rows and group station records by operator and site ID."""
    west, south, east, north = STUDY_BOUNDS
    groups = {}
    with input_path.open(encoding="utf-8-sig", newline="") as handle:
        header = handle.readline()
        handle.seek(0)
        delimiter = csv.Sniffer().sniff(header, delimiters=",;\t").delimiter
        reader = csv.DictReader(handle, delimiter=delimiter)
        if not REQUIRED_COLUMNS.issubset(reader.fieldnames or []):
            missing = sorted(REQUIRED_COLUMNS - set(reader.fieldnames or []))
            raise ValueError(f"Missing columns in the raw CSV: {', '.join(missing)}")

        for row in reader:
            counts["read"] += 1
            if None in row or any(value is None for value in row.values()):
                counts["invalid"] += 1
                continue

            site_id = row["site_id"].strip()
            operator = " ".join(row["operator"].split())
            try:
                longitude = float(row["longitude"].replace(",", "."))
                latitude = float(row["latitude"].replace(",", "."))
            except ValueError:
                counts["invalid"] += 1
                continue

            if (
                not site_id
                or not operator
                or not isfinite(longitude)
                or not isfinite(latitude)
            ):
                counts["invalid"] += 1
                continue
            if not (west <= longitude <= east and south <= latitude <= north):
                counts["outside"] += 1
                continue

            station = {
                "site_id": site_id,
                "operator": operator,
                "longitude": longitude,
                "latitude": latitude,
                "supported_technologies": row.get("supported_technologies", "").strip(),
                "frequency_bands_mhz": row.get("frequency_bands_mhz", "").strip(),
            }
            identity = (operator.casefold(), site_id)
            groups.setdefault(identity, []).append(station)
    return groups


def classify_stations(groups, counts):
    stations = []
    for group in groups.values():
        station = group[0].copy()
        positions = {(row["longitude"], row["latitude"]) for row in group}
        if len(positions) > 1:
            counts["conflicting_positions"] += 1
            continue
        counts["duplicates_merged"] += len(group) - 1

        raw_labels = list(
            dict.fromkeys(
                row["supported_technologies"]
                for row in group
                if row["supported_technologies"]
            )
        )
        band_ranges = list(
            dict.fromkeys(
                band.strip()
                for row in group
                for band in row["frequency_bands_mhz"].split(";")
                if band.strip()
            )
        )
        station["supported_technologies"] = " | ".join(raw_labels)
        station["frequency_bands_mhz"] = "; ".join(band_ranges)

        reported = [reported_generations(label) for label in raw_labels]
        available_labels = [labels for labels in reported if labels is not None]
        if available_labels:
            generations = set().union(*available_labels)
            source = "explicit_api"
        else:
            generations = infer_generations(station["frequency_bands_mhz"])
            source = "frequency_inferred"

        target_labels = [
            generation for generation in ("4G", "5G") if generation in generations
        ]
        if not target_labels:
            counts["unclassified_or_non_target"] += 1
            continue

        station["record_id"] = f"{station['operator']} - {station['site_id']}"
        station["technology"] = "+".join(target_labels)
        station["technology_source"] = source
        station["is_synthetic"] = False
        stations.append(station)

    return sorted(stations, key=lambda station: station["record_id"])


def generate_synthetic_rrhs(count, seed):
    west, south, east, north = STUDY_BOUNDS
    rng = random.Random(seed)
    for index in range(1, count + 1):
        site_id = f"SYNTH_{index:05d}"
        yield {
            "record_id": site_id,
            "site_id": site_id,
            "longitude": rng.uniform(west, east),
            "latitude": rng.uniform(south, north),
            "operator": "synthetic",
            "technology": "unknown",
            "technology_source": "synthetic",
            "supported_technologies": "",
            "frequency_bands_mhz": "",
            "is_synthetic": True,
        }


def write_csv(stations, output_path):
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8-sig",
            newline="",
            dir=output_path.parent,
            prefix="stations_for_simulation_",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            writer = csv.DictWriter(handle, fieldnames=OUTPUT_COLUMNS)
            writer.writeheader()
            writer.writerows(stations)
        temporary_path.replace(output_path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "input",
        nargs="?",
        type=Path,
        default=Path(__file__).resolve().parent / "dataset" / "stations_raw.csv",
    )
    parser.add_argument("--output", type=Path, help="Path for the processed CSV")
    parser.add_argument(
        "--synthetic",
        type=int,
        default=DEFAULT_SYNTHETIC_RRHS,
        help=f"Number of synthetic RRHs to add (default: {DEFAULT_SYNTHETIC_RRHS})",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=DEFAULT_SEED,
        help=f"Random seed for synthetic placement (default: {DEFAULT_SEED})",
    )
    args = parser.parse_args()
    if args.synthetic < 0:
        parser.error("--synthetic must be zero or greater.")
    output_path = args.output or args.input.with_name("stations_for_simulation.csv")
    if args.input.resolve() == output_path.resolve():
        parser.error("Choose an output path different from the raw CSV.")

    counts = Counter()
    try:
        groups = read_stations(args.input, counts)
        stations = classify_stations(groups, counts)
        synthetic = list(generate_synthetic_rrhs(args.synthetic, args.seed))
        write_csv(stations + synthetic, output_path)
    except (OSError, ValueError, csv.Error) as error:
        parser.exit(1, f"Processing failed. {error}\n")

    print(
        f"Read {counts['read']} raw rows and saved {len(stations)} real station records."
    )
    print(f"Synthetic RRHs added {len(synthetic)}")
    print(f"Invalid rows skipped {counts['invalid']}")
    print(f"Rows outside the scenario skipped {counts['outside']}")
    print(f"Duplicate rows merged {counts['duplicates_merged']}")
    print(
        f"Station IDs with conflicting coordinates excluded {counts['conflicting_positions']}"
    )
    print(
        f"Stations without a usable 4G or 5G label excluded {counts['unclassified_or_non_target']}"
    )
    by_technology = Counter(station["technology"] for station in stations)
    for technology in ("4G", "5G", "4G+5G"):
        print(f"{technology} records {by_technology[technology]}")
    inferred = sum(
        station["technology_source"] == "frequency_inferred" for station in stations
    )
    print(f"Frequency-inferred records {inferred}")
    print(f"Saved {output_path.resolve()}")


if __name__ == "__main__":
    main()
