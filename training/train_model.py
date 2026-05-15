"""
Train ML Model for Travel Time Prediction.

Compares multiple regressors (XGBoost, RandomForest, GradientBoosting,
ExtraTrees) with cross-validation and saves the best-performing model
together with feature metadata, encoders, and evaluation metrics.
"""

import os
import sys
import json
import time
import joblib
import pandas as pd
import numpy as np
from sklearn.model_selection import train_test_split, cross_val_score
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.ensemble import (
    RandomForestRegressor,
    GradientBoostingRegressor,
    ExtraTreesRegressor,
)
from xgboost import XGBRegressor


# Make project root importable
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

DATA_PATH  = os.path.join(ROOT, "data", "bangalore_routes.csv")
MODEL_PATH = os.path.join(ROOT, "models", "route_model.pkl")
METRICS_PATH = os.path.join(ROOT, "models", "metrics.json")

WEATHER_MAP = {"Clear": 0, "Cloudy": 1, "Foggy": 2, "Rainy": 3}
DAY_MAP     = {"Weekday": 0, "Weekend": 1}


def feature_engineer(df):
    df = df.copy()
    df["traffic_density"] = df["vehicles"] / df["road_capacity"].clip(lower=1)
    df["weather_enc"]     = df["weather"].map(WEATHER_MAP).fillna(0).astype(int)
    if "day_of_week" in df.columns:
        df["day_enc"] = df["day_of_week"].map(DAY_MAP).fillna(0).astype(int)
    else:
        df["day_enc"] = 0
    df["peak_hour"]       = df["is_peak"].astype(int)
    df["hour_sin"] = np.sin(2 * np.pi * df["hour"] / 24.0)
    df["hour_cos"] = np.cos(2 * np.pi * df["hour"] / 24.0)
    df["congestion_idx"] = df["traffic_density"] * (1 + df["peak_hour"] * 0.5)
    return df


def ensure_dataset():
    if not os.path.exists(DATA_PATH):
        raise FileNotFoundError(
            f"Dataset missing: {DATA_PATH}. Place bangalore_routes.csv there."
        )


def train():
    ensure_dataset()
    df = pd.read_csv(DATA_PATH)
    print(f"Loaded {len(df)} rows")

    df = feature_engineer(df)

    feature_cols = [
        "distance", "vehicles", "speed", "signal_time",
        "road_capacity", "hour", "hour_sin", "hour_cos",
        "traffic_density", "weather_enc", "day_enc",
        "peak_hour", "congestion_idx",
    ]
    X = df[feature_cols]
    y = df["travel_time"]

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42
    )

    candidates = {
        "XGBoost": XGBRegressor(
            n_estimators=500, max_depth=8, learning_rate=0.07,
            subsample=0.9, colsample_bytree=0.9,
            random_state=42, n_jobs=-1, tree_method="hist",
        ),
        "RandomForest": RandomForestRegressor(
            n_estimators=200, max_depth=18, min_samples_leaf=2,
            random_state=42, n_jobs=-1,
        ),
        "GradientBoosting": GradientBoostingRegressor(
            n_estimators=250, max_depth=5, learning_rate=0.08, random_state=42,
        ),
        "ExtraTrees": ExtraTreesRegressor(
            n_estimators=250, max_depth=20, min_samples_leaf=2,
            random_state=42, n_jobs=-1,
        ),
    }

    results = {}
    best_name, best_model, best_r2 = None, None, -1e9
    for name, mdl in candidates.items():
        t0 = time.time()
        print(f"Training {name} ...")
        mdl.fit(X_train, y_train)
        pred = mdl.predict(X_test)
        mae  = mean_absolute_error(y_test, pred)
        rmse = float(np.sqrt(mean_squared_error(y_test, pred)))
        r2   = r2_score(y_test, pred)
        denom = np.where(np.abs(y_test) < 1e-3, 1e-3, np.abs(y_test))
        mape = float(np.mean(np.abs((y_test - pred) / denom)) * 100)
        elapsed = time.time() - t0
        results[name] = {"MAE": float(mae), "RMSE": rmse, "R2": float(r2),
                          "MAPE": mape, "train_seconds": round(elapsed, 2)}
        print(f"  -> R2={r2:.4f}  MAE={mae:.4f}  RMSE={rmse:.4f}  ({elapsed:.1f}s)")
        if r2 > best_r2:
            best_r2, best_name, best_model = r2, name, mdl

    print(f"\nBest model: {best_name} (R2={best_r2:.4f})")

    os.makedirs(os.path.dirname(MODEL_PATH), exist_ok=True)
    joblib.dump({
        "model": best_model,
        "model_name": best_name,
        "features": feature_cols,
        "weather_map": WEATHER_MAP,
        "day_map": DAY_MAP,
        "metrics": results[best_name],
    }, MODEL_PATH)
    with open(METRICS_PATH, "w") as f:
        json.dump({"best": best_name, "results": results}, f, indent=2)
    print(f"Saved: {MODEL_PATH}")
    print(f"Metrics: {METRICS_PATH}")


if __name__ == "__main__":
    train()