"""
Graph construction + routing logic — UPGRADED.

Key upgrades:
  * MEDIAN coordinate per location (outliers > 1km from median dropped).
  * Dynamic day_enc (today's weekday detected at predict time).
  * Robust dijkstra/all_paths preserved.
  * Bangalore bounding-box validation helpers.
  * OSRM snap-to-road cache (disk-persisted JSON) — used by traffic routes.
"""

from __future__ import annotations

import os
import json
import math
import heapq
import bisect
import datetime
import threading
import pandas as pd
import numpy as np
import joblib
from collections import defaultdict


# ---------------------------------------------------------------------------
# Geo helpers
# ---------------------------------------------------------------------------
BANGALORE_BOUNDS = {"lat_min": 12.7, "lat_max": 13.2,
                    "lon_min": 77.4, "lon_max": 77.8}


def haversine_km(lat1, lon1, lat2, lon2) -> float:
    """Great-circle distance (km)."""
    try:
        R = 6371.0088
        lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
        dlat = lat2 - lat1
        dlon = lon2 - lon1
        a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
        return 2 * R * math.asin(min(1.0, math.sqrt(a)))
    except Exception:
        return float("inf")


def in_bangalore(lat, lon) -> bool:
    try:
        return (BANGALORE_BOUNDS["lat_min"] <= lat <= BANGALORE_BOUNDS["lat_max"]
                and BANGALORE_BOUNDS["lon_min"] <= lon <= BANGALORE_BOUNDS["lon_max"])
    except Exception:
        return False


def median_coordinate(lats, lons, max_drift_km: float = 1.0):
    """Median lat/lon of a location, dropping points > max_drift_km from median."""
    lats = np.asarray(lats, dtype=float)
    lons = np.asarray(lons, dtype=float)
    mask = np.isfinite(lats) & np.isfinite(lons)
    lats, lons = lats[mask], lons[mask]
    if lats.size == 0:
        return None
    mlat, mlon = float(np.median(lats)), float(np.median(lons))
    if lats.size > 4:
        keep = []
        for la, lo in zip(lats, lons):
            if haversine_km(la, lo, mlat, mlon) <= max_drift_km:
                keep.append((la, lo))
        if len(keep) >= 3:
            ka = np.array(keep)
            mlat, mlon = float(np.median(ka[:, 0])), float(np.median(ka[:, 1]))
    return (mlat, mlon)


# ---------------------------------------------------------------------------
# Snap cache (used by traffic routes; lives next to graph for reuse)
# ---------------------------------------------------------------------------
class SnapCache:
    """Tiny thread-safe JSON-backed cache for OSRM /nearest results."""
    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()
        self._data: dict = {}
        try:
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    self._data = json.load(f) or {}
        except Exception:
            self._data = {}

    def _key(self, lat: float, lon: float) -> str:
        return f"{round(lat, 5)},{round(lon, 5)}"

    def get(self, lat, lon):
        return self._data.get(self._key(lat, lon))

    def set(self, lat, lon, value):
        with self._lock:
            self._data[self._key(lat, lon)] = value
            try:
                tmp = self.path + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(self._data, f)
                os.replace(tmp, self.path)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Graph
# ---------------------------------------------------------------------------
class RouteGraph:
    def __init__(self, data_path, model_path):
        self.data_path  = data_path
        self.model_path = model_path
        self.df         = None
        self.model      = None
        self.features   = None
        self.weather_map = None
        self.day_map    = {"Weekday": 0, "Weekend": 1}
        self.model_name = None
        self.metrics    = {}
        self.coords: dict[str, tuple[float, float]] = {}
        self.edges      = defaultdict(dict)

        # Location autocomplete index
        self.locations: list[str] = []
        self._loc_lower: list[str] = []
        self._loc_lower_sorted: list[str] = []
        self._loc_lower_to_orig: dict[str, str] = {}

        self._load()
        self._build_location_index()

    # ----------------------------------------------------------
    def _load(self):
        self.df = pd.read_csv(self.data_path)

        bundle = joblib.load(self.model_path)
        self.model       = bundle["model"]
        self.features    = bundle["features"]
        self.weather_map = bundle["weather_map"]
        self.day_map     = bundle.get("day_map", self.day_map)
        self.model_name  = bundle.get("model_name", "model")
        self.metrics     = bundle.get("metrics", {})

        # ---- Median coordinate per location (outlier-resistant) -----------
        src = self.df[["source_location", "lat_src", "lon_src"]].dropna()
        dst = self.df[["destination_location", "lat_dest", "lon_dest"]].dropna()
        src.columns = ["loc", "lat", "lon"]
        dst.columns = ["loc", "lat", "lon"]
        all_pts = pd.concat([src, dst], ignore_index=True)
        all_pts = all_pts[
            (all_pts["lat"].between(BANGALORE_BOUNDS["lat_min"], BANGALORE_BOUNDS["lat_max"]))
            & (all_pts["lon"].between(BANGALORE_BOUNDS["lon_min"], BANGALORE_BOUNDS["lon_max"]))
        ]
        for loc, grp in all_pts.groupby("loc", sort=False):
            mc = median_coordinate(grp["lat"].values, grp["lon"].values)
            if mc is not None:
                self.coords[str(loc)] = mc

        # ---- Aggregate edge attributes ------------------------------------
        grouped = self.df.groupby(["source_location", "destination_location"]).agg({
            "distance": "mean",
            "vehicles": "mean",
            "speed": "mean",
            "signal_time": "mean",
            "road_capacity": "mean",
        }).reset_index()

        for r in grouped.itertuples(index=False):
            src_c = self.coords.get(r.source_location)
            dst_c = self.coords.get(r.destination_location)
            if not src_c or not dst_c:
                continue
            self.edges[r.source_location][r.destination_location] = {
                "distance": float(r.distance),
                "vehicles": float(r.vehicles),
                "speed": float(r.speed),
                "signal_time": float(r.signal_time),
                "road_capacity": float(r.road_capacity),
                "lat_src": src_c[0], "lon_src": src_c[1],
                "lat_dest": dst_c[0], "lon_dest": dst_c[1],
            }

    # ----------------------------------------------------------
    def _build_location_index(self):
        uniq = pd.unique(
            pd.concat([self.df["source_location"], self.df["destination_location"]],
                      ignore_index=True).dropna().astype(str)
        )
        # Only keep locations we have valid coords for
        uniq = [u for u in uniq if u in self.coords]
        self.locations = sorted(uniq)
        self._loc_lower = [s.lower() for s in self.locations]
        self._loc_lower_sorted = sorted(self._loc_lower)
        self._loc_lower_to_orig = {s.lower(): s for s in self.locations}

    # ----------------------------------------------------------
    def get_locations(self):
        return self.locations

    # ----------------------------------------------------------
    def search_locations(self, query, limit=10):
        if not query:
            return self.locations[:limit]
        q = query.strip().lower()
        if not q:
            return self.locations[:limit]

        seen, out = set(), []

        lo = bisect.bisect_left(self._loc_lower_sorted, q)
        i = lo
        while i < len(self._loc_lower_sorted) and self._loc_lower_sorted[i].startswith(q):
            orig = self._loc_lower_to_orig[self._loc_lower_sorted[i]]
            if orig not in seen:
                seen.add(orig); out.append(orig)
                if len(out) >= limit:
                    return out
            i += 1

        for low, orig in zip(self._loc_lower, self.locations):
            if orig in seen:
                continue
            if q in low:
                seen.add(orig); out.append(orig)
                if len(out) >= limit:
                    return out

        tokens = [t for t in q.split() if t]
        if len(tokens) > 1 and len(out) < limit:
            for low, orig in zip(self._loc_lower, self.locations):
                if orig in seen:
                    continue
                if all(t in low for t in tokens):
                    seen.add(orig); out.append(orig)
                    if len(out) >= limit:
                        break
        return out

    # ----------------------------------------------------------
    def _row(self, e, hour, weather, day_enc):
        is_peak = 1 if (8 <= hour <= 11) or (17 <= hour <= 21) else 0
        td = e["vehicles"] / max(e["road_capacity"], 1)
        return {
            "distance":        e["distance"],
            "vehicles":        e["vehicles"],
            "speed":           e["speed"],
            "signal_time":     e["signal_time"],
            "road_capacity":   e["road_capacity"],
            "hour":            hour,
            "hour_sin":        float(np.sin(2 * np.pi * hour / 24.0)),
            "hour_cos":        float(np.cos(2 * np.pi * hour / 24.0)),
            "traffic_density": td,
            "weather_enc":     self.weather_map.get(weather, 0),
            "day_enc":         day_enc,
            "peak_hour":       is_peak,
            "congestion_idx":  td * (1 + is_peak * 0.5),
        }

    def _day_enc_today(self):
        # Mon-Fri = 0 (Weekday), Sat/Sun = 1 (Weekend)
        wd = datetime.datetime.now().weekday()
        return 1 if wd >= 5 else 0

    # ----------------------------------------------------------
    def predict_edge_time(self, src, dst, hour=None, weather="Clear", day_enc=None):
        if dst not in self.edges.get(src, {}):
            return None
        e = self.edges[src][dst]
        if hour is None:
            hour = datetime.datetime.now().hour
        if day_enc is None:
            day_enc = self._day_enc_today()
        row = self._row(e, hour, weather, day_enc)
        feats = pd.DataFrame([{k: row.get(k, 0) for k in self.features}])[self.features]
        try:
            return float(self.model.predict(feats)[0])
        except Exception:
            return float(e["distance"]) / max(e["speed"], 1) * 60.0

    # ----------------------------------------------------------
    def build_weighted_graph(self, hour=None, weather="Clear", day_enc=None):
        adj = defaultdict(list)
        if hour is None:
            hour = datetime.datetime.now().hour
        if day_enc is None:
            day_enc = self._day_enc_today()
        rows, keys = [], []
        for src, neigh in self.edges.items():
            for dst, e in neigh.items():
                rows.append(self._row(e, hour, weather, day_enc))
                keys.append((src, dst))
        if not rows:
            return adj
        X = pd.DataFrame(rows)[self.features]
        try:
            preds = self.model.predict(X)
        except Exception:
            preds = [r["distance"] / max(r["speed"], 1) * 60.0 for r in rows]
        for (src, dst), t in zip(keys, preds):
            adj[src].append((dst, max(float(t), 0.001)))
        return adj

    # ----------------------------------------------------------
    def dijkstra(self, adj, start, end):
        if start not in adj:
            return None, float("inf")
        dist = {start: 0}
        prev = {}
        pq = [(0, start)]
        while pq:
            d, u = heapq.heappop(pq)
            if u == end:
                break
            if d > dist.get(u, float("inf")):
                continue
            for v, w in adj.get(u, []):
                nd = d + w
                if nd < dist.get(v, float("inf")):
                    dist[v] = nd
                    prev[v] = u
                    heapq.heappush(pq, (nd, v))
        if end not in dist:
            return None, float("inf")
        path = [end]
        while path[-1] != start:
            path.append(prev[path[-1]])
        path.reverse()
        return path, dist[end]

    # ----------------------------------------------------------
    def all_paths(self, adj, start, end, max_depth=8, max_paths=12):
        results = []

        def dfs(node, path, visited, total_time):
            if len(results) >= max_paths:
                return
            if len(path) > max_depth:
                return
            if node == end:
                results.append((list(path), total_time))
                return
            for nb, w in adj.get(node, []):
                if nb not in visited:
                    visited.add(nb)
                    path.append(nb)
                    dfs(nb, path, visited, total_time + w)
                    path.pop()
                    visited.remove(nb)

        dfs(start, [start], {start}, 0.0)
        results.sort(key=lambda x: x[1])
        return results

    # ----------------------------------------------------------
    def path_coordinates(self, path):
        return [list(self.coords[node]) for node in path if node in self.coords]

    # ----------------------------------------------------------
    def traffic_level(self, minutes):
        if minutes < 20:  return "Low"
        if minutes < 40:  return "Moderate"
        if minutes < 60:  return "High"
        return "Severe"
