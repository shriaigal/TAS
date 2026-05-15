"""Traffic / routing endpoints — UPGRADED.

Highlights:
  * OSRM routing uses ONLY (source, destination) — no more zig-zags.
  * Real OSRM distance (metres -> km).
  * Snap-to-road via OSRM /nearest with disk cache + retries.
  * Polyline simplification (Ramer-Douglas-Peucker) for smooth lines.
  * Route validation (Bangalore bbox, distance sanity, no-loops).
  * Bad-path filter (> 3x straight-line distance discarded).
  * In-memory route cache keyed by (src, dst, hour, weather).
  * 24-hour forecast endpoint.
  * Best-departure recommendation endpoint.
  * Per-segment congestion colouring for heatmap on the map.
  * Self-healing error handling everywhere.
"""
from __future__ import annotations

import os
import sys
import time
import math
import threading
from collections import defaultdict
from datetime import datetime

import requests
from flask import Blueprint, render_template, request, jsonify, current_app, g
from flask_login import login_required, current_user

# Make routing/ importable
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from routing.graph import (  # noqa: E402
    RouteGraph, SnapCache, haversine_km, in_bangalore, BANGALORE_BOUNDS,
)

from .. import csrf, mongo

traffic_bp = Blueprint("traffic", __name__)

_graph: RouteGraph | None = None
_analytics_cache = None
_route_cache: dict = {}
_route_cache_lock = threading.Lock()
_snap_cache: SnapCache | None = None

OSRM_BASE = os.environ.get("OSRM_BASE", "https://router.project-osrm.org")
OSRM_TIMEOUT = float(os.environ.get("OSRM_TIMEOUT", "8"))
RDP_TOLERANCE = 0.00005


# ---------------------------------------------------------------------------
# Singletons
# ---------------------------------------------------------------------------
def get_graph() -> RouteGraph:
    global _graph, _snap_cache
    if _graph is None:
        data_path = current_app.config["DATA_PATH"]
        model_path = current_app.config["MODEL_PATH"]
        if not os.path.exists(data_path):
            raise RuntimeError("Dataset missing. Run data/generate_dataset.py first.")
        if not os.path.exists(model_path):
            raise RuntimeError("Model missing. Run training/train_model.py first.")
        current_app.logger.info("Loading RouteGraph + building location index…")
        _graph = RouteGraph(data_path, model_path)
        cache_dir = os.path.join(current_app.config["BASE_DIR"], "models")
        os.makedirs(cache_dir, exist_ok=True)
        _snap_cache = SnapCache(os.path.join(cache_dir, "snap_cache.json"))
        current_app.logger.info(
            f"RouteGraph ready. Locations indexed: {len(_graph.get_locations())}"
        )
    return _graph


@traffic_bp.before_app_request
def _attach_graph():
    try:
        gr = get_graph()
        g.graph = gr
        g.locations = gr.locations
    except Exception:
        g.graph = None
        g.locations = []


# ---------------------------------------------------------------------------
# OSRM helpers (with retries + cache + graceful fallback)
# ---------------------------------------------------------------------------
def _http_get(url: str, retries: int = 2):
    last_err = None
    for attempt in range(retries + 1):
        try:
            res = requests.get(url, timeout=OSRM_TIMEOUT)
            if res.status_code == 200:
                return res.json()
            last_err = f"HTTP {res.status_code}"
        except Exception as e:
            last_err = str(e)
        time.sleep(0.3 * (attempt + 1))
    raise RuntimeError(f"OSRM request failed: {last_err}")


def snap_to_road(lat: float, lon: float):
    """Snap a coordinate to the nearest drivable road. Cached on disk."""
    if not in_bangalore(lat, lon):
        return (lat, lon)
    if _snap_cache is not None:
        cached = _snap_cache.get(lat, lon)
        if cached:
            return tuple(cached)
    try:
        data = _http_get(f"{OSRM_BASE}/nearest/v1/driving/{lon},{lat}?number=1")
        if data.get("code") == "Ok" and data.get("waypoints"):
            wp = data["waypoints"][0]["location"]  # [lon, lat]
            snapped = (float(wp[1]), float(wp[0]))
            if _snap_cache is not None:
                _snap_cache.set(lat, lon, list(snapped))
            return snapped
    except Exception as e:
        current_app.logger.debug(f"snap_to_road fallback ({lat},{lon}): {e}")
    return (lat, lon)


def _rdp(points, eps: float):
    """Iterative Ramer-Douglas-Peucker on lat/lon points."""
    if len(points) < 3:
        return points
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        i, j = stack.pop()
        if j <= i + 1:
            continue
        ax, ay = points[i]
        bx, by = points[j]
        dx, dy = bx - ax, by - ay
        denom = (dx * dx + dy * dy) ** 0.5 or 1e-12
        max_d, max_idx = 0.0, -1
        for k in range(i + 1, j):
            px, py = points[k]
            d = abs(dy * px - dx * py + bx * ay - by * ax) / denom
            if d > max_d:
                max_d, max_idx = d, k
        if max_d > eps and max_idx != -1:
            keep[max_idx] = True
            stack.append((i, max_idx))
            stack.append((max_idx, j))
    return [p for p, k in zip(points, keep) if k]


def osrm_route(src_coord, dst_coord):
    """Fetch geometry between EXACTLY two coordinates (no intermediate nodes)."""
    s_lat, s_lon = src_coord
    d_lat, d_lon = dst_coord
    url = (f"{OSRM_BASE}/route/v1/driving/"
           f"{s_lon},{s_lat};{d_lon},{d_lat}"
           f"?overview=full&geometries=geojson&alternatives=false&steps=false")
    data = _http_get(url)
    if data.get("code") != "Ok" or not data.get("routes"):
        raise RuntimeError(f"OSRM no route: {data.get('code')}")
    route = data["routes"][0]
    coords_lonlat = route["geometry"]["coordinates"]
    coords_latlon = [[c[1], c[0]] for c in coords_lonlat]
    distance_km = float(route.get("distance", 0)) / 1000.0
    duration_min = float(route.get("duration", 0)) / 60.0
    return coords_latlon, distance_km, duration_min


def get_full_route(path, coords_map):
    """
    Build geometry between source and destination ONLY (path is informational).
    Returns: (geometry[lat,lon], distance_km, osrm_duration_min)
    """
    try:
        if not path or len(path) < 2:
            return [], 0.0, 0.0
        src, dst = path[0], path[-1]
        if src not in coords_map or dst not in coords_map:
            return [], 0.0, 0.0
        s = snap_to_road(*coords_map[src])
        d = snap_to_road(*coords_map[dst])
        geom, dist_km, dur_min = osrm_route(s, d)
        if not geom or len(geom) < 2:
            return [], 0.0, 0.0
        # Validate
        for la, lo in (geom[0], geom[-1]):
            if not in_bangalore(la, lo):
                # OSRM returned something weird; reject
                return [], 0.0, 0.0
        straight = haversine_km(s[0], s[1], d[0], d[1])
        if straight > 0 and dist_km > straight * 5 and dist_km > 5:
            # Wildly excessive — likely corrupted geometry
            return [], 0.0, 0.0
        # Simplify
        simplified = _rdp(geom, RDP_TOLERANCE) if len(geom) > 50 else geom
        return simplified, round(dist_km, 2), round(dur_min, 2)
    except Exception as e:
        current_app.logger.debug(f"get_full_route error: {e}")
        return [], 0.0, 0.0


# ---------------------------------------------------------------------------
# Path hygiene
# ---------------------------------------------------------------------------
def remove_loops(path):
    seen, new_path = {}, []
    for node in path:
        if node in seen:
            new_path = new_path[:seen[node] + 1]
        else:
            seen[node] = len(new_path)
            new_path.append(node)
    return new_path


def remove_repeated(path):
    out = []
    for n in path:
        if not out or out[-1] != n:
            out.append(n)
    return out


# ---------------------------------------------------------------------------
# Per-segment congestion (for heatmap colouring)
# ---------------------------------------------------------------------------
def segment_levels(gr, path, hour, weather):
    """Predict congestion per consecutive (a,b) edge along the path."""
    out = []
    for a, b in zip(path[:-1], path[1:]):
        e = gr.edges.get(a, {}).get(b)
        if not e:
            out.append({"from": a, "to": b, "level": "Moderate", "score": 0.5})
            continue
        td = e["vehicles"] / max(e["road_capacity"], 1)
        is_peak = 1 if (8 <= hour <= 11) or (17 <= hour <= 21) else 0
        score = float(min(1.5, td * (1 + is_peak * 0.5)))
        if score < 0.45:
            level = "Low"
        elif score < 0.75:
            level = "Moderate"
        elif score < 1.0:
            level = "High"
        else:
            level = "Severe"
        out.append({"from": a, "to": b, "level": level, "score": round(score, 3)})
    return out


# ---------------------------------------------------------------------------
# Analytics (cached)
# ---------------------------------------------------------------------------
def _build_analytics():
    global _analytics_cache
    if _analytics_cache is not None:
        return _analytics_cache
    gr = get_graph()
    df = gr.df

    hourly = df.groupby("hour").agg(
        vehicles=("vehicles", "mean"),
        speed=("speed", "mean"),
    ).reset_index()
    hourly_vehicles = [round(float(v), 1) for v in hourly["vehicles"]]
    hourly_speed    = [round(float(v), 1) for v in hourly["speed"]]

    if "day_of_week" in df.columns:
        daily = df.groupby("day_of_week").agg(
            vehicles=("vehicles", "mean"),
            speed=("speed", "mean"),
        ).to_dict("index")
    else:
        daily = {}

    weather = df.groupby("weather").agg(
        vehicles=("vehicles", "mean"),
        speed=("speed", "mean"),
        count=("vehicles", "count"),
    ).reset_index().to_dict("records")

    density = df["vehicles"] / df["road_capacity"].clip(lower=1)
    severity = {
        "Low":      int((density < 0.4).sum()),
        "Moderate": int(((density >= 0.4) & (density < 0.7)).sum()),
        "High":     int(((density >= 0.7) & (density < 1.0)).sum()),
        "Severe":   int((density >= 1.0).sum()),
    }

    edge_d = df.assign(density=density).groupby(
        ["source_location", "destination_location"]
    ).agg(density=("density", "mean"),
          vehicles=("vehicles", "mean"),
          speed=("speed", "mean")).reset_index()
    top_congested = edge_d.sort_values("density", ascending=False).head(10).to_dict("records")

    area = df.groupby("source_location").agg(
        avg_vehicles=("vehicles", "mean"),
        avg_speed=("speed", "mean"),
        records=("vehicles", "count"),
    ).reset_index().sort_values("avg_vehicles", ascending=False)
    top_areas = area.head(15).to_dict("records")

    peak_rank = hourly.sort_values("vehicles", ascending=False)["hour"].tolist()

    heat_src = df.sample(n=min(3000, len(df)), random_state=1)
    heat = [
        [float(r.lat_src), float(r.lon_src),
         float(min(1.0, r.vehicles / max(r.road_capacity, 1)))]
        for r in heat_src.itertuples()
    ]

    total_routes = int(gr.df.groupby(["source_location", "destination_location"]).ngroups)
    kpis = {
        "locations":      len(gr.get_locations()),
        "edges":          total_routes,
        "records":        int(len(df)),
        "avg_speed":      round(float(df["speed"].mean()), 1),
        "avg_vehicles":   round(float(df["vehicles"].mean()), 1),
        "avg_congestion": round(float(density.mean()) * 100, 1),
        "model":          gr.model_name,
        "metrics":        gr.metrics,
    }

    _analytics_cache = {
        "kpis": kpis,
        "hourly_vehicles": hourly_vehicles,
        "hourly_speed":    hourly_speed,
        "daily":           daily,
        "weather":         weather,
        "severity":        severity,
        "top_congested":   top_congested,
        "top_areas":       top_areas,
        "peak_hours":      [int(h) for h in peak_rank[:5]],
        "heatmap":         heat,
    }
    return _analytics_cache


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@traffic_bp.route("/app")
@login_required
def home():
    return render_template("main/app.html")


@traffic_bp.route("/get_locations", methods=["GET"])
@login_required
def get_locations():
    try:
        q = request.args.get("q", "").strip()
        try:
            limit = int(request.args.get("limit", 10))
        except Exception:
            limit = 10
        limit = max(1, min(limit, 25))
        gr = get_graph()
        results = gr.search_locations(q, limit=limit)
        return jsonify({"query": q, "locations": results, "count": len(results)})
    except Exception as e:
        current_app.logger.exception("get_locations failed")
        return jsonify({"error": str(e), "locations": [], "count": 0}), 500


@traffic_bp.route("/analytics")
@login_required
def analytics_page():
    return render_template("main/analytics.html")


@traffic_bp.route("/api/analytics")
@login_required
def analytics_api():
    try:
        return jsonify(_build_analytics())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@traffic_bp.route("/api/predict", methods=["POST"])
@login_required
@csrf.exempt
def api_predict():
    """24-hour ML forecast for a given (src, dst) edge or full path."""
    try:
        data = request.get_json(force=True) or {}
        src = (data.get("source") or "").strip()
        dst = (data.get("destination") or "").strip()
        weather = data.get("weather", "Clear")
        gr = get_graph()
        if dst not in gr.edges.get(src, {}) and src not in gr.coords:
            return jsonify({"error": "Unknown locations"}), 400
        forecast = []
        for h in range(24):
            if dst in gr.edges.get(src, {}):
                t = gr.predict_edge_time(src, dst, hour=h, weather=weather)
            else:
                # Fall back to best-route ETA at hour h
                adj = gr.build_weighted_graph(hour=h, weather=weather)
                _, t = gr.dijkstra(adj, src, dst)
                if t is None or t == float("inf"):
                    t = 0
            forecast.append({"hour": h, "predicted": round(float(t), 4),
                             "level": gr.traffic_level(float(t) * 60)})
        return jsonify({"source": src, "destination": dst,
                        "weather": weather, "forecast": forecast})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@traffic_bp.route("/api/best_departure", methods=["POST"])
@login_required
@csrf.exempt
def api_best_departure():
    """Find the best hour to depart in the next 24h for a route."""
    try:
        data = request.get_json(force=True) or {}
        src = (data.get("source") or "").strip()
        dst = (data.get("destination") or "").strip()
        weather = data.get("weather", "Clear")
        gr = get_graph()
        now_hour = datetime.now().hour
        per_hour = []
        for h in range(24):
            adj = gr.build_weighted_graph(hour=h, weather=weather)
            path, t = gr.dijkstra(adj, src, dst)
            if path is None or t == float("inf"):
                per_hour.append({"hour": h, "minutes": None})
            else:
                per_hour.append({"hour": h, "minutes": round(float(t), 2)})
        valid = [p for p in per_hour if p["minutes"] is not None]
        if not valid:
            return jsonify({"error": "No route found"}), 404
        best = min(valid, key=lambda x: x["minutes"])
        now_entry = next((p for p in per_hour if p["hour"] == now_hour), None)
        savings = None
        if now_entry and now_entry["minutes"] is not None:
            savings = round(now_entry["minutes"] - best["minutes"], 2)
        return jsonify({
            "per_hour": per_hour,
            "best_hour": best["hour"],
            "best_minutes": best["minutes"],
            "current_hour": now_hour,
            "current_minutes": now_entry["minutes"] if now_entry else None,
            "savings_minutes": savings,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@traffic_bp.route("/get_routes", methods=["POST"])
@login_required
@csrf.exempt
def get_routes():
    try:
        data = request.get_json(force=True) or {}
        src = (data.get("source") or "").strip()
        dst = (data.get("destination") or "").strip()
        hour = data.get("hour")
        weather = data.get("weather", "Clear")

        if not src or not dst:
            return jsonify({"error": "Source and destination required."}), 400
        if src == dst:
            return jsonify({"error": "Source and destination cannot be same."}), 400

        gr = get_graph()
        if src not in gr.coords:
            return jsonify({"error": f"Unknown source: {src}"}), 400
        if dst not in gr.coords:
            return jsonify({"error": f"Unknown destination: {dst}"}), 400

        if hour is not None:
            try:
                hour = int(hour)
            except Exception:
                hour = None
        cache_key = (src, dst, hour, weather)
        with _route_cache_lock:
            cached = _route_cache.get(cache_key)
        if cached:
            return jsonify(cached)

        adj = gr.build_weighted_graph(hour=hour, weather=weather)
        best_path, best_time = gr.dijkstra(adj, src, dst)
        if best_path is None:
            return jsonify({"error": "No route found between these locations."}), 404

        all_paths = gr.all_paths(adj, src, dst, max_depth=8, max_paths=10)

        # Straight-line distance (km) for bad-path filter
        s_coord = gr.coords[src]
        d_coord = gr.coords[dst]
        straight_km = haversine_km(s_coord[0], s_coord[1], d_coord[0], d_coord[1])

        cur_hour = datetime.now().hour if hour is None else hour

        def format_path(path, total_time, label_idx=None):
            path = remove_repeated(remove_loops(path))
            geom, real_km, osrm_min = get_full_route(path, gr.coords)
            # Validation: discard wildly long routes
            if straight_km > 0 and real_km > max(3.0, straight_km * 3.0):
                return None
            seg = segment_levels(gr, path, cur_hour, weather)
            return {
                "path": path,
                "coordinates": geom,
                "travel_time": round(float(total_time), 2),
                "osrm_duration": osrm_min,
                "traffic_level": gr.traffic_level(float(total_time)),
                "hops": max(0, len(path) - 1),
                "distance": real_km,
                "segments": seg,
            }

        best_formatted = format_path(best_path, best_time)
        if best_formatted is None or not best_formatted["coordinates"]:
            return jsonify({"error": "Could not compute route geometry."}), 502

        all_formatted = []
        seen_keys = set()
        for p, t in all_paths:
            key = ">".join(p)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            f = format_path(p, t)
            if f is None:
                continue
            all_formatted.append(f)

        # Route stats panel data
        edges_used = list(zip(best_path[:-1], best_path[1:]))
        speeds, signals, caps, vehs = [], 0.0, [], []
        bottleneck = None
        worst = -1.0
        for a, b in edges_used:
            e = gr.edges.get(a, {}).get(b)
            if not e:
                continue
            speeds.append(e["speed"])
            signals += e["signal_time"]
            caps.append(e["road_capacity"])
            vehs.append(e["vehicles"])
            score = e["vehicles"] / max(e["road_capacity"], 1)
            if score > worst:
                worst, bottleneck = score, f"{a} → {b}"
        avg_speed = round(sum(speeds) / len(speeds), 1) if speeds else 0
        congestion = round((sum(vehs) / sum(caps)) * 100, 1) if caps and sum(caps) else 0
        confidence = max(0, min(100, round(100 - congestion / 1.5, 1)))
        efficiency = 0
        if best_formatted["distance"] > 0 and best_formatted["travel_time"] > 0:
            efficiency = round((best_formatted["distance"] / best_formatted["travel_time"]) * 60, 1)

        stats = {
            "avg_speed_kmh": avg_speed,
            "total_signal_delay_sec": round(signals, 1),
            "congestion_score_pct": congestion,
            "bottleneck": bottleneck or "—",
            "efficiency_kmh": efficiency,
            "confidence_pct": confidence,
            "straight_km": round(straight_km, 2),
        }

        response = {
            "source": src,
            "destination": dst,
            "weather": weather,
            "hour": hour,
            "best_route": best_formatted,
            "all_routes": all_formatted,
            "stats": stats,
        }

        with _route_cache_lock:
            if len(_route_cache) > 256:
                _route_cache.clear()
            _route_cache[cache_key] = response

        try:
            mongo.db.saved_routes.insert_one({
                "user_id": current_user.id,
                "source": src,
                "destination": dst,
                "best_time": best_formatted["travel_time"],
                "distance_km": best_formatted["distance"],
                "traffic_level": best_formatted["traffic_level"],
                "created_at": datetime.utcnow(),
            })
        except Exception:
            pass

        return jsonify(response)
    except Exception as e:
        current_app.logger.exception("get_routes failed")
        return jsonify({"error": str(e)}), 500


@traffic_bp.route("/history")
@login_required
def history():
    items = list(mongo.db.saved_routes.find({"user_id": current_user.id})
                 .sort("created_at", -1).limit(50))
    return render_template("main/history.html", items=items)


@traffic_bp.route("/health")
def health():
    return jsonify({"status": "ok"})
