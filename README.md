# C-RAN Fronthaul Planning and BBU Pool Placement

This project compares the number and placement of baseband unit (BBU) pools for remote radio heads (RRHs) in the Barcelona metropolitan area. The study bounds are `(1.899402, 41.208051, 2.058360, 41.381086)` in west, south, east, north order.

![Study area around Castelldefels, Gavà, and Viladecans](scenario-map.png)

*Study area shown in the [Infoantenas map viewer](https://geoportal.minetur.gob.es/VCTEL/vcne.do).*

## Run the pipeline

Use Python 3.10 or later. Run these commands in PowerShell from the folder containing all four scripts. On macOS or Linux, use `python3` in place of `py -3`. Internet access is required for extraction, and Matplotlib is required for plotting.

```powershell
py -3 -m pip install -r requirements.txt
py -3 extract_infoantenas.py
py -3 process_stations.py
py -3 select_bbu_pools.py dataset/stations_for_simulation.csv --k 1 2 3 5 10
py -3 plot_bbu_results.py
```

| Step | What it does | Output |
| --- | --- | --- |
| Extract | Queries Spain's Infoantenas portal for stations and available assigned frequency bands. | `dataset/stations_raw.csv` |
| Process | Cleans and deduplicates station records, then retains those classified as 4G, 5G, or both. | `dataset/stations_for_simulation.csv` |
| Place pools | Tests each requested pool count with farthest-first k-center and assigns every RRH to its nearest selected pool. | Four CSVs in `results/` |
| Plot | Draws the RRH scenario without pools, a placement map for each K, and distance comparison and distribution charts. | PNGs in `results/figures/` |

## Configure and interpret

- **Synthetic RRHs** default to zero. Run `py -3 process_stations.py --synthetic 50 --seed 42` before placement to add 50 reproducible, uniformly sampled locations within the full study bounds. Synthetic RRHs are flagged and also participate in pool selection.
- **Pool counts** are set with `--k`. Each K must be a positive integer no larger than the number of distinct RRH coordinates. Use the same `--results-dir` for placement and plotting if you change the default.
- **Results** include `scenario_rrhs.csv` for RRH coordinates, `bbu_pools.csv` for selected locations, `nearest_pool_assignments.csv` for per-RRH assignments and distances, and `k_comparison.csv` for maximum, 95th-percentile, and mean distance by K. Start with the maximum distance to compare how the worst-served RRH changes as pools are added.
- **Model assumptions** include frequency-based estimates when technology labels are unavailable, flagged as `frequency_inferred`. Distances are great-circle distances in kilometres. Fibre routing, latency, and pool capacity are outside this model.
