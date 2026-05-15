import os
import random
import pandas as pd
import numpy as np

random.seed(42)
np.random.seed(42)


def load_locations_from_excel(file_path):
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Excel file not found: {file_path}")

    df = pd.read_excel(file_path)
    df.columns = df.columns.str.strip()

    required = {"Location", "Latitude", "Longitude"}
    if not required.issubset(df.columns):
        raise ValueError("Excel must contain Location, Latitude, Longitude")

    locations = {}

    for _, row in df.iterrows():
        try:
            name = str(row["Location"]).strip()
            lat = float(row["Latitude"])
            lon = float(row["Longitude"])

            if pd.isna(lat) or pd.isna(lon):
                continue

            locations[name] = (lat, lon)
        except:
            continue

    print(f"Loaded {len(locations)} locations")
    return locations


def haversine(lat1, lon1, lat2, lon2):
    R = 6371
    lat1, lon1, lat2, lon2 = map(np.radians, [lat1, lon1, lat2, lon2])

    dlat = lat2 - lat1
    dlon = lon2 - lon1

    a = np.sin(dlat/2)**2 + np.cos(lat1)*np.cos(lat2)*np.sin(dlon/2)**2
    return R * 2 * np.arcsin(np.sqrt(a))


def generate_connections(locations, k=7):
    locs = list(locations.keys())
    edges = set()

    for src in locs:
        lat1, lon1 = locations[src]

        distances = []

        for dst in locs:
            if src == dst:
                continue

            lat2, lon2 = locations[dst]
            dist = haversine(lat1, lon1, lat2, lon2)

            distances.append((dst, dist))

        distances.sort(key=lambda x: x[1])

        # 🔥 increase connectivity
        nearest = distances[:k]

        for dst, _ in nearest:
            # ✅ BIDIRECTIONAL (CRITICAL)
            edges.add((src, dst))
            edges.add((dst, src))

    print(f"Generated {len(edges)} connections (dense graph)")
    return list(edges)


def generate_dataset(locations, output_path):

    WEATHER = ["Clear", "Rainy", "Foggy", "Cloudy"]
    connections = generate_connections(locations, k=5)

    records = []

    for src, dst in connections:
        lat_s, lon_s = locations[src]
        lat_d, lon_d = locations[dst]

        distance = round(haversine(lat_s, lon_s, lat_d, lon_d), 2)

        for _ in range(10):
            hour = random.randint(0, 23)
            is_peak = 1 if (8 <= hour <= 11) or (17 <= hour <= 21) else 0

            road_capacity = random.choice([1000, 1500, 2000, 2500, 3000])

            if is_peak:
                vehicles = random.randint(int(road_capacity * 0.7), int(road_capacity * 1.2))
                speed = random.randint(10, 30)
            else:
                vehicles = random.randint(int(road_capacity * 0.2), int(road_capacity * 0.7))
                speed = random.randint(25, 55)

            signal_time = random.randint(20, 120)
            weather = random.choice(WEATHER)

            travel_time = (distance / max(speed, 5)) * 60
            travel_time *= (1.4 if is_peak else 1.0)

            records.append({
                "source_location": src,
                "destination_location": dst,
                "lat_src": lat_s,
                "lon_src": lon_s,
                "lat_dest": lat_d,
                "lon_dest": lon_d,
                "distance": distance,
                "vehicles": vehicles,
                "speed": speed,
                "signal_time": signal_time,
                "road_capacity": road_capacity,
                "weather": weather,
                "travel_time": round(travel_time, 2),
                "hour": hour,
                "is_peak": is_peak
            })

    df = pd.DataFrame(records)

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    df.to_csv(output_path, index=False)

    print(f"\nDataset created: {output_path}")
    print(f"Total rows: {len(df)}")


if __name__ == "__main__":

    BASE = r"D:\MCA Projects\traffic-24-4\route_ml_app\data"

    input_file = os.path.join(BASE, "final_no_none_locations.xlsx")
    output_file = os.path.join(BASE, "bangalore_routes.csv")

    locs = load_locations_from_excel(input_file)
    generate_dataset(locs, output_file)