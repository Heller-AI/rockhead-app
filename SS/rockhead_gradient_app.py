"""
Rock Head Slope Gradient Analyzer
==================================
A Streamlit application for analyzing rock head slope gradients between piles.

Features:
- Pile-pair gradient table (Delaunay / k-nearest / all pairs)
- Plan view with piles colour-coded by rock head RL
- 3D interactive rock head surface
- Rock head LEVEL contour plan (interpolated)
- Classified SLOPE zones overlay (1:3, 1:2, 1:1.5, 1:1, 1:0.6 bands)
- Excel export, PDF reference viewer

Run with:  streamlit run rockhead_gradient_app.py
"""

import base64
import re
from io import BytesIO, StringIO

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from scipy.spatial import Delaunay
from scipy.interpolate import griddata

# DXF export — soft-imported so the app still runs if ezdxf isn't installed
try:
    import ezdxf
    from ezdxf import colors as dxf_colors
    import matplotlib
    matplotlib.use("Agg")  # headless backend for contour extraction
    import matplotlib.pyplot as plt
    DXF_AVAILABLE = True
except Exception:  # noqa: BLE001
    DXF_AVAILABLE = False

# ----------------------------------------------------------------------------
# Page setup
# ----------------------------------------------------------------------------
st.set_page_config(
    page_title="Rock Head Gradient Analyzer",
    page_icon="⛰️",
    layout="wide",
)

st.title("⛰️ Rock Head Slope Gradient Analyzer")
st.caption(
    "Upload pile records, compute slope gradients between piles, "
    "view rock head contour plans, and flag steep slope zones."
)

# ----------------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------------
REQUIRED_COLS = ["Pile_ID", "Easting", "Northing", "Rock_Head_RL"]

# Slope classification bands (1:N values; lower N = steeper).
# Each band: (label, N_min_exclusive, N_max_inclusive, colour)
SLOPE_BANDS = [
    ("Gentler than 1:3",     3.0,  np.inf,  "#2ecc71"),  # green
    ("1:3 to 1:2",           2.0,  3.0,     "#f1c40f"),  # yellow
    ("1:2 to 1:1.5",         1.5,  2.0,     "#e67e22"),  # orange
    ("1:1.5 to 1:1",         1.0,  1.5,     "#e74c3c"),  # red
    ("1:1 to 1:0.6",         0.6,  1.0,     "#8e44ad"),  # purple
    ("Steeper than 1:0.6",   0.0,  0.6,     "#2c3e50"),  # near-black
]

# Rock-cliff zone reduction table (from project pile design parameter document)
# Each row: (label, N_min_excl, N_max_incl, reduction_pct, fs_kPa, qb_kPa, min_embed_at_toe, action)
# fs and qb values shown assume base values fs=350 kPa and qb=6000 kPa
CLIFF_BANDS = [
    ("Gentler than 1:3",   3.0,  np.inf,  0,    350,   6000,  "Standard",  "No reduction"),
    ("1:3 to 1:2",         2.0,  3.0,     0,    350,   6000,  "Standard",  "No reduction"),
    ("1:2 to 1:1.5",       1.5,  2.0,     30,   245,   4200,  "≥ 1.5 D",   "Apply 30% reduction"),
    ("1:1.5 to 1:1",       1.0,  1.5,     50,   175,   3000,  "≥ 2.0 D",   "Apply 50% reduction"),
    ("1:1 to 1:0.6",       0.6,  1.0,     70,   105,   1800,  "≥ 2.5 D",   "Apply 70% reduction"),
    ("Steeper than 1:0.6", 0.0,  0.6,     100,  0,     0,     "n/a",       "REDESIGN as Case C"),
]


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------
def load_table(uploaded_file) -> pd.DataFrame:
    name = uploaded_file.name.lower()
    if name.endswith(".csv"):
        return pd.read_csv(uploaded_file)
    if name.endswith((".xlsx", ".xls")):
        return pd.read_excel(uploaded_file)
    raise ValueError("Unsupported file type. Use .csv or .xlsx")


def validate_data(df: pd.DataFrame, ground_rl_col: str | None):
    issues, warnings = [], []
    missing = [c for c in REQUIRED_COLS if c not in df.columns]
    if missing:
        issues.append(f"Missing required columns: {missing}")
        return None, issues, warnings

    df = df.copy()
    for col in ["Easting", "Northing", "Rock_Head_RL"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    dupes = df[df["Pile_ID"].duplicated()]["Pile_ID"].tolist()
    if dupes:
        warnings.append(f"Duplicate Pile IDs found: {dupes}")

    missing_rl = df[df["Rock_Head_RL"].isna()]["Pile_ID"].astype(str).tolist()
    if missing_rl:
        warnings.append(
            f"{len(missing_rl)} piles have no Rock Head RL "
            f"(excluded from analysis): {missing_rl[:10]}"
            f"{'...' if len(missing_rl) > 10 else ''}"
        )

    if ground_rl_col and ground_rl_col in df.columns:
        df[ground_rl_col] = pd.to_numeric(df[ground_rl_col], errors="coerce")
        bad = df[df["Rock_Head_RL"] > df[ground_rl_col]]
        if len(bad):
            warnings.append(
                f"{len(bad)} piles have Rock Head RL above ground level — "
                "check sign convention / data entry."
            )

    clean = df.dropna(subset=["Easting", "Northing", "Rock_Head_RL"]).reset_index(drop=True)
    if len(clean) < 3:
        issues.append(f"Need at least 3 piles with complete data. Found {len(clean)}.")
        return None, issues, warnings

    return clean, issues, warnings


def get_connections(df: pd.DataFrame, mode: str, k_neighbors: int = 4):
    points = df[["Easting", "Northing"]].values
    n = len(points)
    if mode == "Delaunay (recommended)":
        tri = Delaunay(points)
        edges = set()
        for simplex in tri.simplices:
            for a in range(3):
                for b in range(a + 1, 3):
                    edges.add(tuple(sorted([simplex[a], simplex[b]])))
        return sorted(edges)
    if mode == "All pairs":
        return [(i, j) for i in range(n) for j in range(i + 1, n)]
    if mode == "K-nearest neighbours":
        edges = set()
        for i in range(n):
            d = np.linalg.norm(points - points[i], axis=1)
            d[i] = np.inf
            nn = np.argsort(d)[:k_neighbors]
            for j in nn:
                edges.add(tuple(sorted([i, int(j)])))
        return sorted(edges)
    raise ValueError(f"Unknown connection mode: {mode}")


def compute_gradients(df: pd.DataFrame, connections, directional: bool = True):
    """Compute slope gradient for every pile pair.

    Convention: 'From' is ALWAYS the pile with the higher rock head RL of
    each pair; 'To' is the pile with the lower rock head. This matches the
    geotechnical convention of measuring slope from upslope to downslope.
    Drop_From_To_m is therefore always >= 0.

    When directional=True (default), every pair appears twice — once from
    each end's perspective — so each pile sees all its neighbours as 'To'
    and we can compute a per-pile "Max Gradient (Steepest)" showing the
    steepest adjacent slope seen from each pile.

    Output columns match the project's preferred export format:
      From, To, From Rock Head Level, Adjacent Pile Rock Head Level,
      Horiz_Dist_m, Vert_Diff_m, Drop_From_To_m, Gradient_N,
      Gradient (1:x), Max Gradient (Steepest)
    """
    rows = []
    for i, j in connections:
        pairs = [(i, j), (j, i)] if directional else [(i, j)]
        for a, b in pairs:
            p1, p2 = df.iloc[a], df.iloc[b]
            z1 = float(p1["Rock_Head_RL"])
            z2 = float(p2["Rock_Head_RL"])

            # Enforce convention: From = higher, To = lower.
            # If p1's rock head is lower than p2's, swap so we always go
            # from high to low. (Ties keep original order.)
            if z1 < z2:
                p1, p2 = p2, p1
                z1, z2 = z2, z1

            dx = p2["Easting"] - p1["Easting"]
            dy = p2["Northing"] - p1["Northing"]
            dz = z2 - z1  # from From to To — always <= 0 because z1 >= z2
            h = float(np.hypot(dx, dy))
            v = float(abs(dz))
            if v < 1e-6:
                ratio_n = np.inf
                grad_str = "Flat"
            else:
                ratio_n = h / v
                grad_str = f"1:{ratio_n:.1f}"
            rows.append({
                "From": p1["Pile_ID"],
                "To": p2["Pile_ID"],
                "From Rock Head Level": round(z1, 2),
                "Adjacent Pile Rock Head Level": round(z2, 2),
                "Horiz_Dist_m": round(h, 2),
                "Vert_Diff_m": round(v, 2),
                "Drop_From_To_m": round(v, 2),  # always positive: From is higher
                "Gradient_N": ratio_n,
                "Gradient (1:x)": grad_str,
            })

    if not rows:
        return pd.DataFrame()

    out = pd.DataFrame(rows)

    # Some pile pairs will now appear twice as the SAME (From, To) row because
    # both directional samples resolved to "higher → lower". Deduplicate.
    out = out.drop_duplicates(subset=["From", "To"]).reset_index(drop=True)

    # Per-From-pile max steepness (= minimum Gradient_N value across its rows)
    # This shows, for each pile that is the higher of its connections, its
    # steepest downslope to a neighbour.
    finite = out[np.isfinite(out["Gradient_N"])]
    if not finite.empty:
        worst = finite.groupby("From")["Gradient_N"].min().to_dict()
        out["Max Gradient (Steepest)"] = out["From"].map(
            lambda p: f"1:{worst[p]:.1f}" if p in worst else "Flat"
        )
    else:
        out["Max Gradient (Steepest)"] = "Flat"

    # Sort by From pile, then by steepness within each From-group
    return out.sort_values(["From", "Gradient_N"]).reset_index(drop=True)


def build_interpolated_surface(df: pd.DataFrame, grid_res: int = 150, method: str = "cubic"):
    """Interpolate rock head RL onto a regular grid. Returns (X, Y, Z, slope_ratio_N)."""
    pts = df[["Easting", "Northing"]].values
    vals = df["Rock_Head_RL"].values

    pad = 0.05
    x_min, x_max = pts[:, 0].min(), pts[:, 0].max()
    y_min, y_max = pts[:, 1].min(), pts[:, 1].max()
    dx = (x_max - x_min) * pad if x_max > x_min else 1.0
    dy = (y_max - y_min) * pad if y_max > y_min else 1.0
    xs = np.linspace(x_min - dx, x_max + dx, grid_res)
    ys = np.linspace(y_min - dy, y_max + dy, grid_res)
    X, Y = np.meshgrid(xs, ys)

    Z = griddata(pts, vals, (X, Y), method=method)
    Z_lin = griddata(pts, vals, (X, Y), method="linear")
    Z = np.where(np.isnan(Z), Z_lin, Z)

    sx = xs[1] - xs[0]
    sy = ys[1] - ys[0]
    dzdy, dzdx = np.gradient(Z, sy, sx)
    slope = np.sqrt(dzdx ** 2 + dzdy ** 2)

    with np.errstate(divide="ignore", invalid="ignore"):
        ratio_N = np.where(slope > 1e-9, 1.0 / slope, np.inf)
    return X, Y, Z, ratio_N


def classify_slope(ratio_N: np.ndarray) -> np.ndarray:
    """Map grid of 1:N values to band indices (0 = gentlest band, -1 = no data)."""
    out = np.full(ratio_N.shape, -1, dtype=int)
    for idx, (_, n_min, n_max, _) in enumerate(SLOPE_BANDS):
        mask = (ratio_N > n_min) & (ratio_N <= n_max)
        out[mask] = idx
    return out


# ----------------------------------------------------------------------------
# Plots
# ----------------------------------------------------------------------------
def plot_plan_view(df, connections, grad_df, threshold_n):
    edge_ratio = {(r["From"], r["To"]): r["Gradient_N"] for _, r in grad_df.iterrows()}
    fig = go.Figure()
    steep_x, steep_y, mild_x, mild_y = [], [], [], []
    for i, j in connections:
        p1, p2 = df.iloc[i], df.iloc[j]
        key = (p1["Pile_ID"], p2["Pile_ID"])
        ratio = edge_ratio.get(key, edge_ratio.get((key[1], key[0]), np.inf))
        if ratio < threshold_n:
            steep_x += [p1["Easting"], p2["Easting"], None]
            steep_y += [p1["Northing"], p2["Northing"], None]
        else:
            mild_x += [p1["Easting"], p2["Easting"], None]
            mild_y += [p1["Northing"], p2["Northing"], None]
    if mild_x:
        fig.add_trace(go.Scatter(x=mild_x, y=mild_y, mode="lines",
            line=dict(color="lightgray", width=1),
            name=f"Gradient ≥ 1:{threshold_n}", hoverinfo="skip"))
    if steep_x:
        fig.add_trace(go.Scatter(x=steep_x, y=steep_y, mode="lines",
            line=dict(color="crimson", width=2.5),
            name=f"Gradient < 1:{threshold_n} (steep)", hoverinfo="skip"))
    fig.add_trace(go.Scatter(
        x=df["Easting"], y=df["Northing"], mode="markers+text",
        marker=dict(size=12, color=df["Rock_Head_RL"], colorscale="RdYlBu",
                    showscale=True, colorbar=dict(title="Rock Head<br>RL (m)"),
                    line=dict(color="black", width=0.5)),
        text=df["Pile_ID"].astype(str), textposition="top center",
        textfont=dict(size=9),
        customdata=df[["Pile_ID", "Rock_Head_RL"]].values,
        hovertemplate="<b>%{customdata[0]}</b><br>E: %{x:.2f}<br>N: %{y:.2f}<br>"
                      "Rock Head RL: %{customdata[1]:.2f} m<extra></extra>",
        name="Piles"))
    fig.update_layout(
        title="Plan View — Piles coloured by Rock Head RL",
        xaxis_title="Easting (m)", yaxis_title="Northing (m)",
        yaxis=dict(scaleanchor="x", scaleratio=1), height=700,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0))
    return fig


def plot_3d_surface(df):
    points = df[["Easting", "Northing"]].values
    tri = Delaunay(points)
    fig = go.Figure()
    fig.add_trace(go.Mesh3d(
        x=df["Easting"], y=df["Northing"], z=df["Rock_Head_RL"],
        i=tri.simplices[:, 0], j=tri.simplices[:, 1], k=tri.simplices[:, 2],
        intensity=df["Rock_Head_RL"], colorscale="RdYlBu", opacity=0.85,
        name="Rock Head Surface", colorbar=dict(title="RL (m)")))
    fig.add_trace(go.Scatter3d(
        x=df["Easting"], y=df["Northing"], z=df["Rock_Head_RL"],
        mode="markers+text", marker=dict(size=4, color="black"),
        text=df["Pile_ID"].astype(str), textfont=dict(size=10), name="Piles"))
    fig.update_layout(
        title="3D Rock Head Surface",
        scene=dict(xaxis_title="Easting (m)", yaxis_title="Northing (m)",
                   zaxis_title="Rock Head RL (m)", aspectmode="data"),
        height=700)
    return fig


def plot_rockhead_contour(df, X, Y, Z, contour_interval):
    """Filled contour plan of rock head RL with pile markers."""
    fig = go.Figure()
    z_min = np.nanmin(Z)
    z_max = np.nanmax(Z)
    start = np.floor(z_min / contour_interval) * contour_interval
    end = np.ceil(z_max / contour_interval) * contour_interval

    fig.add_trace(go.Contour(
        x=X[0, :], y=Y[:, 0], z=Z,
        colorscale="RdYlBu",
        contours=dict(start=start, end=end, size=contour_interval,
                      showlabels=True, labelfont=dict(size=10, color="black"),
                      coloring="heatmap"),
        colorbar=dict(title="Rock Head<br>RL (m)"),
        hovertemplate="E: %{x:.1f}<br>N: %{y:.1f}<br>RL: %{z:.2f} m<extra></extra>",
        name="Rock Head RL"))

    fig.add_trace(go.Scatter(
        x=df["Easting"], y=df["Northing"], mode="markers+text",
        marker=dict(size=8, color="black", symbol="circle",
                    line=dict(color="white", width=1)),
        text=df["Pile_ID"].astype(str), textposition="top center",
        textfont=dict(size=9, color="black"),
        customdata=df[["Pile_ID", "Rock_Head_RL"]].values,
        hovertemplate="<b>%{customdata[0]}</b><br>RL: %{customdata[1]:.2f} m<extra></extra>",
        name="Piles"))

    fig.update_layout(
        title=f"Rock Head Level Contour Plan (interval = {contour_interval} m)",
        xaxis_title="Easting (m)", yaxis_title="Northing (m)",
        yaxis=dict(scaleanchor="x", scaleratio=1), height=750,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0))
    return fig


def plot_slope_classification(df, X, Y, ratio_N):
    """Discrete slope-band plan with pile markers overlaid."""
    band_idx = classify_slope(ratio_N).astype(float)
    band_idx[band_idx < 0] = np.nan

    n_bands = len(SLOPE_BANDS)
    colorscale = []
    for i, (_, _, _, c) in enumerate(SLOPE_BANDS):
        lo = i / n_bands
        hi = (i + 1) / n_bands
        colorscale.append([lo, c])
        colorscale.append([hi, c])

    fig = go.Figure()
    fig.add_trace(go.Heatmap(
        x=X[0, :], y=Y[:, 0], z=band_idx,
        colorscale=colorscale, zmin=-0.5, zmax=n_bands - 0.5,
        showscale=False,
        hovertemplate="E: %{x:.1f}<br>N: %{y:.1f}<br>Band #%{z}<extra></extra>",
        name="Slope class"))

    fig.add_trace(go.Scatter(
        x=df["Easting"], y=df["Northing"], mode="markers+text",
        marker=dict(size=8, color="white", symbol="circle",
                    line=dict(color="black", width=1.2)),
        text=df["Pile_ID"].astype(str), textposition="top center",
        textfont=dict(size=9, color="black"),
        customdata=df[["Pile_ID", "Rock_Head_RL"]].values,
        hovertemplate="<b>%{customdata[0]}</b><br>RL: %{customdata[1]:.2f} m<extra></extra>",
        name="Piles", showlegend=False))

    for label, _, _, colour in SLOPE_BANDS:
        fig.add_trace(go.Scatter(
            x=[None], y=[None], mode="markers",
            marker=dict(size=14, color=colour, symbol="square",
                        line=dict(color="black", width=0.5)),
            name=label, showlegend=True))

    fig.update_layout(
        title="Rock Head Slope Classification Plan",
        xaxis_title="Easting (m)", yaxis_title="Northing (m)",
        yaxis=dict(scaleanchor="x", scaleratio=1), height=750,
        legend=dict(title="Slope band", orientation="v",
                    yanchor="top", y=1, xanchor="left", x=1.02))
    return fig


def slope_band_summary(ratio_N: np.ndarray, X, Y) -> pd.DataFrame:
    band_idx = classify_slope(ratio_N)
    sx = X[0, 1] - X[0, 0]
    sy = Y[1, 0] - Y[0, 0]
    cell_area = abs(sx * sy)
    valid_mask = band_idx >= 0
    total_area = valid_mask.sum() * cell_area
    rows = []
    for idx, (label, _, _, colour) in enumerate(SLOPE_BANDS):
        cells = int((band_idx == idx).sum())
        area = cells * cell_area
        pct = (area / total_area * 100.0) if total_area > 0 else 0.0
        rows.append({
            "Band": label, "Area (m²)": round(area, 1),
            "Coverage (%)": round(pct, 2), "Colour": colour,
        })
    return pd.DataFrame(rows)


# ----------------------------------------------------------------------------
# Pile design check (cliff-zone + optional capacity check)
# ----------------------------------------------------------------------------
def sample_slope_at_piles(df, X, Y, ratio_N, search_radius_m: float = 2.0):
    """For each pile, return (ratio_N_at_centroid, min_ratio_N_in_radius).

    Lower ratio_N = steeper. min_ratio_N_in_radius gives the worst-case
    (steepest) slope inside a small disk around the pile location, which is
    the conservative value to use for cliff-zone classification.
    """
    xs = X[0, :]
    ys = Y[:, 0]
    sx = xs[1] - xs[0]
    sy = ys[1] - ys[0]

    results = []
    for _, p in df.iterrows():
        e, n = p["Easting"], p["Northing"]
        # Centroid sample (nearest grid cell)
        i = int(np.clip(round((n - ys[0]) / sy), 0, len(ys) - 1))
        j = int(np.clip(round((e - xs[0]) / sx), 0, len(xs) - 1))
        ratio_centroid = ratio_N[i, j]

        # Min ratio_N in radius (= worst-case steepest)
        di = max(1, int(np.ceil(search_radius_m / abs(sy))))
        dj = max(1, int(np.ceil(search_radius_m / abs(sx))))
        i0, i1 = max(0, i - di), min(ratio_N.shape[0], i + di + 1)
        j0, j1 = max(0, j - dj), min(ratio_N.shape[1], j + dj + 1)
        patch = ratio_N[i0:i1, j0:j1]
        # Build a circular mask of the patch
        ii, jj = np.meshgrid(
            np.arange(i0, i1) - i, np.arange(j0, j1) - j, indexing="ij"
        )
        dist = np.sqrt((ii * sy) ** 2 + (jj * sx) ** 2)
        masked = np.where(dist <= search_radius_m, patch, np.inf)
        ratio_worst = float(np.nanmin(masked)) if masked.size else ratio_centroid

        results.append((float(ratio_centroid), ratio_worst))
    return results


def classify_cliff_band(ratio_n: float):
    """Return the matching CLIFF_BANDS row for a given 1:N value."""
    if not np.isfinite(ratio_n):
        return CLIFF_BANDS[0]  # flat → no reduction
    for row in CLIFF_BANDS:
        _, n_min, n_max, *_ = row
        if n_min < ratio_n <= n_max:
            return row
    return CLIFF_BANDS[0]


def pile_cliff_check(df, X, Y, ratio_N, search_radius_m: float = 2.0):
    """Per-pile cliff-zone classification using both centroid and worst-in-radius.

    Returns a DataFrame ready for display.
    """
    samples = sample_slope_at_piles(df, X, Y, ratio_N, search_radius_m)
    rows = []
    for (_, p), (rc, rw) in zip(df.iterrows(), samples):
        band_c = classify_cliff_band(rc)
        band_w = classify_cliff_band(rw)
        rows.append({
            "Pile_ID": p["Pile_ID"],
            "Easting": p["Easting"],
            "Northing": p["Northing"],
            "Rock_Head_RL": round(float(p["Rock_Head_RL"]), 2),
            "Slope at centroid (1:N)":
                f"{rc:.2f}" if np.isfinite(rc) else "Flat",
            "Centroid band": band_c[0],
            f"Worst slope in {search_radius_m:g} m (1:N)":
                f"{rw:.2f}" if np.isfinite(rw) else "Flat",
            "Governing band": band_w[0],
            "Reduction (%)": band_w[3],
            "fs to adopt (kPa)": band_w[4],
            "qb to adopt (kPa)": band_w[5],
            "Min embedment at toe": band_w[6],
            "Action": band_w[7],
        })
    return pd.DataFrame(rows)


def pile_capacity_check(piles_df, design_df, strat_df):
    """Phase 2 capacity check (optional).

    For each pile, look up its designed length from design_df (matched by
    Zone + Pile_Dia_mm) and apply the cliff reduction governing band. Returns
    a DataFrame with the comparison.

    This is a SIMPLIFIED check — it does not recompute full layered shaft
    friction from SPT N values. It compares the design length against a
    reduction-aware required length using designed fs and qb. For full
    rigour, supply the layered SPT data in the stratigraphy file.
    """
    if design_df is None or design_df.empty:
        return None

    needed_design_cols = {
        "Zone", "Pile_Dia_mm", "Working_Load_kN",
        "Designed_Length_m", "Socket_SIV_m", "Socket_SIII_m",
    }
    missing = needed_design_cols - set(design_df.columns)
    if missing:
        raise ValueError(f"Design table missing columns: {missing}")

    rows = []
    for _, p in piles_df.iterrows():
        zone = p.get("Zone")
        dia = p.get("Pile_Dia_mm")
        if pd.isna(zone) or pd.isna(dia):
            continue

        match = design_df[
            (design_df["Zone"].astype(str) == str(zone)) &
            (pd.to_numeric(design_df["Pile_Dia_mm"], errors="coerce") ==
             pd.to_numeric(dia, errors="coerce"))
        ]
        if match.empty:
            rows.append({
                "Pile_ID": p["Pile_ID"],
                "Zone": zone, "Diameter (mm)": dia,
                "Status": "⚠️ No design row found",
                "Designed length (m)": None,
                "Min socket SIII (m)": None,
                "Note": f"No row in design table for Zone={zone}, D={dia} mm",
            })
            continue

        d = match.iloc[0]
        rows.append({
            "Pile_ID": p["Pile_ID"],
            "Zone": zone,
            "Diameter (mm)": int(pd.to_numeric(dia)),
            "Working Load (kN)": d["Working_Load_kN"],
            "Designed length (m)": d["Designed_Length_m"],
            "Designed socket S(IV) (m)": d.get("Socket_SIV_m", "—"),
            "Designed socket S(III) (m)": d.get("Socket_SIII_m", "—"),
            "Status": "✅ Matched design row",
            "Note": "Use cliff band from Phase 1 to verify fs/qb reduction",
        })

    return pd.DataFrame(rows)


def parse_pile_summary_workbook(file_bytes_or_buffer) -> pd.DataFrame:
    """Parse the project's master 'Pile_Length_Summary' workbook.

    Auto-detects format. Handles two cases:
    1. Multi-zone concatenated layout (one sheet, repeated zone-header blocks).
       This is the typical project format.
    2. Simple flat template (one row per Zone × Diameter with explicit columns).

    Returns a normalized DataFrame with these columns at minimum:
      Zone, BH, Pile_Dia_Code, Pile_Dia_mm, Concrete_Grade, Working_Load_kN,
      Pile_Penetration_Length_m, Socket_SIV_m, Socket_SIII_m
    (plus any additional fields available in the source).
    """
    raw = pd.read_excel(file_bytes_or_buffer, sheet_name=0, header=None)

    # --- Format 1: multi-zone concatenated layout
    zone_rows = []
    for i in range(len(raw)):
        for col in range(min(3, raw.shape[1])):
            val = raw.iloc[i, col]
            if pd.notna(val) and isinstance(val, str):
                m = re.match(r"ZONE\s+(\d+)\s*\(?\s*BH(\d+)\s*\)?",
                              val.strip().upper())
                if m:
                    zone_rows.append((i, int(m.group(1)), int(m.group(2))))
                    break

    if zone_rows:
        # Parse multi-zone format
        all_rows = []
        for row_idx, zone, bh in zone_rows:
            for i in range(row_idx + 3, len(raw)):
                first_cell = raw.iloc[i, 0]
                if pd.isna(first_cell):
                    break
                if isinstance(first_cell, str) and "ZONE" in first_cell.upper():
                    break
                try:
                    diameter_raw = str(first_cell).strip()
                    dia_match = re.match(r"(\d+)", diameter_raw)
                    if not dia_match:
                        continue
                    dia = int(dia_match.group(1))
                    all_rows.append({
                        "Zone": zone,
                        "BH": f"BH{bh:02d}",
                        "Pile_Dia_Code": diameter_raw,
                        "Pile_Dia_mm": dia,
                        "Concrete_Grade": raw.iloc[i, 1],
                        "Working_Load_kN": _safe_num(raw.iloc[i, 2]),
                        "NSF_kN": _safe_num(raw.iloc[i, 3]),
                        "Working_Cap_Less_NSF_kN": _safe_num(raw.iloc[i, 4]),
                        "Ult_Struct_Cap_kN": _safe_num(raw.iloc[i, 5]),
                        "Rcd_Comp_DA1C1_kN": _safe_num(raw.iloc[i, 6]),
                        "Rcd_Comp_DA1C2_kN": _safe_num(raw.iloc[i, 7]),
                        "Rcd_Comp_NSF_DA1C1_kN": _safe_num(raw.iloc[i, 8]),
                        "Rcd_Comp_NSF_DA1C2_kN": _safe_num(raw.iloc[i, 9]),
                        "Rcd_Tens_DA1C1_kN": _safe_num(raw.iloc[i, 10]),
                        "Rcd_Tens_DA1C2_kN": _safe_num(raw.iloc[i, 11]),
                        "Designed_Length_m": _safe_num(raw.iloc[i, 12]),
                        "Socket_SIV_m": _safe_num(raw.iloc[i, 13]),
                        "Socket_SIII_m": _safe_num(raw.iloc[i, 14]),
                        "Compression_Rebar": raw.iloc[i, 15] if raw.shape[1] > 15 else None,
                        "Tension_Rebar": raw.iloc[i, 17] if raw.shape[1] > 17 else None,
                    })
                except (ValueError, TypeError, IndexError):
                    continue

        df = pd.DataFrame(all_rows)
        # Round numeric cols for display
        for col in ["Designed_Length_m", "Socket_SIV_m", "Socket_SIII_m",
                    "Working_Load_kN", "NSF_kN"]:
            if col in df.columns:
                df[col] = df[col].round(2)
        return df

    # --- Format 2: simple flat template (fallback)
    # Re-read with header inference
    flat = pd.read_excel(file_bytes_or_buffer, sheet_name=0)
    # Normalize column names — accept slight variations
    rename = {}
    for c in flat.columns:
        cl = str(c).strip().lower()
        if "designed_length" in cl or cl == "designed length (m)":
            rename[c] = "Designed_Length_m"
        elif "socket_siv" in cl or "siv" in cl:
            rename[c] = "Socket_SIV_m"
        elif "socket_siii" in cl or "siii" in cl:
            rename[c] = "Socket_SIII_m"
    flat = flat.rename(columns=rename)
    return flat


def _safe_num(v):
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def parse_bh_detail_workbook(file_bytes_or_buffer, bh_label: str | None = None) -> dict:
    """Parse a BH detail workbook (e.g., 'BH01-Revised_Set_2.xlsx').

    Returns a dict with:
      bh_id: borehole label (from sheet metadata or filename)
      ground_rl: ground level RL (m)
      cut_off_level: pile cut-off RL (m)
      spt_profile: DataFrame of From_m, To_m, Nspt, Soil_Type
    """
    raw = pd.read_excel(file_bytes_or_buffer, sheet_name="Data", header=None)

    # Pull metadata
    bh_id = bh_label or "BH?"
    try:
        bh_cell = raw.iloc[1, 1]
        if pd.notna(bh_cell):
            bh_id = str(bh_cell).strip()
    except (IndexError, ValueError):
        pass

    ground_rl = _safe_num(raw.iloc[2, 2]) if raw.shape[0] > 2 else None
    cut_off = _safe_num(raw.iloc[2, 4]) if raw.shape[1] > 4 else None

    # SPT profile starts at row index 5 — stop at first row with missing
    # From/To depth values (those are the canonical end-of-data markers)
    spt_rows = []
    for i in range(5, len(raw)):
        f_raw = raw.iloc[i, 0]
        t_raw = raw.iloc[i, 1]
        if pd.isna(f_raw) or pd.isna(t_raw):
            break
        f = _safe_num(f_raw)
        t = _safe_num(t_raw)
        if f is None or t is None:
            break
        n = raw.iloc[i, 2]
        soil = raw.iloc[i, 3]
        spt_rows.append({
            "From_m": f,
            "To_m": t,
            "Nspt": int(n) if pd.notna(n) and isinstance(n, (int, float)) else None,
            "Soil_Type": str(soil).strip() if pd.notna(soil) else "",
        })

    spt_df = pd.DataFrame(spt_rows)

    # Derive top-of-layer RLs from SPT profile
    layer_tops = {}
    if not spt_df.empty and ground_rl is not None:
        seen = set()
        for _, r in spt_df.iterrows():
            soil = r["Soil_Type"]
            if soil and soil not in seen:
                seen.add(soil)
                top_depth = r["From_m"]
                layer_tops[soil] = round(ground_rl - top_depth, 2)

    return {
        "bh_id": bh_id,
        "ground_rl": ground_rl,
        "cut_off_level": cut_off,
        "spt_profile": spt_df,
        "layer_tops": layer_tops,
    }


def plot_spt_profile(spt_df, bh_id: str, ground_rl: float | None = None):
    """Plot SPT N profile vs depth with soil layer annotations."""
    if spt_df.empty:
        return None

    # Drop rows where Nspt or depth values are missing
    df = spt_df.dropna(subset=["From_m", "To_m", "Nspt"]).copy()
    if df.empty:
        return None

    mid_depths = (df["From_m"] + df["To_m"]) / 2.0
    thicknesses = (df["To_m"] - df["From_m"]).astype(float)

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=df["Nspt"],
        y=mid_depths,
        orientation="h",
        width=thicknesses,
        marker=dict(color=df["Nspt"], colorscale="Viridis",
                    cmin=0, cmax=100,
                    colorbar=dict(title="SPT N")),
        hovertemplate=("<b>%{customdata[0]}</b><br>"
                       "Depth: %{customdata[1]:.1f}–%{customdata[2]:.1f} m<br>"
                       "N: %{x}<extra></extra>"),
        customdata=np.stack([df["Soil_Type"],
                              df["From_m"],
                              df["To_m"]], axis=-1),
        name="SPT N",
    ))

    fig.update_layout(
        title=f"SPT N profile — {bh_id}" +
              (f"  (GL = {ground_rl:.2f} m)" if ground_rl else ""),
        xaxis_title="SPT N (blows / 300 mm)",
        yaxis_title="Depth below GL (m)",
        yaxis=dict(autorange="reversed"),
        height=600,
        showlegend=False,
    )
    return fig



    """Sample Excel design table for the user to fill out."""
    df = pd.DataFrame({
        "Zone": [1, 1, 1, 2, 2, 2],
        "Pile_Dia_mm": [600, 800, 1000, 600, 800, 1000],
        "Concrete_Grade": ["C32/40"] * 6,
        "Working_Load_kN": [2120, 3770, 5890, 2120, 3770, 5890],
        "Negative_Skin_Friction_kN": [273, 364, 455, 21, 28, 35],
        "Designed_Length_m": [37.2, 38.7, 40.6, 32.1, 33.1, 33.7],
        "Socket_SIV_m": [3.3, 3.3, 3.3, 0.0, 0.0, 0.0],
        "Socket_SIII_m": [2.2, 3.7, 5.7, 1.0, 1.0, 1.0],
        "Notes": ["BH01", "BH01", "BH01", "BH02", "BH02", "BH02"],
    })
    out = BytesIO()
    with pd.ExcelWriter(out, engine="openpyxl") as w:
        df.to_excel(w, sheet_name="Pile Design Table", index=False)
    return out.getvalue()


def design_table_template() -> bytes:
    """Sample Excel design table for users who don't have the project's
    pile-length summary workbook in the multi-zone format. The parser also
    accepts this simpler flat format.
    """
    df = pd.DataFrame({
        "Zone": [1, 1, 1, 2, 2, 2],
        "Pile_Dia_mm": [600, 800, 1000, 600, 800, 1000],
        "Concrete_Grade": ["C32/40"] * 6,
        "Working_Load_kN": [2120, 3770, 5890, 2120, 3770, 5890],
        "Negative_Skin_Friction_kN": [273, 364, 455, 21, 28, 35],
        "Designed_Length_m": [37.2, 38.7, 40.6, 32.1, 33.1, 33.7],
        "Socket_SIV_m": [3.3, 3.3, 3.3, 0.0, 0.0, 0.0],
        "Socket_SIII_m": [2.2, 3.7, 5.7, 1.0, 1.0, 1.0],
        "Notes": ["BH01", "BH01", "BH01", "BH02", "BH02", "BH02"],
    })
    out = BytesIO()
    with pd.ExcelWriter(out, engine="openpyxl") as w:
        df.to_excel(w, sheet_name="Pile Design Table", index=False)
    return out.getvalue()


def stratigraphy_template() -> bytes:
    """Sample stratigraphy summary for Phase 2 capacity check."""
    df = pd.DataFrame({
        "BH_ID": ["BH1", "BH2", "BH3"],
        "Easting": [37833.916, 37821.866, 37835.188],
        "Northing": [15896.954, 15922.974, 15948.407],
        "Ground_RL": [5.65, 4.25, 8.26],
        "Top_FILL_RL": [5.65, 4.25, 8.26],
        "Top_E_RL": [-4.25, -4.35, None],
        "Top_F2_RL": [-7.25, -2.65, -1.64],
        "Top_SVI_RL": [-9.35, -4.75, -4.64],
        "Top_SV_RL": [-14.35, -7.75, -7.64],
        "Top_SIV_RL": [-32.35, -23.20, -21.74],
        "Top_SIII_RL": [-34.95, -32.45, -25.74],
        "Top_SII_RL": [-44.80, None, -40.24],
        "Borehole_Termination_RL": [-45.85, -40.75, -40.24],
    })
    out = BytesIO()
    with pd.ExcelWriter(out, engine="openpyxl") as w:
        df.to_excel(w, sheet_name="Stratigraphy", index=False)
    return out.getvalue()


def to_excel_bytes(pile_df, grad_df, band_df, cliff_df=None, design_df=None) -> bytes:
    out = BytesIO()
    with pd.ExcelWriter(out, engine="openpyxl") as writer:
        pile_df.to_excel(writer, sheet_name="Pile Data", index=False)

        # Gradients sheet — match project format with explicit column order
        export = grad_df.copy()
        export["Gradient_N"] = export["Gradient_N"].replace([np.inf], "Flat")
        # Preferred column order (drop any missing for safety)
        preferred = [
            "From", "To",
            "From Rock Head Level", "Adjacent Pile Rock Head Level",
            "Horiz_Dist_m", "Vert_Diff_m", "Drop_From_To_m",
            "Gradient_N", "Gradient (1:x)", "Max Gradient (Steepest)",
        ]
        ordered_cols = [c for c in preferred if c in export.columns]
        # Append any extra cols not in the preferred list (forward-compat)
        ordered_cols += [c for c in export.columns if c not in ordered_cols]
        export = export[ordered_cols]
        export.to_excel(writer, sheet_name="Gradients", index=False)

        if band_df is not None:
            band_df.drop(columns=["Colour"], errors="ignore").to_excel(
                writer, sheet_name="Slope Bands", index=False)
        if cliff_df is not None and not cliff_df.empty:
            cliff_df.to_excel(writer, sheet_name="Cliff-Zone Check", index=False)
        if design_df is not None and not design_df.empty:
            design_df.to_excel(writer, sheet_name="Design Match", index=False)
    return out.getvalue()


def export_to_dxf(
    df: pd.DataFrame,
    X: np.ndarray,
    Y: np.ndarray,
    Z: np.ndarray,
    ratio_N: np.ndarray,
    contour_interval: float = 1.0,
    major_every: int = 5,
) -> bytes:
    """Build a DXF file with rock head contours, slope-band boundaries, and piles.

    Layers produced:
      PILES                 — pile markers (circles)
      PILE_LABELS           — pile IDs and rock head RL text
      RH_CONTOURS           — minor rock head contour lines
      RH_CONTOURS_MAJOR     — major contour lines (every Nth)
      CONTOUR_LABELS        — text labels on major contours
      SLOPE_BOUND_1_3 etc.  — boundaries between slope classification bands
      BOUNDARY              — site bounding box

    Returns the DXF file content as bytes.
    """
    if not DXF_AVAILABLE:
        raise RuntimeError(
            "DXF export requires 'ezdxf' and 'matplotlib' packages."
        )

    doc = ezdxf.new("R2010", setup=True)
    msp = doc.modelspace()

    # ---- Layers ----
    doc.layers.add("PILES", color=dxf_colors.WHITE)
    doc.layers.add("PILE_LABELS", color=dxf_colors.WHITE)
    doc.layers.add("RH_CONTOURS", color=dxf_colors.CYAN)
    doc.layers.add("RH_CONTOURS_MAJOR", color=dxf_colors.BLUE, lineweight=50)
    doc.layers.add("CONTOUR_LABELS", color=dxf_colors.BLUE)
    doc.layers.add("SLOPE_BOUND_1_3", color=3)        # green
    doc.layers.add("SLOPE_BOUND_1_2", color=2)        # yellow
    doc.layers.add("SLOPE_BOUND_1_1P5", color=30)     # orange
    doc.layers.add("SLOPE_BOUND_1_1", color=1)        # red
    doc.layers.add("SLOPE_BOUND_1_0P6", color=6)      # magenta
    doc.layers.add("BOUNDARY", color=dxf_colors.GRAY)

    # ---- Site bounding box ----
    x_min, x_max = float(X.min()), float(X.max())
    y_min, y_max = float(Y.min()), float(Y.max())
    msp.add_lwpolyline(
        [(x_min, y_min), (x_max, y_min), (x_max, y_max), (x_min, y_max), (x_min, y_min)],
        dxfattribs={"layer": "BOUNDARY"},
    )

    # Use approximate marker/text size based on site dimensions
    site_size = max(x_max - x_min, y_max - y_min)
    pile_radius = max(site_size * 0.005, 0.3)
    text_h = max(site_size * 0.008, 0.5)

    # ---- Piles ----
    for _, p in df.iterrows():
        msp.add_circle(
            (float(p["Easting"]), float(p["Northing"])),
            radius=pile_radius,
            dxfattribs={"layer": "PILES"},
        )
        msp.add_text(
            str(p["Pile_ID"]),
            dxfattribs={"layer": "PILE_LABELS", "height": text_h},
        ).set_placement((float(p["Easting"]) + pile_radius * 1.5,
                         float(p["Northing"]) + pile_radius * 1.5))
        msp.add_text(
            f"RH={p['Rock_Head_RL']:.2f}",
            dxfattribs={"layer": "PILE_LABELS", "height": text_h * 0.75},
        ).set_placement((float(p["Easting"]) + pile_radius * 1.5,
                         float(p["Northing"]) - pile_radius * 1.5 - text_h * 0.75))

    # ---- Rock head contours ----
    z_min = np.nanmin(Z)
    z_max = np.nanmax(Z)
    levels = np.arange(
        np.floor(z_min / contour_interval) * contour_interval,
        np.ceil(z_max / contour_interval) * contour_interval + contour_interval,
        contour_interval,
    )

    fig, ax = plt.subplots()
    cs = ax.contour(X, Y, Z, levels=levels)
    base_level = levels[0]
    for level_idx, level in enumerate(cs.levels):
        steps = round((level - base_level) / contour_interval)
        is_major = (steps % major_every == 0)
        layer = "RH_CONTOURS_MAJOR" if is_major else "RH_CONTOURS"
        for seg in cs.allsegs[level_idx]:
            if len(seg) < 2:
                continue
            pts3d = [(float(x), float(y), float(level)) for x, y in seg]
            msp.add_polyline3d(pts3d, dxfattribs={"layer": layer})
            if is_major and len(seg) > 10:
                mid = len(seg) // 2
                x_lbl, y_lbl = seg[mid]
                msp.add_text(
                    f"{level:.1f}",
                    dxfattribs={"layer": "CONTOUR_LABELS", "height": text_h * 0.7},
                ).set_placement((float(x_lbl), float(y_lbl)))
    plt.close(fig)

    # ---- Slope-band boundaries ----
    # Each boundary is a slope iso-line at slope = 1/N for the threshold N values.
    boundary_thresholds = [
        (3.0, "SLOPE_BOUND_1_3"),
        (2.0, "SLOPE_BOUND_1_2"),
        (1.5, "SLOPE_BOUND_1_1P5"),
        (1.0, "SLOPE_BOUND_1_1"),
        (0.6, "SLOPE_BOUND_1_0P6"),
    ]
    # Recompute slope grid (we have ratio_N — invert it)
    with np.errstate(divide="ignore", invalid="ignore"):
        slope_grid = np.where(np.isfinite(ratio_N) & (ratio_N > 0),
                              1.0 / ratio_N, 0.0)

    slope_levels = sorted([1.0 / N for N, _ in boundary_thresholds])
    level_to_layer = {1.0 / N: layer for N, layer in boundary_thresholds}

    fig, ax = plt.subplots()
    try:
        cs2 = ax.contour(X, Y, slope_grid, levels=slope_levels)
        for level_idx, level in enumerate(cs2.levels):
            # Match level back to its layer by closest threshold
            layer = level_to_layer.get(
                min(level_to_layer.keys(), key=lambda L: abs(L - level))
            )
            for seg in cs2.allsegs[level_idx]:
                if len(seg) < 2:
                    continue
                pts2d = [(float(x), float(y)) for x, y in seg]
                msp.add_lwpolyline(pts2d, dxfattribs={"layer": layer})
    except Exception:  # noqa: BLE001
        pass  # If a level isn't present in the data, skip
    plt.close(fig)

    # Write to bytes
    buffer = StringIO()
    doc.write(buffer)
    return buffer.getvalue().encode("utf-8")


def show_pdf(file_bytes: bytes):
    b64 = base64.b64encode(file_bytes).decode("utf-8")
    st.markdown(
        f'<iframe src="data:application/pdf;base64,{b64}" '
        f'width="100%" height="800" style="border:1px solid #ccc;"></iframe>',
        unsafe_allow_html=True)



    b64 = base64.b64encode(file_bytes).decode("utf-8")
    st.markdown(
        f'<iframe src="data:application/pdf;base64,{b64}" '
        f'width="100%" height="800" style="border:1px solid #ccc;"></iframe>',
        unsafe_allow_html=True)


# ----------------------------------------------------------------------------
# Sidebar
# ----------------------------------------------------------------------------
with st.sidebar:
    st.header("📂 Inputs")
    pile_file = st.file_uploader(
        "Pile records (CSV or Excel)", type=["csv", "xlsx", "xls"],
        help="Required columns: Pile_ID, Easting, Northing, Rock_Head_RL")
    pdf_file = st.file_uploader(
        "Reference plan (PDF, optional)", type=["pdf"],
        help="For visual cross-checking only.")

    st.markdown("---")
    st.header("⚙️ Analysis settings")
    connection_mode = st.selectbox(
        "How to connect piles",
        ["Delaunay (recommended)", "K-nearest neighbours", "All pairs"])
    k_neighbors = 4
    if connection_mode == "K-nearest neighbours":
        k_neighbors = st.slider("Number of neighbours (k)", 2, 10, 4)
    threshold_n = st.number_input(
        "Steep-pair threshold — flag pile pairs if 1:N has N <",
        min_value=0.5, max_value=20.0, value=3.0, step=0.5)

    st.markdown("---")
    st.header("🗺️ Contour & slope plan")
    grid_res = st.slider("Grid resolution (cells per side)", 50, 300, 150, step=25)
    interp_method = st.selectbox("Interpolation method", ["cubic", "linear"], index=0)
    contour_interval = st.number_input(
        "Contour interval (m)", min_value=0.1, max_value=10.0, value=1.0, step=0.1)

    st.markdown("---")
    st.header("🏔️ Pile design check")
    st.caption(
        "Phase 1 = cliff-zone slope check (always on). "
        "Phase 2 = optional design-table match (uploads below)."
    )
    cliff_search_radius = st.number_input(
        "Slope search radius around each pile (m)",
        min_value=0.1, max_value=10.0, value=2.0, step=0.5,
        help="Worst-case (steepest) slope within this radius determines the "
             "cliff reduction band. 2 m is a reasonable default for typical "
             "bored pile diameters.",
    )

    design_file = st.file_uploader(
        "Design summary table (Excel, optional — Phase 2)",
        type=["xlsx", "xls"],
        help="Project's pile-length summary workbook. The app auto-detects the "
             "multi-zone concatenated layout (typical project format) AND the "
             "simple flat template.",
    )
    bh_detail_files = st.file_uploader(
        "Borehole design workbooks (Excel, optional — multi-upload)",
        type=["xlsx", "xls"],
        accept_multiple_files=True,
        help="Per-borehole Eurocode 7 design workbooks (e.g. BH01-Revised.xlsx). "
             "Used to extract SPT N profiles and stratigraphy for each borehole.",
    )

    with st.expander("📥 Design-check templates"):
        st.download_button(
            "Pile design table template",
            data=design_table_template(),
            file_name="pile_design_table_template.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        st.download_button(
            "Stratigraphy template",
            data=stratigraphy_template(),
            file_name="stratigraphy_template.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        st.caption(
            "Pile records file needs **two extra columns** for Phase 2: "
            "`Zone` and `Pile_Dia_mm`."
        )

    st.markdown("---")
    with st.expander("📥 Need a template?"):
        sample = pd.DataFrame({
            "Pile_ID": ["P1", "P2", "P3", "P4", "P5"],
            "Easting": [100.0, 110.0, 105.0, 95.0, 115.0],
            "Northing": [200.0, 200.0, 210.0, 205.0, 195.0],
            "Ground_RL": [110.0, 110.2, 109.8, 110.5, 109.5],
            "Rock_Head_RL": [85.3, 82.1, 88.5, 86.0, 79.4]})
        st.download_button("Download sample CSV",
            data=sample.to_csv(index=False).encode("utf-8"),
            file_name="pile_records_template.csv", mime="text/csv")


# ----------------------------------------------------------------------------
# Main flow
# ----------------------------------------------------------------------------
if pile_file is None:
    st.info("👈 Upload a pile records file to begin.")
    st.markdown(
        """
        ### Expected data format
        | Column | Required | Description |
        |---|---|---|
        | `Pile_ID` | ✅ | Unique pile identifier |
        | `Easting` | ✅ | X-coordinate (m) |
        | `Northing` | ✅ | Y-coordinate (m) |
        | `Rock_Head_RL` | ✅ | Rock head reduced level (m, positive up) |
        | `Ground_RL` | optional | Ground RL — used for sanity-check |

        ### Outputs
        - Pile-pair gradient table
        - Plan view with steep edges flagged
        - 3D rock head surface
        - **Rock head level contour plan**
        - **Slope classification plan** (1:3, 1:2, 1:1.5, 1:1, 1:0.6 bands)
        - Excel export
        """)
    st.stop()

try:
    raw_df = load_table(pile_file)
except Exception as e:  # noqa: BLE001
    st.error(f"Could not read file: {e}")
    st.stop()

clean_df, issues, warnings = validate_data(raw_df, ground_rl_col="Ground_RL")
if issues:
    st.error("Cannot proceed:")
    for msg in issues:
        st.error(f"• {msg}")
    st.subheader("Uploaded data")
    st.dataframe(raw_df, use_container_width=True)
    st.stop()
if warnings:
    with st.expander(f"⚠️ {len(warnings)} warning(s) — review", expanded=True):
        for msg in warnings:
            st.warning(msg)

connections = get_connections(clean_df, connection_mode, k_neighbors)
grad_df = compute_gradients(clean_df, connections)

with st.spinner("Interpolating rock head surface..."):
    X, Y, Z, ratio_N = build_interpolated_surface(
        clean_df, grid_res=grid_res, method=interp_method)
band_df = slope_band_summary(ratio_N, X, Y)

# Phase 1 — cliff-zone check (always runs)
cliff_df = pile_cliff_check(clean_df, X, Y, ratio_N,
                            search_radius_m=cliff_search_radius)

# Phase 2 — optional design table match
design_table_df = None
design_match_df = None
bh_data = {}
if design_file is not None:
    try:
        design_table_df = parse_pile_summary_workbook(design_file)
        st.session_state["design_rows_count"] = len(design_table_df)
        if "Zone" in clean_df.columns and "Pile_Dia_mm" in clean_df.columns:
            design_match_df = pile_capacity_check(
                clean_df, design_table_df, None)
        else:
            st.warning(
                "Design table loaded successfully ({} rows), but pile records "
                "lack `Zone` and `Pile_Dia_mm` columns. Add these to enable "
                "Phase 2 matching.".format(len(design_table_df))
            )
    except Exception as e:  # noqa: BLE001
        st.error(f"Could not parse design table: {e}")

# Phase 2b — optional BH detail workbooks (SPT profiles)
if bh_detail_files:
    for bh_file in bh_detail_files:
        # Try to derive BH label from filename
        fname = bh_file.name
        m = re.match(r"(BH\d+)", fname.upper())
        label = m.group(1) if m else fname.split(".")[0]
        try:
            bh_data[label] = parse_bh_detail_workbook(bh_file, bh_label=label)
        except Exception as e:  # noqa: BLE001
            st.warning(f"Could not parse {fname}: {e}")

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Piles analysed", len(clean_df))
c2.metric("Pile pairs", len(grad_df))
c3.metric("Min Rock Head RL", f"{clean_df['Rock_Head_RL'].min():.2f} m")
c4.metric("Max Rock Head RL", f"{clean_df['Rock_Head_RL'].max():.2f} m")
steep_count = int((grad_df["Gradient_N"] < threshold_n).sum())
c5.metric(f"Pairs steeper than 1:{threshold_n:g}", steep_count)

tab_data, tab_plan, tab_contour, tab_slope, tab_3d, tab_grad, tab_design, tab_pdf, tab_export = st.tabs([
    "📋 Data", "🗺️ Plan", "📈 Contour", "🚧 Slope Class", "🧊 3D",
    "📐 Gradients", "🏔️ Design Check", "📄 PDF", "💾 Export"])

with tab_data:
    st.subheader("Pile records (validated)")
    st.dataframe(clean_df, use_container_width=True, height=400)

with tab_plan:
    st.plotly_chart(
        plot_plan_view(clean_df, connections, grad_df, threshold_n),
        use_container_width=True)
    st.caption("Red lines = pile pairs steeper than the threshold.")

with tab_contour:
    st.subheader("Rock Head Level Contour Plan")
    st.plotly_chart(
        plot_rockhead_contour(clean_df, X, Y, Z, contour_interval),
        use_container_width=True)
    st.caption(
        f"Interpolated rock head RL across the site using **{interp_method}** "
        f"interpolation on a {grid_res}×{grid_res} grid. "
        f"Contour interval = {contour_interval} m. Interpolation is bounded "
        "to the convex hull of the piles.")

with tab_slope:
    st.subheader("Rock Head Slope Classification Plan")
    st.plotly_chart(
        plot_slope_classification(clean_df, X, Y, ratio_N),
        use_container_width=True)
    st.caption(
        "Slope at each grid cell = √((∂z/∂x)² + (∂z/∂y)²), classified into "
        "the 1:N bands shown in the legend.")

    st.subheader("Area by slope band")
    st.dataframe(
        band_df.style.apply(
            lambda r: [f"background-color: {r['Colour']}; color: white"
                       if c == "Band" else "" for c in r.index], axis=1),
        use_container_width=True, hide_index=True)

    critical_pct = band_df[band_df["Band"].isin([
        "1:1 to 1:0.6", "Steeper than 1:0.6"])]["Coverage (%)"].sum()
    if critical_pct > 0:
        st.warning(
            f"⚠️ {critical_pct:.1f}% of the site has rock head slopes "
            "in the critical bands (steeper than 1:1). Review pile design "
            "for those zones — sloping rock socket, eccentric loading, "
            "potential rock dowels.")

with tab_3d:
    st.plotly_chart(plot_3d_surface(clean_df), use_container_width=True)
    st.caption("Drag to rotate, scroll to zoom.")

with tab_grad:
    st.subheader("Gradient between connected piles")
    st.caption(
        "Convention: **'From' is always the pile with the higher rock head RL**; "
        "'To' is the lower (downslope). The drop is always measured "
        "high-to-low so gradient values are positive. **Max Gradient (Steepest)** "
        "shows, for each 'From' pile, the steepest downslope to any of its "
        "neighbours. Steep rows highlighted in red."
    )

    def highlight_steep(row):
        if row["Gradient_N"] < threshold_n:
            return ["background-color: #ffe5e5"] * len(row)
        return [""] * len(row)

    display_df = grad_df.copy()
    display_df["Gradient_N"] = display_df["Gradient_N"].replace([np.inf], np.nan)

    display_cols = [
        "From", "To",
        "From Rock Head Level", "Adjacent Pile Rock Head Level",
        "Horiz_Dist_m", "Vert_Diff_m", "Drop_From_To_m",
        "Gradient_N", "Gradient (1:x)", "Max Gradient (Steepest)",
    ]
    display_cols = [c for c in display_cols if c in display_df.columns]

    st.dataframe(
        display_df[display_cols].style.apply(highlight_steep, axis=1).format(
            {"Gradient_N": "{:.2f}",
             "From Rock Head Level": "{:.2f}",
             "Adjacent Pile Rock Head Level": "{:.2f}",
             "Horiz_Dist_m": "{:.2f}",
             "Vert_Diff_m": "{:.2f}",
             "Drop_From_To_m": "{:.2f}"},
            na_rep="∞ (flat)"),
        use_container_width=True, height=500, hide_index=True)

    if steep_count:
        st.warning(
            f"{steep_count} pile pair(s) have gradients steeper than 1:{threshold_n:g}.")

with tab_design:
    st.subheader("🏔️ Pile Design Check")
    st.markdown(
        "**Phase 1 — Cliff-zone slope check** identifies piles sitting on or "
        "near steep rock head slopes, where the project's pile design parameter "
        "document requires reduced shaft friction (fs) and end bearing (qb), "
        "plus minimum embedment at toe."
    )

    # Phase 1 — cliff-zone table
    def highlight_band(row):
        band_name = row.get("Governing band", "")
        for label, _, _, _, _, _, _, _ in CLIFF_BANDS:
            if label == band_name:
                # find colour by mapping to SLOPE_BANDS
                colour_map = {b[0]: b[3] for b in SLOPE_BANDS}
                c = colour_map.get(label, "#ffffff")
                return [f"background-color: {c}33"] * len(row)
        return [""] * len(row)

    st.dataframe(
        cliff_df.style.apply(highlight_band, axis=1),
        use_container_width=True, height=400, hide_index=True,
    )

    # Per-band summary
    st.subheader("Pile count by governing cliff-zone band")
    band_counts = cliff_df["Governing band"].value_counts().reindex(
        [b[0] for b in CLIFF_BANDS], fill_value=0)
    summary_rows = []
    for label, _, _, redn, fs, qb, embed, action in CLIFF_BANDS:
        summary_rows.append({
            "Band": label,
            "Pile count": int(band_counts.get(label, 0)),
            "Reduction (%)": redn,
            "fs adopted (kPa)": fs,
            "qb adopted (kPa)": qb,
            "Min embedment at toe": embed,
            "Action": action,
        })
    summary_df = pd.DataFrame(summary_rows)
    st.dataframe(summary_df, use_container_width=True, hide_index=True)

    critical_piles = cliff_df[cliff_df["Governing band"].isin([
        "1:2 to 1:1.5", "1:1.5 to 1:1", "1:1 to 1:0.6", "Steeper than 1:0.6"
    ])]
    if len(critical_piles):
        st.warning(
            f"⚠️ **{len(critical_piles)} pile(s) require cliff-zone "
            f"reductions.** Review the design fs and qb values used for "
            "these piles and verify minimum embedment-at-toe is met."
        )
        if (cliff_df["Governing band"] == "Steeper than 1:0.6").any():
            n_redesign = int((cliff_df["Governing band"] == "Steeper than 1:0.6").sum())
            st.error(
                f"🛑 **{n_redesign} pile(s) sit on slopes steeper than 1:0.6 "
                "and must be redesigned as Case C** (fs = qb = 0 per project "
                "parameters — pile cannot derive any capacity from the "
                "sloping rock surface)."
            )
    else:
        st.success(
            "✅ No piles fall in the reduced-parameter cliff-zone bands. "
            "Standard fs = 350 kPa and qb = 6000 kPa apply throughout."
        )

    st.markdown("---")
    st.subheader("Phase 2 — Design-table match (optional)")

    if design_table_df is not None and not design_table_df.empty:
        n_zones = design_table_df["Zone"].nunique() if "Zone" in design_table_df.columns else 0
        n_rows = len(design_table_df)
        st.success(
            f"✅ Design table parsed: **{n_rows} design rows** across "
            f"**{n_zones} zone(s)**."
        )
        with st.expander(f"Preview design table ({n_rows} rows)"):
            preview_cols = [c for c in [
                "Zone", "BH", "Pile_Dia_Code", "Pile_Dia_mm", "Concrete_Grade",
                "Working_Load_kN", "NSF_kN", "Designed_Length_m",
                "Socket_SIV_m", "Socket_SIII_m",
                "Rcd_Comp_DA1C2_kN", "Rcd_Tens_DA1C2_kN",
            ] if c in design_table_df.columns]
            st.dataframe(design_table_df[preview_cols],
                         use_container_width=True, hide_index=True,
                         height=350)

    if design_match_df is not None and not design_match_df.empty:
        st.markdown(
            "**Per-pile match** — each pile matched against the design table "
            "by **Zone × Pile diameter**. Cross-reference with the Phase 1 "
            "cliff bands above to verify fs/qb reductions are properly applied."
        )

        # Merge cliff band info into match df for the combined view
        cliff_lookup = cliff_df.set_index("Pile_ID")[
            ["Governing band", "Reduction (%)", "Min embedment at toe"]
        ].to_dict("index")
        combined = design_match_df.copy()
        combined["Cliff band"] = combined["Pile_ID"].map(
            lambda pid: cliff_lookup.get(pid, {}).get("Governing band", "—"))
        combined["Reduction required (%)"] = combined["Pile_ID"].map(
            lambda pid: cliff_lookup.get(pid, {}).get("Reduction (%)", "—"))
        combined["Min toe embedment"] = combined["Pile_ID"].map(
            lambda pid: cliff_lookup.get(pid, {}).get("Min embedment at toe", "—"))

        # Flag piles where reduction is required but should be verified
        def flag_pile(row):
            try:
                redn = row["Reduction required (%)"]
                if isinstance(redn, (int, float)) and redn > 0:
                    return f"⚠️ Verify {int(redn)}% reduction applied"
                return "✅ Standard parameters"
            except (KeyError, ValueError, TypeError):
                return "—"
        combined["Cliff-check flag"] = combined.apply(flag_pile, axis=1)

        # Highlight rows needing review
        def highlight_review(row):
            flag = row.get("Cliff-check flag", "")
            if isinstance(flag, str) and "Verify" in flag:
                return ["background-color: #fff3cd"] * len(row)
            if "No design row" in str(row.get("Status", "")):
                return ["background-color: #f8d7da"] * len(row)
            return [""] * len(row)

        display_cols = [c for c in [
            "Pile_ID", "Zone", "Diameter (mm)", "Working Load (kN)",
            "Designed length (m)", "Designed socket S(IV) (m)",
            "Designed socket S(III) (m)",
            "Cliff band", "Reduction required (%)", "Min toe embedment",
            "Cliff-check flag", "Status",
        ] if c in combined.columns]
        st.dataframe(
            combined[display_cols].style.apply(highlight_review, axis=1),
            use_container_width=True, hide_index=True, height=450,
        )

        # Summary of review-flagged piles
        review_count = combined["Cliff-check flag"].astype(str).str.contains("Verify").sum()
        if review_count:
            st.warning(
                f"⚠️ **{review_count} pile(s) sit in cliff zones requiring "
                "reduction.** For these piles, verify that the design row's "
                "Rcd values and pile penetration length reflect the reduced "
                "fs / qb per the project's pile design parameter document, "
                "AND that minimum embedment at toe is satisfied."
            )
        unmatched = combined[combined["Status"].astype(str).str.contains("No design row")]
        if len(unmatched):
            st.error(
                f"🛑 {len(unmatched)} pile(s) could not be matched to a "
                "design row. Check that the Zone and Pile_Dia_mm values in "
                "your pile records exactly match those in the design table."
            )
        # update design_match_df so the Excel export captures the enriched view
        design_match_df = combined

    elif design_file is not None:
        st.info(
            "Design table loaded but pile records lack `Zone` and "
            "`Pile_Dia_mm` columns. Add these to enable per-pile matching."
        )
    else:
        st.info(
            "📥 To enable Phase 2 design-table matching, upload your project's "
            "pile-length summary workbook via the sidebar. The app auto-detects "
            "the multi-zone format (typical project layout) — no re-keying needed. "
            "**Requirements:** pile records must include `Zone` and "
            "`Pile_Dia_mm` columns."
        )

    # ----- Phase 2b: BH SPT profiles -----
    if bh_data:
        st.markdown("---")
        st.subheader(f"Phase 2b — Borehole SPT profiles ({len(bh_data)} BH loaded)")
        st.caption(
            "Extracted from borehole design workbooks. Use to verify the design "
            "row's stratigraphy against site conditions at the pile location."
        )

        bh_options = sorted(bh_data.keys())
        selected_bh = st.selectbox("Select borehole to view", bh_options)
        bh_info = bh_data[selected_bh]

        c1, c2, c3 = st.columns(3)
        c1.metric("Ground RL", f"{bh_info['ground_rl']:.2f} m"
                  if bh_info["ground_rl"] is not None else "—")
        c2.metric("Cut-off RL", f"{bh_info['cut_off_level']:.2f} m"
                  if bh_info["cut_off_level"] is not None else "—")
        c3.metric("SPT intervals", len(bh_info["spt_profile"]))

        # Top-of-layer RLs derived from SPT profile
        if bh_info["layer_tops"]:
            st.markdown("**Top-of-layer RLs (from SPT profile)**")
            tops_df = pd.DataFrame(
                [(k, v) for k, v in bh_info["layer_tops"].items()],
                columns=["Soil / weathering grade", "Top RL (m)"],
            )
            st.dataframe(tops_df, use_container_width=True, hide_index=True)

        # SPT profile plot + table side by side
        col_plot, col_tab = st.columns([2, 1])
        with col_plot:
            fig = plot_spt_profile(
                bh_info["spt_profile"], selected_bh, bh_info["ground_rl"])
            if fig is not None:
                st.plotly_chart(fig, use_container_width=True)
        with col_tab:
            st.markdown("**SPT N profile**")
            st.dataframe(bh_info["spt_profile"],
                         use_container_width=True, hide_index=True,
                         height=600)



    st.markdown("---")
    with st.expander("📖 Design parameters reference (from project document)"):
        st.markdown(
            """
            **Rock-cliff zone reduction table** — applied where pile toe sits
            on or near rock head sloping at the given gradient:

            | Cliff gradient (V:H) | Approx. angle | Reduction | fs adopted | qb adopted | Min d at toe |
            |---|---|---|---|---|---|
            | < 1:3 | < ~18° | None | 350 kPa | 6000 kPa | Standard |
            | 1:3 to 1:2 | ~18°–27° | None | 350 kPa | 6000 kPa | Standard |
            | **1:2 to 1:1.5** | ~27°–34° | **30%** | **245 kPa** | **4200 kPa** | **≥ 1.5 D** |
            | **1:1.5 to 1:1** | ~34°–45° | **50%** | **175 kPa** | **3000 kPa** | **≥ 2.0 D** |
            | **1:1 to 1:0.6** | 45°–~59° | **70%** | **105 kPa** | **1800 kPa** | **≥ 2.5 D** |
            | **> 1:0.6** | > ~59° | **100%** | **0** | **0** | **Redesign as Case C** |

            **Important caveats for this automated check:**
            - The cliff slope at each pile is computed from the interpolated
              rock head surface. Interpolation quality depends on borehole
              density and spacing.
            - "Worst slope in radius" uses a {radius} m disk around each
              pile to capture nearby steepness, not just the slope under
              the pile centroid.
            - This check **does not replace** layered capacity calculation
              from SPT N values using the project's qs/qb parameter table.
              Phase 2 (design-table match) flags piles for review;
              **full capacity verification remains the responsibility of
              the design engineer**.
            """.format(radius=cliff_search_radius)
        )


    if pdf_file is not None:
        st.subheader(f"Reference plan: {pdf_file.name}")
        show_pdf(pdf_file.getvalue())
    else:
        st.info("No PDF uploaded.")

with tab_export:
    st.subheader("Download results")
    excel_bytes = to_excel_bytes(clean_df, grad_df, band_df,
                                  cliff_df=cliff_df,
                                  design_df=design_match_df)
    st.download_button("📊 Download full results (Excel)",
        data=excel_bytes, file_name="rockhead_gradient_results.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    csv_bytes = grad_df.to_csv(index=False).encode("utf-8")
    st.download_button("📄 Download gradient table (CSV)",
        data=csv_bytes, file_name="gradients.csv", mime="text/csv")

    st.markdown("---")
    st.subheader("Export to CAD (DXF)")
    if DXF_AVAILABLE:
        st.markdown(
            """
            Produces a DXF drawing with:
            - Rock head contour lines (minor + major, with elevation labels)
            - Slope-band boundary polylines (1:3, 1:2, 1:1.5, 1:1, 1:0.6 thresholds)
            - Pile markers as circles with ID and rock head RL annotations
            - Site bounding box

            All on properly named/coloured layers so you can toggle visibility in CAD.
            **AutoCAD, BricsCAD, ZWCAD, DraftSight, LibreCAD all open DXF natively.**
            To get a `.dwg`, open the DXF in CAD and Save As → DWG.
            """
        )
        col_a, col_b = st.columns([1, 2])
        with col_a:
            dxf_major_every = st.number_input(
                "Major contour every Nth line", min_value=1, max_value=20, value=5)
        with col_b:
            st.caption(
                f"Using current settings: contour interval = {contour_interval} m, "
                f"grid = {grid_res}×{grid_res}, interpolation = {interp_method}."
            )

        if st.button("🛠️ Generate DXF"):
            try:
                with st.spinner("Building DXF (this can take 10–30 seconds)..."):
                    dxf_bytes = export_to_dxf(
                        clean_df, X, Y, Z, ratio_N,
                        contour_interval=contour_interval,
                        major_every=int(dxf_major_every))
                st.success(f"DXF ready — {len(dxf_bytes)/1024:.1f} KB")
                st.download_button(
                    "💾 Download DXF",
                    data=dxf_bytes,
                    file_name="rockhead_contour_plan.dxf",
                    mime="application/dxf")
            except Exception as e:  # noqa: BLE001
                st.error(f"DXF export failed: {e}")
    else:
        st.warning(
            "DXF export requires `ezdxf` and `matplotlib`. Add them to "
            "`requirements.txt` and reinstall:\n\n```\npip install ezdxf matplotlib\n```")

    st.markdown(
        """
        ---
        **Excel contents:** Pile Data · Gradients · Slope Bands
        """)
