# Rock Head Gradient Analyzer

A simple desktop tool for computing slope gradients of the rock head surface between piles, given pile records and (optionally) a PDF plan for visual reference.

## What it does

- Reads pile records from CSV or Excel (`Pile_ID`, `Easting`, `Northing`, `Rock_Head_RL`).
- Connects piles using Delaunay triangulation (or k-nearest neighbours / all pairs).
- Computes the rock head gradient between every connected pair as a `1:N` ratio.
- Flags pile pairs steeper than a user-defined threshold (default `1:3`).
- Shows a coloured plan view, an interactive 3D rock head surface, and a sortable gradient table.
- Exports results to Excel.
- Displays an uploaded PDF plan for visual cross-checking (no auto-extraction — see notes below).

## Setup (one-time)

You need Python 3.10 or newer. From a terminal in this folder:

```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS / Linux:
source .venv/bin/activate

pip install -r requirements.txt
```

## Running

```bash
streamlit run rockhead_gradient_app.py
```

Your browser opens at `http://localhost:8501`. Upload `sample_pile_data.csv` from this folder to test.

## Input format

| Column | Required | Description |
|---|---|---|
| `Pile_ID` | yes | Unique identifier (e.g. `P01`, `BP-204`) |
| `Easting` | yes | X-coordinate, metres (any consistent grid is fine — SVY21, local site grid, etc.) |
| `Northing` | yes | Y-coordinate, metres |
| `Rock_Head_RL` | yes | Rock head reduced level in metres, **positive up** |
| `Ground_RL` | optional | Ground RL — used only to flag if Rock Head RL > Ground RL (likely sign error) |

If your records use **probe depth from ground** instead of RL, convert before upload:
`Rock_Head_RL = Ground_RL − Depth_to_Rock_Head`.

## Notes on PDF input

The PDF upload is intentionally **reference-only**. Auto-extracting pile coordinates from architectural/structural PDFs is unreliable across different drawing styles, scales, and scan qualities, and a single misread coordinate silently corrupts every gradient downstream. Keep the spreadsheet as your source of truth and use the PDF tab for visual sanity-check.

If you later want PDF-based input, a click-to-pin overlay (user calibrates scale, then taps each pile) is far more reliable than auto-parsing — that's a sensible v2 feature.

## Customising

The whole app is one Python file (`rockhead_gradient_app.py`). Common tweaks:

- **Default steepness threshold** — change the `value=3.0` argument in the `st.number_input` for `threshold_n`.
- **Colour scale** — change `colorscale="RdYlBu"` to any Plotly scale (`Viridis`, `Earth`, etc.).
- **Add columns to export** — extend `compute_gradients()` and the Excel writer.
- **Different connection rule** — add a new branch to `get_connections()`.

## Limitations / things to watch

- Gradients are computed only between pairs that the chosen connection rule connects. With Delaunay this is the natural neighbour set; with "All pairs" you get every combination but visualisation gets noisy beyond ~30 piles.
- A "flat" gradient (`Gradient_N = ∞`) means two piles have identical rock head RL — sometimes real, sometimes a data-entry duplicate; check.
- The 3D surface is a triangulated mesh, not a smoothed/kriged surface. For formal interpolation, export the data and use a geostatistical tool.
