# Upgrade Notes (v2)

## Routing pipeline
- OSRM routing now uses **only** source and destination coordinates — no more zig-zag geometry from forced intermediate waypoints.
- Distance is the **real OSRM distance** (`routes[0].distance / 1000`), not `len(path) * 2.5`.
- Coordinates per location are computed as the **median** of all observed lat/lon, with points > 1 km from the median dropped (Haversine).
- Coordinates are **snapped to the nearest road** via OSRM `/nearest`, with a disk-backed cache (`models/snap_cache.json`) and retries.
- Polyline geometry is simplified with **Ramer–Douglas–Peucker** (tolerance `0.00005`) for smooth rendering.
- **Validation**: routes outside the Bangalore bbox (12.7–13.2 lat, 77.4–77.8 lon) or with distance > 5× straight-line are discarded.
- **Bad-path filter**: any path whose real distance > 3× straight-line is dropped.
- **Caching**: in-memory route cache keyed by `(src, dst, hour, weather)`.

## ML
- `day_enc` is now derived dynamically from `datetime.now().weekday()` (weekend vs weekday) for both single-edge prediction and batch graph weighting.
- Predict failures fall back to a deterministic `distance / speed` estimate so the app never crashes.

## API additions
- `POST /api/predict` — 24-hour ML forecast (also works edge-to-edge or full-route).
- `POST /api/best_departure` — finds the best departure hour, returns predicted savings vs now.
- `GET /get_routes` response now includes:
  - `osrm_duration` per route,
  - `segments` (per-edge congestion level for the heatmap),
  - `stats` (avg speed, signal delay, congestion %, bottleneck, efficiency, confidence, straight-line km).

## Frontend (route planner)
- Best route is drawn as **multi-coloured segments** by congestion (green/yellow/orange/red).
- Alternate routes are slightly **offset** to prevent overlap mess and use dashed grey lines.
- Below the map the page now renders **dynamically** (analytics page is untouched):
  1. **Route comparison** horizontal bar chart (click a bar to flash that route).
  2. **24-hour traffic forecast** line chart with the current hour highlighted.
  3. **Best departure** card with savings vs now.
  4. **Route statistics** grid (speed, congestion, bottleneck, confidence, etc.).
- Map legend explains the segment colours.

## Reliability
- All endpoints wrapped in try/except with structured JSON errors.
- OSRM failures, missing edges, NaN values, malformed geometry, and chart errors are all handled gracefully.
