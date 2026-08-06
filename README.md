# pairwise-images

Generates pairwise text-reuse visualizations from OpenITI alignment data.

Each chart reproduces the D3.js chart from the KITAB explore app
(`src/components/Visualisation/Chart/index.jsx`) as a static image: book1's
reused passages on top, book2's below, connected by alignment curves.

## Layout

```
data/<bk1>/<bk1>_<bk2>.csv    alignment data (one subfolder per book1 id)
meta/OpenITI_metadata_*.csv   OpenITI metadata (used for each book's tok_length)
scripts/generate_visualizations.py
```

Running the script produces, for every `<bk1>/<bk1>_<bk2>.csv` file found:

```
svg/<bk1>/<bk1>_<bk2>.svg
png/<bk1>/<bk1>_<bk2>.png
thumbnail/<bk1>/<bk1>_<bk2>.png
```

## Setup

- Python 3 (standard library only).
- Node.js, with dependencies installed once via:

  ```
  cd scripts
  npm install
  ```

  (installs `@resvg/resvg-js`, used to rasterize the generated SVGs to PNG.)

## Usage

```
python scripts/generate_visualizations.py [data_dir] [meta_file] [--overwrite]
```

- `data_dir` — folder containing one subfolder per book1 id (default: `data/`).
- `meta_file` — OpenITI metadata file used to look up each book's `tok_length`
  for axis scaling (default: `meta/OpenITI_metadata_2025-1-9.csv`).
- `--overwrite` — regenerate a pair's SVG/PNG/thumbnail even if all three
  already exist (default: skip pairs that are already fully generated).
