# NearHome QGIS clean project

Open **nearhome_validation_clean.qgz** in QGIS.

The project uses **relative paths** and project CRS **EPSG:3414 (SVY21 / Singapore TM)**. Keep the project file and `data/` folder together.

## Permanent project layers

- `data/access_candidates_svy21.geojson` — 99 candidate points; ACCEPTED/REJECTED categories.
- `data/sla_major_roads_svy21.geojson` — 61 SLA major-road features.
- `data/osm_matched_edges_svy21.geojson` — 150 OSM matched-edge features, reprojected from WGS84 to SVY21.
- `data/sla_major_roads_buffer_35m.geojson` — your backed-up 35 m buffer.
- `data/access_candidates_nearest_sla_svy21.geojson` — 100 nearest-analysis rows recovered from the earlier non-empty backup.
- `data/access_candidates_qa_svy21.geojson` — 100 QA rows from your latest backup (PASS/CHECK/FAIL).

## Recovery note

The newly uploaded `access_candidates.geojson` and `candidate_nearest_sla.geojson` contained **0 features**, so they were not used as the project data. Their non-empty versions were recovered from the earlier organized backup in this conversation.

The three descriptive fields (`matched_osm_edge_ids`, `approach_osm_edge_ids`, and `road_review_reasons`) were restored from the QGIS-compatible candidate backup where possible. No topology status or QA flag values were changed.

`metadata_backup/` contains the uploaded `.qmd` files for reference only; the project does not depend on them.
