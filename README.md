# Extract Drainage System

Extracts the upstream river network draining into each dam in a portfolio, using
the HydroRIVERS v10 global river network. For every dam, the tool snaps the dam
coordinate to the nearest HydroRIVERS reach and traverses the network upstream
(via the `NEXT_DOWN` connectivity field, reversed) to collect every contributing
reach. The result is a per-dam GeoPackage containing the full upstream drainage
line network, ready for visualization in QGIS.

## Folder structure

```
Extract_DrainageSystem/
├── Data/      # input CSVs (dam coordinates)
├── Module/    # scripts
├── Output/    # enriched CSV with QC flags and snap statistics
└── Plot/      # per-dam GeoPackages, one subfolder per input CSV
```

## Input

A CSV file with the following columns:

| Column      | Description                          |
|-------------|---------------------------------------|
| Dam ID      | Unique identifier (used as index key) |
| Dam name    | Dam name (not used as key — duplicates exist in the global portfolio) |
| Latitude    | Dam latitude, decimal degrees, WGS84  |
| Longitude   | Dam longitude, decimal degrees, WGS84 |
| Area_km2    | Reported drainage area, km²           |

## Output

- **Per-dam GeoPackage**: `Plot/{csv_stem}/{Dam_ID}_DrainageSystem.gpkg`, containing
  every HydroRIVERS reach identified as upstream of the dam's snapped location.
- **Summary CSV**: `Output/{csv_stem}_output.csv`, the input CSV enriched with:
  - `QC_flag` — `OK`, `NO_SNAP` (no reach found within the snap cutoff), or `ERROR`
  - `Snap_distance_m` — distance from the dam coordinate to the snapped reach
  - `N_upstream_reaches` — number of reaches in the extracted drainage system

Dams flagged `OK` with a `Snap_distance_m` above 1,000 m are printed to the
console with a warning and should be visually verified in QGIS, following the
same convention used for the Extract Downstream River Course tool.

## Usage

```bash
uv run Module/extract_drainage_system.py
```

Running without arguments opens a file picker to select the input CSV (falls
back to a typed path prompt if no display is available). Re-running on a
partially processed CSV skips dams already marked with a `QC_flag`.

## Method

1. **Snapping**: candidate reaches are read from HydroRIVERS within a
   progressively widening bounding box around the dam coordinate. Distance is
   approximated in metres using an equirectangular projection centred on the
   dam's latitude (longitude scaled by `cos(latitude)`), which is accurate to
   well under 1% at the few-kilometre scale used for snapping and avoids a
   per-dam UTM reprojection. The nearest reach within the 2,000 m hard cutoff
   is selected.
2. **Upstream traversal**: a reverse connectivity graph (`NEXT_DOWN` → list of
   contributing `HYRIV_ID`s) is built once per run from the full HydroRIVERS
   attribute table, then traversed breadth-first from the snapped reach to
   collect every upstream contributor.
3. **Export**: geometries for the collected reach IDs are read from
   HydroRIVERS and written to the per-dam GeoPackage.

## Data source

HydroRIVERS v10 (Lehner & Grill, 2013), path configured in
`Module/extract_drainage_system.py`:
```
/Users/filou/MyProjects/MyGISProjects/Resources/Maps and data/HydroRIVERS/HydroRIVERS_v10_shp
```

## Requirements

Python 3.14, managed with `uv`:

```bash
uv python pin 3.14
uv add geopandas pandas pyogrio shapely
```