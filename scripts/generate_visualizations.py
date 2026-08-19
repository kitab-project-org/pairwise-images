"""Generate pairwise text-reuse visualizations for all text reuse data files in a folder.

Reproduces the D3.js chart from kitab-project-org/explore
(src/components/Visualisation/Chart/index.jsx) as a static SVG using
alignment data CSVs found in a folder tree of the form
<data_dir>/<bk1>/<bk1>_<bk2>.csv, saves that SVG (svg/<bk1>/), then
rasterizes it via the Node.js @resvg/resvg-js package (see svg_to_png.js)
into a full-size PNG (png/<bk1>/) and a thumbnail (thumbnail/<bk1>/).
Per-book token lengths (used to scale the axes) are looked up from the
OpenITI metadata file in meta/.

This is a direct port of the layout constants and scale/coordinate formulas
from the pairwise chart visualisation in the KITAB explore app
-- not a stylistic reinterpretation -- so that the output
matches what that page's "Download PNG" button would produce for the
#svgChart element (book1 bars on top, book2 bars below, connected by
alignment curves). Two simplifications versus the original: the small 6px
"outer tick" hooks on D3 axis domain lines are omitted, and the brush/hover
elements (invisible in a static export) are skipped entirely.

Usage: 
```
python generate_visualizations.py [data_dir] [meta_file] [--overwrite]
```

"""

import argparse
import concurrent.futures
import csv
import math
import os
import subprocess

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DATA_DIR = os.path.join(ROOT_DIR, "data")
DEFAULT_META_FILE = os.path.join(ROOT_DIR, "meta", "OpenITI_metadata_2025-1-9.csv")
SVG_DIR = os.path.join(ROOT_DIR, "svg")
PNG_DIR = os.path.join(ROOT_DIR, "png")
THUMBNAIL_DIR = os.path.join(ROOT_DIR, "thumbnail")
THUMBNAIL_WIDTH = 300
RASTER_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "svg_to_png.js")

# --- layout constants, copied from index.jsx ---
OUTER_HEIGHT = 530
# index.jsx uses the live width of its (responsive) container; there is no
# single "correct" value outside a browser, so this is a fixed stand-in
# matching a typical desktop view.
OUTER_WIDTH = 1000
MARGIN = {"top": 40, "right": 20, "bottom": 20, "left": 60}
PADDING = {"top": 40, "right": 0, "bottom": 40, "left": 40}
BAR_MAX_HEIGHT = 150
CHUNK_SIZE = 300
CONN_COLOR = "#FFCC66"
BAR_WIDTH = 0.5


def d3_tick_step(start, stop, count):
    """Port of d3-array's tickStep, used by D3 axes with .ticks(n)."""
    e10, e5, e2 = math.sqrt(50), math.sqrt(10), math.sqrt(2)
    step0 = abs(stop - start) / max(0, count)
    step1 = 10 ** math.floor(math.log10(step0))
    error = step0 / step1
    if error >= e10:
        step1 *= 10
    elif error >= e5:
        step1 *= 5
    elif error >= e2:
        step1 *= 2
    return -step1 if stop < start else step1


def d3_ticks(start, stop, count):
    step = d3_tick_step(start, stop, count)
    first = math.ceil(start / step) * step
    last = math.floor(stop / step) * step
    n = int(round((last - first) / step)) + 1
    return [round(first + i * step) for i in range(n)]


def fmt(n):
    return str(int(n)) if float(n).is_integer() else f"{n:g}"


def load_tok_lengths(meta_path):
    """Map book version id (e.g. "Shamela0011680-ara1.mARkdown") to tok_length.

    The metadata's own "id"/"version_uri" columns don't include file-format
    suffixes like ".mARkdown", but the CSVs' <bk1>/<bk2> ids do. The
    local_path column's basename does include those suffixes, so the id is
    recovered as that basename with the "book" column's prefix stripped off.
    """
    tok_lengths = {}
    with open(meta_path, encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter="\t")
        for row in reader:
            local_path = row.get("local_path")
            tok_length = row.get("tok_length")
            if not local_path or not tok_length:
                continue
            basename = local_path.rsplit("/", 1)[-1]
            prefix = (row.get("book") or "") + "."
            book_id = basename[len(prefix):] if basename.startswith(prefix) else basename
            tok_lengths[book_id] = int(tok_length)
    return tok_lengths


def load_dataset(path):
    with open(path, encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter="\t")
        rows = [{k: int(v) for k, v in row.items()} for row in reader]
    return rows or None


def build_svg(data_set, tok_length1, tok_length2):
    last_ms1 = math.ceil(tok_length1 / CHUNK_SIZE) if tok_length1 else 0
    last_ms2 = math.ceil(tok_length2 / CHUNK_SIZE) if tok_length2 else 0
    show_end1 = last_ms1 > 0
    show_end2 = last_ms2 > 0
    # if the token length isn't known, use the highest milestone found in
    # the alignment data as a proxy (same fallback as index.jsx)
    if not show_end1:
        last_ms1 = max(row["seq1"] for row in data_set)
    if not show_end2:
        last_ms2 = max(row["seq2"] for row in data_set)

    max_book1, max_book2 = last_ms1, last_ms2
    max_peak = max(max_book1, max_book2)

    inner_width = OUTER_WIDTH - MARGIN["left"] - MARGIN["right"]
    width = inner_width - PADDING["left"] - PADDING["right"]
    inner_height = OUTER_HEIGHT - MARGIN["top"] - MARGIN["bottom"]
    height = inner_height - 20

    svg_width = OUTER_WIDTH - 30
    svg_height = OUTER_HEIGHT

    def x_scale(v):
        return 1 + (v / max_peak) * (width - 2)

    def y_scale(v):
        return (v / CHUNK_SIZE) * BAR_MAX_HEIGHT

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{svg_width}" '
        f'height="{svg_height}" viewBox="0 0 {svg_width} {svg_height}" '
        f'font-family="Arial, sans-serif">',
        f'<rect x="0" y="0" width="{svg_width}" height="{svg_height}" fill="white"/>',
        f'<defs><clipPath id="clip"><rect width="{width}" height="{height}"/></clipPath></defs>',
    ]

    # --- drawingG: book1 bars, connections, book2 bars (clipped) ---
    parts.append(f'<g transform="translate({MARGIN["left"]},{MARGIN["top"]})" clip-path="url(#clip)">')

    parts.append('<g id="firstchart">')
    for row in data_set:
        x = x_scale(row["seq1"])
        y1, y2 = y_scale(row["bw1"]), y_scale(row["ew1"])
        parts.append(
            f'<line x1="{x:.2f}" x2="{x:.2f}" y1="{y1:.2f}" y2="{y2:.2f}" '
            f'stroke="red" stroke-width="{BAR_WIDTH}"/>'
        )
    parts.append("</g>")

    parts.append('<g class="connections">')
    for row in data_set:
        x1, x2 = x_scale(row["seq1"]), x_scale(row["seq2"])
        d = f"M {x1:.2f} 150 C {x1:.2f} 250,{x2:.2f} 220 , {x2:.2f} 300"
        parts.append(f'<path d="{d}" stroke="{CONN_COLOR}" fill="none"/>')
    parts.append("</g>")

    parts.append('<g id="secondchart" transform="translate(0,300)">')
    for row in data_set:
        x = x_scale(row["seq2"])
        y1, y2 = y_scale(row["bw2"]), y_scale(row["ew2"])
        parts.append(
            f'<line x1="{x:.2f}" x2="{x:.2f}" y1="{y1:.2f}" y2="{y2:.2f}" '
            f'stroke="red" stroke-width="{BAR_WIDTH}"/>'
        )
    parts.append("</g>")

    parts.append("</g>")  # end drawingG

    # --- marksG: axes + reference lines (not clipped) ---
    parts.append(f'<g transform="translate({MARGIN["left"]},{MARGIN["top"]})">')

    range0, range1 = 1, width - 1

    # x0 axis: book1, bottom, at y=150
    parts.append(f'<g transform="translate(0,{BAR_MAX_HEIGHT})">')
    parts.append(f'<path d="M{range0},0H{range1}" stroke="{CONN_COLOR}" stroke-width="3" fill="none"/>')
    for v in [1, max_book1]:
        tx = x_scale(v)
        parts.append(
            f'<g transform="translate({tx:.2f},0)">'
            f'<text x="-5" y="5" transform="rotate(-90)" text-anchor="end" '
            f'font-size="12">{fmt(v)}</text></g>'
        )
    parts.append("</g>")

    # x1 axis: book2, top, at y=300
    parts.append(f'<g transform="translate(0,{BAR_MAX_HEIGHT * 2})">')
    parts.append(f'<path d="M{range0},0H{range1}" stroke="{CONN_COLOR}" stroke-width="3" fill="none"/>')
    for v in [1, max_book2]:
        tx = x_scale(v)
        parts.append(
            f'<g transform="translate({tx:.2f},0)">'
            f'<text x="5" y="2" transform="rotate(-90)" text-anchor="start" '
            f'font-size="12">{fmt(v)}</text></g>'
        )
    parts.append("</g>")

    y_ticks = d3_ticks(0, CHUNK_SIZE, 5)

    # y0 axis: book1, left, at x=0
    parts.append('<g transform="translate(0,0)">')
    parts.append(f'<path d="M0,0V{BAR_MAX_HEIGHT}" stroke="{CONN_COLOR}" stroke-width="3" fill="none"/>')
    for v in y_ticks:
        ty = y_scale(v)
        parts.append(
            f'<g transform="translate(0,{ty:.2f})">'
            f'<text x="-9" dy="0.32em" text-anchor="end" font-size="12">{fmt(v)}</text></g>'
        )
    parts.append("</g>")

    # y1 axis: book2, left, at x=0, offset y=300
    parts.append(f'<g transform="translate(0,{BAR_MAX_HEIGHT * 2})">')
    parts.append(f'<path d="M0,0V{BAR_MAX_HEIGHT}" stroke="{CONN_COLOR}" stroke-width="3" fill="none"/>')
    for v in y_ticks:
        ty = y_scale(v)
        parts.append(
            f'<g transform="translate(0,{ty:.2f})">'
            f'<text x="-9" dy="0.32em" text-anchor="end" font-size="12">{fmt(v)}</text></g>'
        )
    parts.append("</g>")

    # book-start/end bars, drawn last so they sit on top of the axis lines
    ref_defs = [
        (0, 0, True),
        (max_book1 + 1, 0, show_end1),
        (0, 300, True),
        (max_book2 + 1, 300, show_end2),
    ]
    for x_val, y_off, solid in ref_defs:
        x = x_scale(x_val)
        y1, y2 = y_off, y_off + BAR_MAX_HEIGHT
        if solid:
            parts.append(f'<line x1="{x:.2f}" x2="{x:.2f}" y1="{y1}" y2="{y2}" stroke="black" stroke-width="5"/>')
        else:
            parts.append(
                f'<line x1="{x:.2f}" x2="{x:.2f}" y1="{y1}" y2="{y2}" stroke="black" '
                f'stroke-width="2" stroke-dasharray="5,5"/>'
            )

    parts.append("</g>")  # end marksG
    parts.append("</svg>")
    return "\n".join(parts)


def rasterize(svg, full_path, thumb_path):
    subprocess.run(
        ["node", RASTER_SCRIPT, full_path, thumb_path, str(THUMBNAIL_WIDTH)],
        input=svg.encode("utf-8"),
        check=True,
    )


_worker_tok_lengths = None


def _init_worker(tok_lengths):
    """ProcessPoolExecutor initializer: stash tok_lengths once per worker
    instead of pickling/sending it with every task."""
    global _worker_tok_lengths
    _worker_tok_lengths = tok_lengths


def output_paths(bk1, bk2):
    svg_path = os.path.join(SVG_DIR, bk1, f"{bk1}_{bk2}.svg")
    full_path = os.path.join(PNG_DIR, bk1, f"{bk1}_{bk2}.png")
    thumb_path = os.path.join(THUMBNAIL_DIR, bk1, f"{bk1}_{bk2}.png")
    return svg_path, full_path, thumb_path


def outputs_exist(bk1, bk2):
    return all(os.path.exists(p) for p in output_paths(bk1, bk2))


def process_pair(bk1, bk2, csv_path):
    """Build+rasterize one <bk1>/<bk2> pair. Runs in a worker process.

    Callers are expected to have already skipped pairs whose outputs exist
    (see outputs_exist), so this always (re)generates.
    """
    data_set = load_dataset(csv_path)
    if not data_set:
        return "skipped", bk1, bk2

    tok_length1 = _worker_tok_lengths.get(bk1, 0)
    tok_length2 = _worker_tok_lengths.get(bk2, 0)

    svg_path, full_path, thumb_path = output_paths(bk1, bk2)
    os.makedirs(os.path.dirname(svg_path), exist_ok=True)
    os.makedirs(os.path.dirname(full_path), exist_ok=True)
    os.makedirs(os.path.dirname(thumb_path), exist_ok=True)

    svg = build_svg(data_set, tok_length1, tok_length2)
    with open(svg_path, "w", encoding="utf-8") as f:
        f.write(svg)

    rasterize(svg, full_path, thumb_path)
    return "generated", bk1, bk2


def iter_csv_files(data_dir):
    """Yield (bk1, bk2, csv_path) for each <data_dir>/<bk1>/<bk1>_<bk2>.csv file."""
    for bk1 in sorted(os.listdir(data_dir)):
        print(bk1)
        bk1_dir = os.path.join(data_dir, bk1)
        if not os.path.isdir(bk1_dir):
            continue

        prefix = bk1 + "_"
        for filename in sorted(os.listdir(bk1_dir)):
            if not (filename.startswith(prefix) and filename.endswith(".csv")):
                continue
            bk2 = filename[len(prefix):-len(".csv")]
            yield bk1, bk2, os.path.join(bk1_dir, filename)


def main():
    parser = argparse.ArgumentParser(description="Generate pairwise text-reuse visualizations.")
    parser.add_argument(
        "data_dir",
        nargs="?",
        default=DEFAULT_DATA_DIR,
        help=f"Folder containing one subfolder per book1 id, each holding "
        f"<bk1>_<bk2>.csv alignment files (default: {DEFAULT_DATA_DIR})",
    )
    parser.add_argument(
        "meta_file",
        nargs="?",
        default=DEFAULT_META_FILE,
        help=f"OpenITI metadata file (default: {DEFAULT_META_FILE})"
    )
    parser.add_argument(
        "--overwrite",
        default=False,
        action=argparse.BooleanOptionalAction,
        help="Overwrite existing visualizations (default: False)"
    )
    parser.add_argument(
        "-j", "--workers",
        type=int,
        default=os.cpu_count() or 4,
        help="Number of worker processes to rasterize in parallel (default: all cores)"
    )

    args = parser.parse_args()

    overwrite = args.overwrite

    tok_lengths = load_tok_lengths(args.meta_file)

    tasks = list(iter_csv_files(args.data_dir))

    generated, skipped, existed = 0, 0, 0
    if not overwrite:
        pending = []
        for bk1, bk2, csv_path in tasks:
            if outputs_exist(bk1, bk2):
                existed += 1
            else:
                pending.append((bk1, bk2, csv_path))
        tasks = pending

    with concurrent.futures.ProcessPoolExecutor(
        max_workers=args.workers, initializer=_init_worker, initargs=(tok_lengths,)
    ) as executor:
        futures = [
            executor.submit(process_pair, bk1, bk2, csv_path)
            for bk1, bk2, csv_path in tasks
        ]
        for future in concurrent.futures.as_completed(futures):
            status, bk1, bk2 = future.result()
            if status == "generated":
                generated += 1
                print(f"OK    {bk1}_{bk2}.png")
            else:
                skipped += 1

    print(f"\nDone: {generated} visualizations generated, {skipped} pairs had no text reuse data")


if __name__ == "__main__":
    main()
