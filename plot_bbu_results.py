"""Plot the RRH scenario and compare the BBU placements saved by select_bbu_pools.py."""

import argparse
import csv
from collections import defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.collections import LineCollection
from matplotlib.colors import Normalize
from matplotlib.patches import Rectangle
from matplotlib.ticker import MaxNLocator, PercentFormatter, ScalarFormatter

STUDY_BOUNDS = (1.815856, 41.186855, 2.133773, 41.532925)  # west, south, east, north
REAL_COLOR = "#166a98"
SYNTHETIC_COLOR = "#a75013"
POOL_COLOR = "#892c60"


def read_rows(path):
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def point(row):
    return float(row["longitude"]), float(row["latitude"])


def read_results(results_dir):
    rrhs = {
        row["record_id"]: row for row in read_rows(results_dir / "scenario_rrhs.csv")
    }
    pools = defaultdict(dict)
    for row in read_rows(results_dir / "bbu_pools.csv"):
        pools[int(row["k"])][row["pool_id"]] = row
    assignments = defaultdict(list)
    for row in read_rows(results_dir / "nearest_pool_assignments.csv"):
        assignments[int(row["k"])].append(row)
    summary = sorted(
        read_rows(results_dir / "k_comparison.csv"), key=lambda row: int(row["k"])
    )
    tested = {int(row["k"]) for row in summary}
    if not rrhs or not tested or tested != pools.keys() or tested != assignments.keys():
        raise ValueError(
            "The results files are empty or inconsistent. Run the placement script again."
        )
    for row in summary:
        k = int(row["k"])
        assigned = assignments[k]
        if (
            len(pools[k]) != k
            or len(assigned) != len(rrhs)
            or len(rrhs) != int(row["rrh_count"])
            or {item["record_id"] for item in assigned} != rrhs.keys()
            or any(item["pool_id"] not in pools[k] for item in assigned)
        ):
            raise ValueError(
                f"The results files are inconsistent for K={k}. Run placement again."
            )
    return rrhs, pools, assignments, summary


def scatter_locations(ax, rows, **style):
    coordinates = [point(row) for row in rows]
    if coordinates:
        longitudes, latitudes = zip(*coordinates)
        ax.scatter(longitudes, latitudes, **style)


def style_axes(ax):
    ax.set_axisbelow(True)
    ax.grid(color="#e4ebf1", linewidth=0.75)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)


def save_figure(fig, path):
    fig.savefig(path, dpi=200, facecolor="white")
    plt.close(fig)


def plot_scenario(path, rrhs, pools=None, assignments=(), summary=None):
    fig, ax = plt.subplots(figsize=(12, 6.5))
    # Use the same landscape panel for every scenario map.
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
    pad_x = max(0.06 * (east - west), 0.0001)
    pad_y = max(0.06 * (north - south), 0.0001)
    ax.set_xlim(west - pad_x, east + pad_x)
    ax.set_ylim(south - pad_y, north + pad_y)
    # Fill the panel with independent longitude and latitude display scales.
    ax.set_aspect("auto")
    for axis in (ax.xaxis, ax.yaxis):
        axis.set_major_locator(MaxNLocator(nbins=5))
        axis.set_major_formatter(ScalarFormatter(useOffset=False))
    ax.set(xlabel="Longitude (°)", ylabel="Latitude (°)")

    if pools:
        segments = [
            (coords[row["record_id"]], point(pools[row["pool_id"]]))
            for row in assignments
        ]
        ax.add_collection(
            LineCollection(
                segments, colors="#5e7589", linewidths=0.7, alpha=0.28, zorder=2
            )
        )
    real = [row for row in rrhs.values() if row["is_synthetic"] == "False"]
    synthetic = [row for row in rrhs.values() if row["is_synthetic"] == "True"]
    scatter_locations(
        ax,
        real,
        s=23,
        color=REAL_COLOR,
        linewidths=0,
        zorder=3,
        label=f"Real RRHs ({len(real)})",
    )
    scatter_locations(
        ax,
        synthetic,
        s=29,
        marker="D",
        facecolors="none",
        edgecolors=SYNTHETIC_COLOR,
        linewidths=0.95,
        zorder=4,
        label=f"Synthetic RRHs ({len(synthetic)})",
    )
    if pools:
        real_pools = [row for row in pools.values() if row["is_synthetic"] == "False"]
        synthetic_pools = [
            row for row in pools.values() if row["is_synthetic"] == "True"
        ]
        scatter_locations(
            ax,
            real_pools,
            s=148,
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
            s=148,
            marker="*",
            facecolors="white",
            edgecolors=POOL_COLOR,
            linewidths=1.15,
            zorder=5,
            label="BBU on synthetic RRH",
        )
        fig.text(
            0.5,
            0.916,
            f"K = {summary['k']}  |  Maximum nearest-pool distance {float(summary['max_km']):.2f} km",
            ha="center",
            fontsize=11,
        )
        title = "RRH scenario with BBU pools"
    else:
        title = "RRH scenario without BBU pools"
    fig.suptitle(title, y=0.972, fontweight="bold", fontsize=15)
    fig.legend(loc="lower center", bbox_to_anchor=(0.5, 0.022), ncol=4, frameon=False)
    save_figure(fig, path)


def plot_distance_summary(path, summary):
    fig, ax = plt.subplots(figsize=(8.2, 4.8), constrained_layout=True)
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
    if len(ks) <= 12:
        ax.set_xticks(ks)
    else:
        ax.xaxis.set_major_locator(MaxNLocator(integer=True))
    ax.set(
        xlabel="Number of BBU pools (K)",
        ylabel="Distance to nearest pool (km)",
        ylim=(0, None),
    )
    ax.set_title(
        "Nearest-pool distance by number of BBU pools", loc="left", fontweight="bold"
    )
    ax.legend(frameon=False)
    save_figure(fig, path)


def plot_distance_distribution(path, assignments, ks):
    fig, ax = plt.subplots(figsize=(8.2, 4.8), constrained_layout=True)
    style_axes(ax)
    distances = {
        k: sorted(float(row["distance_km"]) for row in assignments[k]) for k in ks
    }
    right = 1.04 * max(values[-1] for values in distances.values()) or 1.0
    many = len(ks) > 10
    colors = plt.get_cmap("viridis" if many else "tab10")
    scale = Normalize(vmin=ks[0], vmax=ks[-1])
    for i, (k, values) in enumerate(distances.items()):
        share = [100 * rank / len(values) for rank in range(1, len(values) + 1)]
        ax.step(
            [0, *values, right],
            [0, *share, 100],
            where="post",
            linewidth=1.65,
            color=colors(scale(k) if many else i),
            label=f"K = {k}",
        )
    ax.set(
        xlabel="Distance to nearest pool (km)",
        ylabel="RRHs within this distance",
        xlim=(0, right),
        ylim=(0, 104),
    )
    ax.yaxis.set_major_formatter(PercentFormatter(100))
    ax.set_title(
        "Cumulative distribution of RRH distances", loc="left", fontweight="bold"
    )
    if many:
        fig.colorbar(
            plt.cm.ScalarMappable(norm=scale, cmap=colors), ax=ax, label="BBU pools (K)"
        )
    else:
        ax.legend(loc="lower right", ncol=2 if len(ks) > 5 else 1, frameon=True)
    save_figure(fig, path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-dir", type=Path, default=Path("results"))
    args = parser.parse_args()
    try:
        rrhs, pools, assignments, summary = read_results(args.results_dir)
        figures = args.results_dir / "figures"
        figures.mkdir(exist_ok=True)
        plot_scenario(figures / "scenario_without_pools.png", rrhs)
        ks = []
        for row in summary:
            k = int(row["k"])
            ks.append(k)
            plot_scenario(
                figures / f"scenario_k_{k:02d}.png", rrhs, pools[k], assignments[k], row
            )
        plot_distance_summary(figures / "distance_vs_k.png", summary)
        plot_distance_distribution(
            figures / "distance_distribution.png", assignments, ks
        )
    except (OSError, KeyError, ValueError, csv.Error) as error:
        parser.exit(1, f"Plotting failed. {error}\n")
    print(f"Saved {len(ks) + 3} figures to {figures.resolve()}")


if __name__ == "__main__":
    main()
