"""Independently audit generated phase CSVs; run after the full pipeline."""

import argparse
import csv
from collections import Counter, defaultdict
import json
import math
from pathlib import Path
import sys

from openpyxl import load_workbook

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import settings
from select_bbu_pools import read_pool_categories


def rows(path):
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def separation(a, b):
    lon1, lat1, lon2, lat2 = map(math.radians, (
        float(a["longitude"]), float(a["latitude"]),
        float(b["longitude"]), float(b["latitude"]),
    ))
    h = math.sin((lat2-lat1)/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin((lon2-lon1)/2)**2
    return 2 * settings.EARTH_RADIUS_KM * math.atan2(math.sqrt(h), math.sqrt(max(0, 1-h)))


def audit(directory=settings.MIN_DELAY_RESULTS_DIR, destination=None):
    rrhs = {row["record_id"]: row for row in rows(directory / settings.RRHS_FILE)}
    pools = {row["pool_id"]: row for row in rows(directory / settings.POOLS_FILE)}
    assignments = rows(directory / settings.ASSIGNMENTS_FILE)
    links = rows(directory / "feasible_links.csv")
    categories = read_pool_categories(directory)
    summary = rows(directory / settings.COMPARISON_FILE)
    checks = 0

    def require(condition, message):
        nonlocal checks
        checks += 1
        if not condition:
            raise AssertionError(message)

    processed = rows(settings.PROCESSED_STATIONS_FILE)
    require(len(processed) == len(rrhs), "Processed/scenario RRH count")
    require(sum(row["is_synthetic"] == "True" for row in processed) == settings.DEFAULT_SYNTHETIC_RRHS, "Configured synthetic count")
    west, south, east, north = settings.STUDY_BOUNDS
    for row in processed:
        require(west <= float(row["longitude"]) <= east and south <= float(row["latitude"]) <= north, "RRH within unchanged study bounds")
        require(row["traffic_profile"] in settings.TRAFFIC_PROFILE_TYPES, "Valid traffic profile")
        require(rrhs[row["record_id"]]["traffic_profile"] == row["traffic_profile"], "Traffic profile preserved in scenario")
    stages = {}
    for item in summary:
        stage = item["stage"]
        active = {key: pool for key, pool in pools.items() if
                  stage == "expanded" or pool["is_initial" if stage == "initial" else "is_active_final"] == "True"}
        assigned = [row for row in assignments if row["stage"] == stage]
        require(len(assigned) == len(rrhs) and {row["record_id"] for row in assigned} == rrhs.keys(), f"{stage}: RRH records")
        expected_links = {}
        expected_categories = defaultdict(Counter)
        distances = []
        delays = []
        for row in assigned:
            rrh = rrhs[row["record_id"]]
            measured = sorted((separation(rrh, pool), key) for key, pool in active.items())
            options = [(d, key) for d, key in measured if
                       d <= settings.MAX_LINK_DISTANCE_KM and
                       2*d*1e9/settings.PROPAGATION_SPEED_M_PER_S < settings.MAX_ROUND_TRIP_DELAY_US]
            distances.append(measured[0][0])
            require(math.isclose(float(row["nearest_distance_km"]), measured[0][0], abs_tol=1e-8), f"{stage}: nearest distance")
            require(int(row["feasible_pool_count"]) == len(options), f"{stage}: option count")
            require(set(filter(None, row["feasible_pool_ids"].split(";"))) == {key for _, key in options}, f"{stage}: option list")
            require((row["status"] == "served") == bool(options), f"{stage}: served flag")
            if options:
                d, key = options[0]
                delay = 2*d*1e9/settings.PROPAGATION_SPEED_M_PER_S
                require(row["pool_id"] == key, f"{stage}: lowest-delay pool")
                require(math.isclose(float(row["distance_km"]), d, abs_tol=1e-8), f"{stage}: assigned distance")
                require(math.isclose(float(row["round_trip_delay_us"]), delay, abs_tol=1e-7), f"{stage}: RTT")
                delays.append(delay)
            else:
                require(not row["pool_id"] and not row["distance_km"] and not row["round_trip_delay_us"], f"{stage}: unserved blanks")
            for index, (d, key) in enumerate(options):
                category = "backup" if index else "only_option" if len(options) == 1 else "preferred_with_alternatives"
                expected_links[(row["record_id"], key)] = (category, d)
                expected_categories[key][category] += 1
        actual = [row for row in links if row["stage"] == stage]
        require(len(actual) == len(expected_links), f"{stage}: feasible link count")
        require({(row["record_id"], row["pool_id"]) for row in actual} == expected_links.keys(), f"{stage}: feasible link membership")
        for row in actual:
            category, d = expected_links[(row["record_id"], row["pool_id"])]
            require(row["category"] == category and math.isclose(float(row["distance_km"]), d, abs_tol=1e-8), f"{stage}: link category/distance")
        category_rows = [row for row in categories if row["stage"] == stage]
        require(len(category_rows) == len(active), f"{stage}: pool category rows")
        for row in category_rows:
            for category in ("only_option", "preferred_with_alternatives", "backup"):
                require(int(row[category]) == expected_categories[row["pool_id"]][category], f"{stage}: category total")
        require(int(item["k"]) == len(active), f"{stage}: active pool total")
        require(int(item["served_count"]) == len(delays), f"{stage}: served total")
        require(int(item["unserved_count"]) == len(rrhs)-len(delays), f"{stage}: unserved total")
        for key, expected in (("max_km", max(distances)), ("p95_km", sorted(distances)[math.ceil(.95*len(distances))-1]), ("mean_km", sum(distances)/len(distances)), ("coverage_percent", 100*len(delays)/len(rrhs)), ("max_served_rtt_us", max(delays))):
            require(math.isclose(float(item[key]), expected, abs_tol=1e-7), f"{stage}: {key}")
        if stage == "final":
            require(len(delays) == len(rrhs), "Final coverage must be complete")
        stages[stage] = item
    from select_bbu_pools import POOL_CATEGORY_LABELS, POOL_WORKBOOK_FILE
    book = load_workbook(directory / POOL_WORKBOOK_FILE)
    try:
        expected_summary = [
            (row["stage"], row["pool_id"], *(int(row[key]) for key in POOL_CATEGORY_LABELS),
             sum(int(row[key]) for key in POOL_CATEGORY_LABELS))
            for row in categories
        ]
        require(list(book["Summary"].values)[1:] == expected_summary, "Excel summary totals")
        require("Charts" not in book.sheetnames and all(not sheet._charts for sheet in book.worksheets), "Excel workbook contains tables without charts")
        link_lookup = {(row["pool_id"], row["stage"], row["record_id"]): row for row in links}
        assigned_lookup = {(row["stage"], row["record_id"]): row for row in assignments}
        exported = set()
        for pool in pools:
            require(pool in book.sheetnames, f"Excel worksheet for {pool}")
            for row in book[pool].iter_rows(min_row=2, values_only=True):
                stage, category, record_id = row[:3]
                key = (pool, stage, record_id)
                require(key in link_lookup and key not in exported, "Excel feasible RRH link membership")
                exported.add(key)
                link = link_lookup[key]
                assigned = assigned_lookup[(stage, record_id)]
                require(category == POOL_CATEGORY_LABELS[link["category"]], "Excel RRH category")
                require(row[3] == rrhs[record_id]["traffic_profile"], "Excel traffic profile")
                require(math.isclose(row[6], float(link["distance_km"]), abs_tol=1e-8) and math.isclose(row[7], float(link["round_trip_delay_us"]), abs_tol=1e-7), "Excel link distance/delay")
                require(row[8] == assigned["pool_id"] and row[9] == int(assigned["feasible_pool_count"]), "Excel optimal pool and option count")
                alternatives = ";".join(key for key in assigned["feasible_pool_ids"].split(";") if key != pool)
                require((row[10] or "") == alternatives and row[11] == assigned["feasible_pool_ids"], "Excel alternative pool IDs")
        require(exported == link_lookup.keys(), "Excel includes every feasible RRH link")
    finally:
        book.close()
    history = rows(directory / "k_search.csv")
    previous = None
    for step in history:
        active_ids = set(filter(None, step["active_pool_ids"].split(";")))
        require(len(active_ids) == int(step["k"]) and active_ids <= pools.keys(), "Search layout membership")
        accepted = step["accepted"] == "True"
        if previous is not None:
            changed = {step["changed_pool_id"]}
            if step["action"] == "add":
                require(active_ids == previous | changed and len(active_ids) == len(previous)+1, "One-pool addition")
            else:
                require(step["action"] == "remove" and changed <= previous and active_ids == previous-changed, "One-pool removal trial")
        distances = [min((separation(rrh, pools[key]) for key in active_ids), default=math.inf) for rrh in rrhs.values()]
        served = sum(d <= settings.MAX_LINK_DISTANCE_KM and 2*d*1e9/settings.PROPAGATION_SPEED_M_PER_S < settings.MAX_ROUND_TRIP_DELAY_US for d in distances)
        require(int(step["served_count"]) == served and int(step["unserved_count"]) == len(rrhs)-served, "Search coverage")
        for key, expected in (("max_km", max(distances)), ("p95_km", sorted(distances)[math.ceil(.95*len(distances))-1]), ("mean_km", sum(distances)/len(distances))):
            require(math.isclose(float(step[key]), expected, abs_tol=1e-7), f"Search {key}")
        require(accepted == (served == len(rrhs)), "Search feasibility flag")
        previous = active_ids
    feasible_steps = [step for step in history if step["accepted"] == "True"]
    optimum = min(feasible_steps, key=lambda step: int(step["k"]))
    require(set(optimum["active_pool_ids"].split(";")) == {key for key, pool in pools.items() if pool["is_active_final"] == "True"}, "Search final layout")
    tested_k = [int(step["k"]) for step in history]
    direction = -1 if history[0]["accepted"] == "True" else 1
    require(all(b-a == direction for a,b in zip(tested_k, tested_k[1:])), "Consecutive K values in a single direction")
    best_k = int(optimum["k"])
    if best_k > 1:
        require(any(int(step["k"]) == best_k-1 and step["accepted"] == "False" for step in history), "Infeasible K immediately below optimum")
    figures = directory / settings.FIGURES_SUBDIR
    expected_figures = {
        settings.SCENARIO_FIGURE, settings.DISTANCE_SUMMARY_FIGURE,
        settings.DISTANCE_DISTRIBUTION_FIGURE, "min_delay_coverage.png",
        *(f"min_delay_{kind}_{stage}.png" for kind in ("assignments", "pool_categories", "rrh_options") for stage in ("initial", "final")),
    }
    require({path.name for path in figures.glob("*.png")} == expected_figures, "Ten phase figures")
    report = {
        "checks_passed": checks,
        "raw_records": len(rows(settings.RAW_STATIONS_FILE)),
        "rrhs": len(rrhs),
        "synthetic_rrhs": sum(row["is_synthetic"] == "True" for row in rrhs.values()),
        "traffic_profiles": dict(Counter(row["traffic_profile"] for row in rrhs.values())),
        "candidate_sites_evaluated": len(pools),
        "accepted_k_values": [int(row["k"]) for row in history if row["accepted"] == "True"],
        "tested_k_values": tested_k,
        "minimum_feasible_k": best_k,
        "pool_workbook": str(directory / POOL_WORKBOOK_FILE),
        "rejected_removal_trials": sum(row["action"] == "remove" and row["accepted"] == "False" for row in history),
        "stages": stages,
        "figures": sorted(path.name for path in figures.glob("*.png")),
    }
    destination = destination or settings.RESULTS_DIR / "validation" / "audit.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, indent=2)+"\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-dir", type=Path, default=settings.MIN_DELAY_RESULTS_DIR)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    audit(args.results_dir, args.report)
