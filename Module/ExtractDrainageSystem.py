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

import math

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
# Build the upstream connectivity graph once (geometry-free, fast)
# ---------------------------------------------------------------------------

def build_reverse_graph(shp_path: Path) -> dict:
    """Read only HYRIV_ID / NEXT_DOWN and build a reverse adjacency dict:
    {HYRIV_ID: [list of HYRIV_IDs whose NEXT_DOWN == HYRIV_ID]}."""
    old_cwd = os.getcwd()
    try:
        os.chdir(shp_path.parent)  # GDAL/Fiona macOS path-stripping workaround
        df = gpd.read_file(
            shp_path.name,
            columns=["HYRIV_ID", "NEXT_DOWN"],
            ignore_geometry=True,
        )
    finally:
        os.chdir(old_cwd)

    reverse_graph: dict = {}
    for hyriv_id, next_down in zip(df["HYRIV_ID"].to_numpy(), df["NEXT_DOWN"].to_numpy()):
        if next_down == 0:
            continue
        reverse_graph.setdefault(next_down, []).append(hyriv_id)
    return reverse_graph


# ---------------------------------------------------------------------------
# Snapping: find the nearest river reach to a dam point
# ---------------------------------------------------------------------------

METERS_PER_DEGREE = 111_320  # at the equator; scaled by cos(lat) below for longitude


def approx_distance_m(candidates: gpd.GeoDataFrame, lon: float, lat: float):
    """Approximate planar distance in metres from (lon, lat) to each geometry,
    using an equirectangular approximation centred on the dam's latitude
    (scale longitude by cos(lat)). Accurate to well under 1% at the few-km
    scale used here, and avoids a per-dam UTM zone lookup/reprojection."""
    scale_x = math.cos(math.radians(lat))
    scaled_geoms = candidates.geometry.affine_transform([scale_x, 0, 0, 1, 0, 0])
    point_scaled = Point(lon * scale_x, lat)
    return scaled_geoms.distance(point_scaled) * METERS_PER_DEGREE


def snap_to_nearest_reach(shp_path: Path, lon: float, lat: float):
    """Return (HYRIV_ID, snap_distance_m) for the nearest reach, or (None, None)
    if nothing is found within HARD_SNAP_CUTOFF."""
    old_cwd = os.getcwd()
    for radius_deg in (0.02, 0.05, 0.1):  # progressively wider bbox (~2 / 5 / 10 km)
        bbox = (lon - radius_deg, lat - radius_deg, lon + radius_deg, lat + radius_deg)
        try:
            os.chdir(shp_path.parent)
            candidates = gpd.read_file(shp_path.name, bbox=bbox)
        finally:
            os.chdir(old_cwd)

        if candidates.empty:
            continue

        candidates["_dist_m"] = approx_distance_m(candidates, lon, lat)
        nearest = candidates.loc[candidates["_dist_m"].idxmin()]

        if nearest["_dist_m"] <= HARD_SNAP_CUTOFF:
            return int(nearest["HYRIV_ID"]), float(nearest["_dist_m"])

    return None, None


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


def export_upstream_gpkg(shp_path: Path, upstream_ids: list, out_path: Path):
    id_list = ",".join(str(i) for i in upstream_ids)
    where_clause = f"HYRIV_ID IN ({id_list})"
    old_cwd = os.getcwd()
    try:
        os.chdir(shp_path.parent)
        gdf = gpd.read_file(shp_path.name, where=where_clause)
    finally:
        os.chdir(old_cwd)
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
    print("Building upstream connectivity graph (one-time, whole network)...")
    reverse_graph = build_reverse_graph(shp_path)
    print(f"Graph built: {len(reverse_graph):,} downstream nodes with upstream contributors.")

    out_subdir = PLOT_DIR / csv_stem
    output_csv_path = OUTPUT_DIR / f"{csv_stem}_output.csv"

    for i, row in df.iterrows():
        dam_id = row["Dam ID"]

        if pd.notna(row.get("QC_flag")) and row["QC_flag"] != "":
            continue  # already processed, skip on rerun

        lat, lon = row["Latitude"], row["Longitude"]
        try:
            snapped_id, snap_dist = snap_to_nearest_reach(shp_path, lon, lat)
            if snapped_id is None:
                df.at[i, "QC_flag"] = "NO_SNAP"
                continue

            upstream_ids = collect_upstream_ids(reverse_graph, snapped_id)
            out_gpkg = out_subdir / f"{dam_id}_DrainageSystem.gpkg"
            export_upstream_gpkg(shp_path, upstream_ids, out_gpkg)

            df.at[i, "QC_flag"] = "OK"
            df.at[i, "Snap_distance_m"] = round(snap_dist, 1)
            df.at[i, "N_upstream_reaches"] = len(upstream_ids)

            flag = "" if snap_dist <= DEFAULT_SNAP_RADIUS else "  <-- large snap distance, verify in QGIS"
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