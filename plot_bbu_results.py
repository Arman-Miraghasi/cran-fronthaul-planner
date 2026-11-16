"""Plot minimum-delay assignments or the earlier distance-only comparison."""

import argparse
import csv
from collections import defaultdict
from pathlib import Path
from tempfile import TemporaryDirectory

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.collections import LineCollection
from matplotlib.colors import Normalize
from matplotlib.patches import Rectangle
from matplotlib.ticker import MaxNLocator, PercentFormatter, ScalarFormatter

from select_bbu_pools import K_SEARCH_FILE, POOL_CATEGORIES, read_pool_categories
from settings import (
    ASSIGNMENTS_FILE,
    CHART_FIGSIZE,
    COMPARISON_FILE,
    DISTANCE_DISTRIBUTION_FIGURE,
    DISTANCE_SUMMARY_FIGURE,
    FIGURE_DPI,
    FIGURES_SUBDIR,
    MAP_ASPECT,
    MAP_MIN_PADDING_DEGREES,
    MAP_PADDING_FRACTION,
    MIN_DELAY_RESULTS_DIR,
    PLACEMENT_FIGURE_TEMPLATE,
    POOL_COLOR,
    POOL_MARKER_SIZE,
    POOLS_FILE,
    REAL_COLOR,
    REAL_RRH_SIZE,
    RESULTS_DIR,
    RRHS_FILE,
    SCENARIO_FIGSIZE,
    SCENARIO_FIGURE,
    STUDY_BOUNDS,
    SYNTHETIC_COLOR,
    SYNTHETIC_RRH_SIZE,
)

UNSERVED_COLOR = "#b42318"
TRAFFIC_COLORS = {
    "residential": REAL_COLOR,
    "office": SYNTHETIC_COLOR,
    "mixed": "#16845b",
}
DELAY_STAGES = ("initial", "expanded", "final")
CATEGORY_LABELS = (
    "Only feasible pool",
    "Preferred with alternatives",
    "Backup",
)


def read_rows(path):
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def group_rows(rows, key):
    grouped = defaultdict(list)
    for row in rows:
        grouped[row[key]].append(row)
    return grouped


def point(row):
    return float(row["longitude"]), float(row["latitude"])


def style_axes(ax):
    ax.set_axisbelow(True)
    ax.grid(color="#e4ebf1", linewidth=0.75)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)


def save_figure(fig, path):
    try:
        fig.savefig(path, dpi=FIGURE_DPI, facecolor="white")
    finally:
        plt.close(fig)


def scatter_locations(ax, rows, **style):
    coordinates = [point(row) for row in rows]
    if coordinates:
        longitudes, latitudes = zip(*coordinates)
        ax.scatter(longitudes, latitudes, **style)


def setup_scenario_map(rrhs):
    fig, ax = plt.subplots(figsize=SCENARIO_FIGSIZE)
    fig.subplots_adjust(left=0.09, right=0.97, bottom=0.17, top=0.87)
    style_axes(ax)

    coords = {record_id: point(row) for record_id, row in rrhs.items()}
    west, south, east, north = STUDY_BOUNDS
    if all(
        west <= lon <= east and south <= lat <= north for lon, lat in coords.values()
    ):
        ax.add_patch(
            Rectangle(
                (west, south),
                east - west,
                north - south,
                fill=False,
                linestyle="--",
                linewidth=1,
                edgecolor="#9eabb5",
                zorder=1,
            )
        )
    else:
        longitudes, latitudes = zip(*coords.values())
        west, east = min(longitudes), max(longitudes)
        south, north = min(latitudes), max(latitudes)

    pad_x = max(MAP_PADDING_FRACTION * (east - west), MAP_MIN_PADDING_DEGREES)
    pad_y = max(MAP_PADDING_FRACTION * (north - south), MAP_MIN_PADDING_DEGREES)
    ax.set_xlim(west - pad_x, east + pad_x)
    ax.set_ylim(south - pad_y, north + pad_y)
    ax.set_aspect(MAP_ASPECT)
    for axis in (ax.xaxis, ax.yaxis):
        axis.set_major_locator(MaxNLocator(nbins=5))
        axis.set_major_formatter(ScalarFormatter(useOffset=False))
    ax.set(xlabel="Longitude (°)", ylabel="Latitude (°)")
    return fig, ax, coords


def draw_assignment_links(ax, coords, pools, assignments, color, alpha):
    segments = [
        (coords[row["record_id"]], point(pools[row["pool_id"]])) for row in assignments
    ]
    if segments:
        ax.add_collection(
            LineCollection(
                segments, colors=color, linewidths=0.7, alpha=alpha, zorder=2
            )
        )


def read_min_delay_results(results_dir, summary):
    rrhs = {row["record_id"]: row for row in read_rows(results_dir / RRHS_FILE)}
    catalog = {row["pool_id"]: row for row in read_rows(results_dir / POOLS_FILE)}
    summaries = {row["stage"]: row for row in summary}
    stages = [name for name in DELAY_STAGES if name in summaries]
    assignments = group_rows(read_rows(results_dir / ASSIGNMENTS_FILE), "stage")
    categories = group_rows(read_pool_categories(results_dir), "stage")

    if (
        not rrhs
        or len(stages) != len(summary)
        or not {"initial", "final"}.issubset(summaries)
        or set(stages) != assignments.keys()
        or set(stages) != categories.keys()
    ):
        raise ValueError("Minimum-delay CSVs are inconsistent. Rerun pool selection.")

    active = {
        "initial": {
            key: row for key, row in catalog.items() if row["is_initial"] == "True"
        },
        "expanded": catalog,
        "final": {
            key: row for key, row in catalog.items() if row["is_active_final"] == "True"
        },
    }

    for stage in stages:
        rows = assignments[stage]
        category_rows = categories[stage]
        pools = active[stage]
        served = sum(row["status"] == "served" for row in rows)
        if (
            len(rows) != len(rrhs)
            or {row["record_id"] for row in rows} != rrhs.keys()
            or len(rrhs) != int(summaries[stage]["rrh_count"])
            or served != int(summaries[stage]["served_count"])
            or len(rows) - served != int(summaries[stage]["unserved_count"])
            or len(pools) != int(summaries[stage]["k"])
            or len(category_rows) != len(pools)
            or {row["pool_id"] for row in category_rows} != pools.keys()
        ):
            raise ValueError(f"Minimum-delay CSVs disagree for the {stage} stage.")

        for row in rows:
            option_count = int(row["feasible_pool_count"])
            if row["status"] == "served":
                valid = row["pool_id"] in pools and 0 < option_count <= len(pools)
            else:
                valid = (
                    row["status"] == "unserved"
                    and not row["pool_id"]
                    and option_count == 0
                )
            if not valid:
                raise ValueError(f"Invalid {stage} assignment for {row['record_id']}.")

    return rrhs, catalog, summaries, stages, active, assignments, categories


def plot_min_delay_map(path, rrhs, pool_colors, pools, assignments, summary):
    fig, ax, coords = setup_scenario_map(rrhs)

    served = [row for row in assignments if row["status"] == "served"]
    draw_assignment_links(
        ax,
        coords,
        pools,
        served,
        color=[pool_colors[row["pool_id"]] for row in served],
        alpha=0.35,
    )

    for profile, color in TRAFFIC_COLORS.items():
        scatter_locations(
            ax,
            [row for row in rrhs.values() if row["traffic_profile"] == profile],
            s=REAL_RRH_SIZE,
            color=color,
            alpha=0.78,
            linewidths=0,
            zorder=3,
            label=profile.capitalize(),
        )

    unserved = [
        rrhs[row["record_id"]] for row in assignments if row["status"] == "unserved"
    ]
    scatter_locations(
        ax,
        unserved,
        s=45,
        marker="x",
        color=UNSERVED_COLOR,
        linewidths=1.2,
        zorder=4,
        label=f"Unserved ({len(unserved)})",
    )

    for index, (pool_id, pool) in enumerate(pools.items()):
        scatter_locations(
            ax,
            [pool],
            s=POOL_MARKER_SIZE,
            marker="*",
            color=pool_colors[pool_id],
            edgecolors="#202020",
            linewidths=0.8,
            zorder=5,
            label="Active BBU pool" if index == 0 else None,
        )
        ax.annotate(
            pool_id,
            point(pool),
            xytext=(5, 4),
            textcoords="offset points",
            fontsize=7,
            zorder=6,
        )

    stage = summary["stage"].capitalize()
    fig.suptitle(f"{stage} minimum-delay assignments", y=0.972, fontweight="bold")
    fig.text(
        0.5,
        0.916,
        f"{len(pools)} active pools  |  {summary['served_count']}/{len(rrhs)} RRHs served",
        ha="center",
    )
    fig.legend(loc="lower center", bbox_to_anchor=(0.5, 0.022), ncol=5, frameon=False)
    save_figure(fig, path)


def draw_cdf(ax, values, right, label, color=None):
    distances = sorted(values)
    shares = [100 * rank / len(distances) for rank in range(1, len(distances) + 1)]
    ax.step(
        [0, *distances, right],
        [0, *shares, 100],
        where="post",
        linewidth=1.7,
        label=label,
        color=color,
    )


def style_cdf(ax, right):
    style_axes(ax)
    ax.set(
        title="Cumulative distribution of nearest-pool distances",
        xlabel="Distance to nearest active pool (km)",
        ylabel="Share of all RRHs",
        xlim=(0, right),
        ylim=(0, 104),
    )
    ax.yaxis.set_major_formatter(PercentFormatter(100))


def plot_min_delay_cdf(path, assignments, summaries, stages):
    fig, ax = plt.subplots(figsize=CHART_FIGSIZE, constrained_layout=True)
    distances = {
        stage: [float(row["nearest_distance_km"]) for row in assignments[stage]]
        for stage in stages
    }
    distance_limit = float(summaries["initial"]["max_link_distance_km"])
    right = 1.04 * max(
        distance_limit, max(max(values) for values in distances.values())
    )
    style_cdf(ax, right)

    for stage in stages:
        draw_cdf(
            ax,
            distances[stage],
            right,
            label=f"{stage.capitalize()} (K={summaries[stage]['k']})",
        )
    ax.axvline(
        distance_limit,
        color="#444444",
        linestyle="--",
        label=f"{distance_limit:g} km link limit",
    )
    ax.legend(loc="lower right")
    save_figure(fig, path)


def plot_pool_categories(path, rows, stage):
    width = max(CHART_FIGSIZE[0], 0.36 * len(rows))
    fig, ax = plt.subplots(figsize=(width, CHART_FIGSIZE[1]), constrained_layout=True)
    style_axes(ax)

    positions = list(range(len(rows)))
    for offset, column, label in zip(
        (-0.25, 0, 0.25), POOL_CATEGORIES, CATEGORY_LABELS
    ):
        ax.bar(
            [position + offset for position in positions],
            [int(row[column]) for row in rows],
            width=0.25,
            label=label,
        )
    ax.set_xticks(positions, [row["pool_id"] for row in rows], rotation=60, ha="right")
    ax.set(
        title=f"{stage.capitalize()} feasible RRHs per BBU pool",
        xlabel="Active BBU pool",
        ylabel="Number of RRHs",
    )
    ax.yaxis.set_major_locator(MaxNLocator(integer=True))
    ax.legend()
    save_figure(fig, path)


def plot_rrh_options(path, rrhs, assignments, stage):
    fig, ax = plt.subplots(figsize=CHART_FIGSIZE, constrained_layout=True)
    style_axes(ax)

    ordered = sorted(assignments, key=lambda row: point(rrhs[row["record_id"]]))
    counts = [int(row["feasible_pool_count"]) for row in ordered]
    ax.scatter(
        range(len(ordered)),
        counts,
        s=10,
        c=[UNSERVED_COLOR if count == 0 else REAL_COLOR for count in counts],
    )

    last = len(ordered) - 1
    ticks = sorted({round(index * last / 5) for index in range(6)})
    ax.set_xticks(
        ticks,
        [ordered[index]["record_id"] for index in ticks],
        rotation=25,
        ha="right",
        fontsize=7,
    )
    ax.set(
        title=f"{stage.capitalize()} feasible pools per RRH",
        xlabel="RRHs ordered west to east (selected IDs shown)",
        ylabel="Number of feasible BBU pools",
        ylim=(-0.5, max(counts) + 0.5),
    )
    ax.yaxis.set_major_locator(MaxNLocator(integer=True))
    save_figure(fig, path)


def plot_coverage_summary(path, summaries, stages):
    fig, ax = plt.subplots(figsize=CHART_FIGSIZE, constrained_layout=True)
    style_axes(ax)

    labels = [f"{stage.capitalize()}\nK={summaries[stage]['k']}" for stage in stages]
    served = [int(summaries[stage]["served_count"]) for stage in stages]
    unserved = [int(summaries[stage]["unserved_count"]) for stage in stages]
    ax.bar(labels, served, label="Served", color=REAL_COLOR)
    ax.bar(labels, unserved, bottom=served, label="Unserved", color=UNSERVED_COLOR)

    for index, stage in enumerate(stages):
        percentage = float(summaries[stage]["coverage_percent"])
        ax.annotate(
            f"{percentage:.1f}%",
            (index, served[index] + unserved[index]),
            xytext=(0, 4),
            textcoords="offset points",
            ha="center",
        )

    ax.set_ylim(0, max(a + b for a, b in zip(served, unserved)) * 1.25)
    ax.set_title("RRH coverage by selection stage", pad=16)
    ax.set_ylabel("Number of RRHs")
    ax.yaxis.set_major_locator(MaxNLocator(integer=True))
    ax.legend(loc="lower right")
    save_figure(fig, path)


def plot_min_delay_results(results_dir, figures, summary):
    rrhs, catalog, summaries, stages, active, assignments, categories = (
        read_min_delay_results(results_dir, summary)
    )
    cmap = plt.get_cmap("gist_rainbow")
    pool_colors = {
        pool_id: cmap((index * 0.61803398875) % 1)
        for index, pool_id in enumerate(catalog)
    }

    plot_scenario(figures / SCENARIO_FIGURE, rrhs)
    history = read_rows(results_dir / K_SEARCH_FILE)
    if not history:
        raise ValueError("The K search is empty. Rerun pool selection.")
    plot_distance_summary(figures / DISTANCE_SUMMARY_FIGURE, history, summaries["final"])

    for stage in ("initial", "final"):
        plot_min_delay_map(
            figures / f"min_delay_assignments_{stage}.png",
            rrhs,
            pool_colors,
            active[stage],
            assignments[stage],
            summaries[stage],
        )
        plot_pool_categories(
            figures / f"min_delay_pool_categories_{stage}.png",
            categories[stage],
            stage,
        )
        plot_rrh_options(
            figures / f"min_delay_rrh_options_{stage}.png",
            rrhs,
            assignments[stage],
            stage,
        )

    plot_min_delay_cdf(
        figures / DISTANCE_DISTRIBUTION_FIGURE, assignments, summaries, stages
    )
    plot_coverage_summary(figures / "min_delay_coverage.png", summaries, stages)


def plot_scenario(path, rrhs, pools=None, assignments=(), summary=None):
    fig, ax, coords = setup_scenario_map(rrhs)
    if pools:
        draw_assignment_links(
            ax,
            coords,
            pools,
            assignments,
            color="#5e7589",
            alpha=0.28,
        )

    real = [row for row in rrhs.values() if row["is_synthetic"] == "False"]
    synthetic = [row for row in rrhs.values() if row["is_synthetic"] == "True"]
    scatter_locations(
        ax,
        real,
        s=REAL_RRH_SIZE,
        color=REAL_COLOR,
        linewidths=0,
        zorder=3,
        label=f"Real RRHs ({len(real)})",
    )
    scatter_locations(
        ax,
        synthetic,
        s=SYNTHETIC_RRH_SIZE,
        marker="D",
        facecolors="none",
        edgecolors=SYNTHETIC_COLOR,
        linewidths=0.95,
        zorder=4,
        label=f"Synthetic RRHs ({len(synthetic)})",
    )

    title = "RRH scenario without BBU pools"
    if pools:
        title = "RRH scenario with BBU pools"
        real_pools = [row for row in pools.values() if row["is_synthetic"] == "False"]
        synthetic_pools = [
            row for row in pools.values() if row["is_synthetic"] == "True"
        ]
        scatter_locations(
            ax,
            real_pools,
            s=POOL_MARKER_SIZE,
            marker="*",
            color=POOL_COLOR,
            edgecolors="white",
            linewidths=0.8,
            zorder=5,
            label="BBU on real RRH",
        )
        scatter_locations(
            ax,
            synthetic_pools,
            s=POOL_MARKER_SIZE,
            marker="*",
            facecolors="white",
            edgecolors=POOL_COLOR,
            linewidths=1.15,
            zorder=5,
            label="BBU on synthetic RRH",
        )
        maximum = float(summary["max_km"])
        fig.text(
            0.5,
            0.916,
            f"K = {summary['k']}  |  Maximum nearest-pool distance {maximum:.2f} km",
            ha="center",
            fontsize=11,
        )

    fig.suptitle(title, y=0.972, fontweight="bold", fontsize=15)
    fig.legend(loc="lower center", bbox_to_anchor=(0.5, 0.022), ncol=4, frameon=False)
    save_figure(fig, path)


def plot_distance_summary(path, summary, limits=None):
    fig, ax = plt.subplots(figsize=CHART_FIGSIZE, constrained_layout=True)
    style_axes(ax)
    ks = [int(row["k"]) for row in summary]

    for column, label, color, marker in (
        ("max_km", "Maximum", POOL_COLOR, "o"),
        ("p95_km", "95th percentile", SYNTHETIC_COLOR, "^"),
        ("mean_km", "Mean", REAL_COLOR, "s"),
    ):
        ax.plot(
            ks,
            [float(row[column]) for row in summary],
            label=label,
            color=color,
            marker=marker,
            linewidth=1.8,
        )

    if limits is not None:
        distance_limit = float(limits["max_link_distance_km"])
        delay_limit = float(limits["max_round_trip_delay_us"]) * float(limits["propagation_speed_m_per_s"]) / 2e9
        ax.axhline(min(distance_limit, delay_limit), color="#444444", linestyle="--", label="Effective link limit")
        chosen_k = int(limits["k"])
        ax.axvline(chosen_k, color="#777777", linestyle=":", label=f"Chosen K = {chosen_k}")

    if len(ks) <= 12:
        ax.set_xticks(ks)
    else:
        ax.xaxis.set_major_locator(MaxNLocator(integer=True))
    ax.set(
        title="Nearest-pool distance by number of BBU pools",
        xlabel="Number of BBU pools (K)",
        ylabel="Distance to nearest pool (km)",
        ylim=(0, None),
    )
    ax.legend(frameon=False)
    save_figure(fig, path)


def plot_distance_distribution(path, assignments, ks):
    fig, ax = plt.subplots(figsize=CHART_FIGSIZE, constrained_layout=True)
    distances = {
        k: [float(row["distance_km"]) for row in assignments[str(k)]] for k in ks
    }
    right = 1.04 * max(max(values) for values in distances.values()) or 1.0
    style_cdf(ax, right)

    many = len(ks) > 10
    cmap = plt.get_cmap("viridis" if many else "tab10")
    scale = Normalize(vmin=ks[0], vmax=ks[-1])
    for index, (k, values) in enumerate(distances.items()):
        draw_cdf(
            ax, values, right, label=f"K = {k}", color=cmap(scale(k) if many else index)
        )

    if many:
        fig.colorbar(
            plt.cm.ScalarMappable(norm=scale, cmap=cmap),
            ax=ax,
            label="BBU pools (K)",
        )
    else:
        ax.legend(loc="lower right", ncol=2 if len(ks) > 5 else 1)
    save_figure(fig, path)


def plot_legacy_results(results_dir, figures, summary):
    rrhs = {row["record_id"]: row for row in read_rows(results_dir / RRHS_FILE)}
    pools = group_rows(read_rows(results_dir / POOLS_FILE), "k")
    assignments = group_rows(read_rows(results_dir / ASSIGNMENTS_FILE), "k")
    summary = sorted(summary, key=lambda row: int(row["k"]))
    tested = {row["k"] for row in summary}

    if not rrhs or tested != pools.keys() or tested != assignments.keys():
        raise ValueError("The results files are inconsistent. Rerun pool selection.")

    plot_scenario(figures / SCENARIO_FIGURE, rrhs)
    for row in summary:
        key = row["k"]
        k = int(key)
        active = {pool["pool_id"]: pool for pool in pools[key]}
        assigned = assignments[key]
        if (
            len(active) != k
            or len(assigned) != len(rrhs)
            or len(rrhs) != int(row["rrh_count"])
            or {item["record_id"] for item in assigned} != rrhs.keys()
            or any(item["pool_id"] not in active for item in assigned)
        ):
            raise ValueError(f"The results files are inconsistent for K={k}.")

        plot_scenario(
            figures / PLACEMENT_FIGURE_TEMPLATE.format(k=k),
            rrhs,
            active,
            assigned,
            row,
        )

    ks = [int(row["k"]) for row in summary]
    plot_distance_summary(figures / DISTANCE_SUMMARY_FIGURE, summary)
    plot_distance_distribution(figures / DISTANCE_DISTRIBUTION_FIGURE, assignments, ks)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--results-dir", type=Path, help="Override the results directory."
    )
    parser.add_argument(
        "--figures-dir", type=Path, help="Override the figure directory."
    )
    args = parser.parse_args()

    results_dir = args.results_dir or MIN_DELAY_RESULTS_DIR
    if (
        args.results_dir is None
        and not (results_dir / COMPARISON_FILE).is_file()
        and (RESULTS_DIR / COMPARISON_FILE).is_file()
    ):
        results_dir = RESULTS_DIR

    figures = args.figures_dir or results_dir / FIGURES_SUBDIR
    try:
        summary = read_rows(results_dir / COMPARISON_FILE)
        if not summary:
            raise ValueError("The comparison CSV is empty. Rerun pool selection.")

        figures.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=figures) as temp:
            staging = Path(temp)
            if "stage" in summary[0]:
                plot_min_delay_results(results_dir, staging, summary)
            else:
                plot_legacy_results(results_dir, staging, summary)

            generated = list(staging.iterdir())
            for path in generated:
                path.replace(figures / path.name)
    except (OSError, KeyError, ValueError, csv.Error) as error:
        parser.exit(1, f"Plotting failed. {error}\n")

    print(f"Saved {len(generated)} figures from {results_dir} to {figures.resolve()}")


if __name__ == "__main__":
    main()
