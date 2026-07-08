Extract Drainage System

Extracts the upstream river network draining into each dam in a portfolio, using
the HydroRIVERS v10 global river network. For every dam, the tool snaps the dam
coordinate to the nearest HydroRIVERS reach and traverses the network upstream
(via the NEXT_DOWN connectivity field, reversed) to collect every contributing
reach. The result is a per-dam GeoPackage containing the full upstream drainage
line network, ready for visualization in QGIS.

Folder structure

Extract_DrainageSystem/
├── Data/      # input CSVs (dam coordinates)
├── Module/    # scripts
├── Output/    # enriched CSV with QC flags and snap statistics
└── Plot/      # per-dam GeoPackages, one subfolder per input CSV

Input

A CSV file with the following columns:

ColumnDescriptionDam IDUnique identifier (used as index key)Dam nameDam name (not used as key — duplicates exist in the global portfolio)LatitudeDam latitude, decimal degrees, WGS84LongitudeDam longitude, decimal degrees, WGS84Area_km2Reported drainage area, km²

Output


Per-dam GeoPackage: Plot/{csv_stem}/{Dam_ID}_DrainageSystem.gpkg, containing
every HydroRIVERS reach identified as upstream of the dam's snapped location.
Summary CSV: Output/{csv_stem}_output.csv, the input CSV enriched with:

QC_flag — OK, NO_SNAP (no reach found within the snap cutoff), or ERROR
Snap_distance_m — distance from the dam coordinate to the snapped reach
N_upstream_reaches — number of reaches in the extracted drainage system





Dams flagged OK with a Snap_distance_m above 1,000 m are printed to the
console with a warning and should be visually verified in QGIS, following the
same convention used for the Extract Downstream River Course tool.

Usage

bashuv run Module/ExtractDrainageSystem.py

Running without arguments opens a file picker to select the input CSV (falls
back to a typed path prompt if no display is available). Re-running on a
partially processed CSV skips dams already marked with a QC_flag.

Method


Load once: the full HydroRIVERS network (geometry + attributes) is
read into memory a single time per run, alongside a metric-CRS copy
(EPSG:4087, World Equidistant Cylindrical) used for distance
measurement. A reverse connectivity graph (NEXT_DOWN → list of
contributing HYRIV_IDs) is also built once from the attribute table.
Snapping: candidate reaches near each dam coordinate are found via the
spatial index on the metric-CRS copy, and true planar distance is measured
directly in metres — no per-dam bounding-box widening or degree-to-metre
approximation. The nearest reach within the 2,000 m hard cutoff is
selected.
Upstream traversal: the reverse connectivity graph is traversed
breadth-first from the snapped reach to collect every upstream
contributor.
Export: the in-memory river network is filtered to the collected
reach IDs and written directly to the per-dam GeoPackage — no repeated
disk reads or SQL filtering against the shapefile.


Data source

HydroRIVERS v10 (Lehner & Grill, 2013), path configured in
Module/ExtractDrainageSystem.py:

/Users/filou/MyProjects/MyGISProjects/Resources/Maps and data/HydroRIVERS/HydroRIVERS_v10_shp

Requirements

Python 3.14, managed with uv:

bashuv python pin 3.14
uv add geopandas pandas pyogrio shapely


Earlier versions filtered the exported subset via a GDAL where clause
(HYRIV_ID IN (...)) re-queried per dam, and approximated snap distance with
a cos(latitude)-scaled degree conversion. Both were replaced with the
load-once/filter-in-memory and EPSG:4087 reprojection approach described
above: the where-clause approach failed on dams with very large upstream
networks once the IN (...) list grew past what GDAL's shapefile SQL engine
could parse.