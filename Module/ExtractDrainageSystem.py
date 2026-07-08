"""
Extract Drainage System
------------------------
For each dam in an input CSV, snap to the nearest HydroRIVERS v10 river reach
and traverse the network upstream (via NEXT_DOWN connectivity, reversed) to
extract the full upstream drainage system (all contributing river reaches).

Output: one GeoPackage per dam, named "{Dam_ID}_DrainageSystem.gpkg", written
to Plot/{csv_stem}/ ; plus a QC summary CSV in Output/{csv_stem}_output.csv.

Expected input CSV columns: Dam ID, Dam name, Latitude, Longitude, Area_km2
"""

import os
from pathlib import Path

import pandas as pd
import geopandas as gpd
from shapely.geometry import Point

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

HYDRORIVERS_DIR = Path(
    "/Users/filou/MyProjects/MyGISProjects/Resources/Maps and data/HydroRIVERS/HydroRIVERS_v10_shp"
)

DEFAULT_SNAP_RADIUS = 1000   # metres -- distances beyond this get flagged for review
HARD_SNAP_CUTOFF = 2000      # metres -- hard cutoff, no snap beyond this
SAVE_EVERY = 10

# Metric CRS used purely for distance measurement (search radius, snap
# distance). HydroRIVERS ships in geographic WGS84 (EPSG:4326); degrees are
# not a reliable distance unit. Output geometry stays in the original CRS.
# Matches the approach used in ExtractDownstreamRiverCourse.py.
METRIC_CRS = "EPSG:4087"  # World Equidistant Cylindrical

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "Data"
OUTPUT_DIR = PROJECT_ROOT / "Output"
PLOT_DIR = PROJECT_ROOT / "Plot"


# ---------------------------------------------------------------------------
# Input file selection (tkinter dialog, typed fallback)
# ---------------------------------------------------------------------------

def select_input_csv() -> Path:
    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        path_str = filedialog.askopenfilename(
            title="Select dam input CSV",
            initialdir=str(DATA_DIR if DATA_DIR.exists() else PROJECT_ROOT),
            filetypes=[("CSV files", "*.csv")],
        )
        root.destroy()
        if not path_str:
            raise RuntimeError("No file selected.")
        return Path(path_str)
    except Exception:
        typed = input("Enter path to dam input CSV: ").strip()
        return Path(typed)


# ---------------------------------------------------------------------------
# HydroRIVERS discovery
# ---------------------------------------------------------------------------

def find_hydrorivers_shp() -> Path:
    candidates = sorted(HYDRORIVERS_DIR.glob("*.shp"))
    if not candidates:
        raise FileNotFoundError(f"No .shp file found in {HYDRORIVERS_DIR}")
    # Prefer the seamless global file over regional tiles, if present
    global_candidates = [c for c in candidates if c.stem.lower() == "hydrorivers_v10"]
    return global_candidates[0] if global_candidates else candidates[0]


# ---------------------------------------------------------------------------
# Load the full river network ONCE (geometry + attributes) and build both
# the reverse connectivity graph and a metric-CRS copy for snapping.
#
# Loading everything once and filtering in-memory (via .isin()) — rather
# than re-querying the shapefile per dam with a GDAL "where" clause — avoids
# pushing large HYRIV_ID IN (...) lists down to GDAL's shapefile SQL engine,
# which has practical limits on expression length/complexity and fails with
# an "Invalid SQL query for layer ..." error once the upstream ID list gets
# large (e.g. a dam far downstream on a major river with many contributing
# tributaries). This mirrors the working pattern already used in
# ExtractBasinAtlasAttributesAtDamSite.py (basins[basins["HYBAS_ID"].isin(...)]).
# ---------------------------------------------------------------------------

def load_river_network(shp_path: Path):
    """Read the full HydroRIVERS layer once. Returns (rivers, rivers_metric,
    reverse_graph)."""
    old_cwd = os.getcwd()
    try:
        os.chdir(shp_path.parent)  # GDAL/Fiona macOS path-stripping workaround
        rivers = gpd.read_file(shp_path.name)
    finally:
        os.chdir(old_cwd)

    required_cols = ["HYRIV_ID", "NEXT_DOWN"]
    missing = [c for c in required_cols if c not in rivers.columns]
    if missing:
        raise ValueError(f"River network is missing expected columns: {missing}")

    if rivers.crs is None:
        raise ValueError("River network has no CRS defined.")

    rivers_metric = rivers.to_crs(METRIC_CRS)

    reverse_graph: dict = {}
    for hyriv_id, next_down in zip(
        rivers["HYRIV_ID"].to_numpy(), rivers["NEXT_DOWN"].to_numpy()
    ):
        if next_down == 0:
            continue
        reverse_graph.setdefault(next_down, []).append(hyriv_id)

    return rivers, rivers_metric, reverse_graph


# ---------------------------------------------------------------------------
# Snapping: find the nearest river reach to a dam point
# ---------------------------------------------------------------------------

def snap_to_nearest_reach(rivers: gpd.GeoDataFrame, rivers_metric: gpd.GeoDataFrame,
                           lon: float, lat: float, radius_m: float = HARD_SNAP_CUTOFF):
    """Return (HYRIV_ID, snap_distance_m) for the nearest reach within
    radius_m, or (None, None) if nothing is found within that radius.
    Uses the spatial index on the metric-CRS copy for fast candidate lookup,
    then measures true planar distance in metres."""
    point_wgs84 = gpd.GeoSeries([Point(lon, lat)], crs="EPSG:4326")
    point_metric = point_wgs84.to_crs(rivers_metric.crs).iloc[0]

    search_area = point_metric.buffer(radius_m)
    candidate_positions = list(
        rivers_metric.sindex.query(search_area, predicate="intersects")
    )

    if not candidate_positions:
        return None, None

    candidates = rivers_metric.iloc[candidate_positions]
    distances = candidates.geometry.distance(point_metric)
    best_pos_in_candidates = distances.values.argmin()
    best_position = candidate_positions[best_pos_in_candidates]

    nearest_reach = rivers.iloc[best_position]
    snap_distance_m = distances.iloc[best_pos_in_candidates]

    if snap_distance_m > radius_m:
        return None, None

    return int(nearest_reach["HYRIV_ID"]), float(snap_distance_m)


# ---------------------------------------------------------------------------
# Upstream traversal (BFS) + geometry export
# ---------------------------------------------------------------------------

def collect_upstream_ids(reverse_graph: dict, start_id: int) -> list:
    visited = {start_id}
    queue = [start_id]
    while queue:
        current = queue.pop()
        for child in reverse_graph.get(current, []):
            if child not in visited:
                visited.add(child)
                queue.append(child)
    return list(visited)


def export_upstream_gpkg(rivers: gpd.GeoDataFrame, upstream_ids: list, out_path: Path):
    """Filter the already-loaded river GeoDataFrame in-memory and write the
    subset to a GeoPackage. No per-dam disk re-read, no GDAL "where" clause."""
    gdf = rivers[rivers["HYRIV_ID"].isin(upstream_ids)].copy()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    gdf.to_file(out_path, driver="GPKG")
    return gdf


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    csv_path = select_input_csv()
    csv_stem = csv_path.stem
    df = pd.read_csv(csv_path)

    required_cols = {"Dam ID", "Dam name", "Latitude", "Longitude", "Area_km2"}
    missing = required_cols - set(df.columns)
    if missing:
        raise ValueError(f"Input CSV is missing required columns: {missing}")

    for col in ("QC_flag", "Snap_distance_m", "N_upstream_reaches"):
        if col not in df.columns:
            df[col] = pd.NA

    shp_path = find_hydrorivers_shp()
    print(f"Using HydroRIVERS file: {shp_path}")
    print("Loading river network and building upstream connectivity graph "
          "(one-time, whole network)...")
    rivers, rivers_metric, reverse_graph = load_river_network(shp_path)
    print(f"Loaded {len(rivers):,} reaches; "
          f"{len(reverse_graph):,} downstream nodes with upstream contributors.")

    out_subdir = PLOT_DIR / csv_stem
    output_csv_path = OUTPUT_DIR / f"{csv_stem}_output.csv"

    for i, row in df.iterrows():
        dam_id = row["Dam ID"]

        if pd.notna(row.get("QC_flag")) and row["QC_flag"] != "":
            continue  # already processed, skip on rerun

        lat, lon = row["Latitude"], row["Longitude"]
        try:
            snapped_id, snap_dist = snap_to_nearest_reach(rivers, rivers_metric, lon, lat)
            if snapped_id is None:
                df.at[i, "QC_flag"] = "NO_SNAP"
                continue

            upstream_ids = collect_upstream_ids(reverse_graph, snapped_id)
            out_gpkg = out_subdir / f"{dam_id}_DrainageSystem.gpkg"
            export_upstream_gpkg(rivers, upstream_ids, out_gpkg)

            df.at[i, "QC_flag"] = "OK"
            df.at[i, "Snap_distance_m"] = round(snap_dist, 1)
            df.at[i, "N_upstream_reaches"] = len(upstream_ids)

            flag = "" if snap_dist <= DEFAULT_SNAP_RADIUS else " <-- large snap distance, verify in QGIS"
            print(f"Dam {dam_id}: snapped {snap_dist:.0f} m, {len(upstream_ids)} reaches{flag}")

        except Exception as exc:
            df.at[i, "QC_flag"] = "ERROR"
            print(f"Dam {dam_id}: FAILED ({exc})")

        if (i + 1) % SAVE_EVERY == 0:
            OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            df.to_csv(output_csv_path, index=False)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_csv_path, index=False)
    print(f"\nDone. Summary written to {output_csv_path}")


if __name__ == "__main__":
    main()