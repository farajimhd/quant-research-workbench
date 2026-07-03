from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pipelines.reference_data.security_dimensions import (  # noqa: E402
    SecurityDimensionContext,
    default_dimension_codes,
    dimension_registry,
    security_dimension_observations_sql,
    security_dimension_observations_sql_for_context,
    resolve_security_dimension_context_sql,
)
from research.mlops.clickhouse import (  # noqa: E402
    ClickHouseHttpClient,
    default_clickhouse_password,
    default_clickhouse_url,
    default_clickhouse_user,
    discover_clickhouse_env_files,
)
from research.mlops.env import load_env_files  # noqa: E402


DEFAULT_OUTPUT_ROOT = Path("prepared/reference_data/experiments/security_dimensions")


def main() -> None:
    load_env_files(discover_clickhouse_env_files())
    args = parse_args()
    run_root = Path(args.output_root) / datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    run_root.mkdir(parents=True, exist_ok=True)
    dimensions = tuple(args.dimensions.split(",")) if args.dimensions else default_dimension_codes()
    registry = dimension_registry()
    client = ClickHouseHttpClient(args.clickhouse_url, args.clickhouse_user, default_clickhouse_password())
    context = resolve_context(client, args)
    if context:
        sql = security_dimension_observations_sql_for_context(
            database=args.database,
            context=context,
            dimension_codes=dimensions,
            start_date=args.start_date,
            end_date=args.end_date,
        )
    else:
        sql = security_dimension_observations_sql(
            database=args.database,
            ticker=args.ticker,
            symbol_id=args.symbol_id,
            dimension_codes=dimensions,
            start_date=args.start_date,
            end_date=args.end_date,
        )
    query_path = run_root / "security_dimension_observations.sql"
    query_path.write_text(sql, encoding="utf-8")
    started = time.perf_counter()
    raw = client.execute(sql)
    elapsed = time.perf_counter() - started
    rows = [json.loads(line) for line in raw.splitlines() if line.strip()]
    rows_path = run_root / "security_dimension_observations.jsonl"
    write_jsonl(rows_path, rows)
    summary = {
        "ticker": args.ticker.upper() if args.ticker else "",
        "symbol_id": args.symbol_id,
        "resolved_context": asdict(context) if context else None,
        "database": args.database,
        "start_date": args.start_date,
        "end_date": args.end_date,
        "dimensions": [
            asdict(registry[code])
            for code in dimensions
            if code in registry
        ],
        "row_count": len(rows),
        "query_elapsed_seconds": elapsed,
        "rows_path": str(rows_path),
        "query_path": str(query_path),
    }
    summary["dimension_counts"] = dimension_counts(rows)
    plot_path = None
    if args.plot:
        plot_path = run_root / f"{args.ticker.upper() or 'symbol'}_security_dimensions.png"
        plot_path = render_plot(rows, plot_path, title=args.title or f"{args.ticker.upper()} SEC/Massive Dimensions")
        summary["plot_path"] = str(plot_path)
    summary_path = run_root / "security_dimension_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    print("summary=" + json.dumps(summary, sort_keys=True), flush=True)
    print(f"run_root={run_root}", flush=True)
    if plot_path:
        print(f"plot_path={plot_path}", flush=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Extract and plot source-backed security dimensions.")
    parser.add_argument("--clickhouse-url", default=default_clickhouse_url())
    parser.add_argument("--clickhouse-user", default=default_clickhouse_user())
    parser.add_argument("--database", default="q_live")
    parser.add_argument("--ticker", default="AAPL")
    parser.add_argument("--symbol-id", default="")
    parser.add_argument("--start-date", default="2019-01-01")
    parser.add_argument("--end-date", default="2027-01-01")
    parser.add_argument("--dimensions", default="sec_entity_common_stock_shares_outstanding,sec_common_stock_shares_outstanding,sec_weighted_avg_basic_shares,sec_weighted_avg_diluted_shares,massive_free_float,massive_shares_outstanding,massive_share_class_shares_outstanding")
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--title", default="")
    parser.add_argument("--plot", action=argparse.BooleanOptionalAction, default=True)
    return parser.parse_args()


def resolve_context(client: ClickHouseHttpClient, args: argparse.Namespace) -> SecurityDimensionContext | None:
    raw = client.execute(
        resolve_security_dimension_context_sql(database=args.database, ticker=args.ticker, symbol_id=args.symbol_id)
    )
    rows = [json.loads(line) for line in raw.splitlines() if line.strip()]
    if not rows:
        return None
    row = rows[0]
    return SecurityDimensionContext(
        ticker=str(row.get("ticker") or args.ticker),
        cik=str(row.get("cik") or ""),
        symbol_id=str(row.get("symbol_id") or args.symbol_id),
        listing_id=str(row.get("listing_id") or ""),
        security_id=str(row.get("security_id") or ""),
        issuer_id=str(row.get("issuer_id") or ""),
    )


def dimension_counts(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row["dimension_code"]), []).append(row)
    return {
        code: {
            "rows": len(items),
            "min_observed_at_utc": min(str(item["observed_at_utc"]) for item in items),
            "max_observed_at_utc": max(str(item["observed_at_utc"]) for item in items),
            "latest_value": sorted(items, key=lambda item: str(item["observed_at_utc"]))[-1]["value"],
        }
        for code, items in sorted(grouped.items())
    }


def render_plot(rows: list[dict[str, Any]], output_path: Path, *, title: str) -> Path:
    try:
        import matplotlib.dates as mdates
        import matplotlib.pyplot as plt
    except ModuleNotFoundError:
        try:
            render_pil_plot(rows, output_path, title=title)
            return output_path
        except ModuleNotFoundError:
            svg_path = output_path.with_suffix(".svg")
            render_svg_plot(rows, svg_path, title=title)
            return svg_path

    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row["dimension_code"]), []).append(row)
    if not grouped:
        raise RuntimeError("No rows returned; cannot render plot.")
    fig, ax = plt.subplots(figsize=(13, 7), dpi=160)
    for code, items in sorted(grouped.items()):
        ordered = sorted(items, key=lambda item: str(item["observed_at_utc"]))
        xs = [parse_ch_datetime(str(item["observed_at_utc"])) for item in ordered]
        ys = [float(item["value"]) / 1_000_000_000 for item in ordered]
        label = str(ordered[0].get("dimension_label") or code)
        ax.step(xs, ys, where="post", label=label, linewidth=1.9)
        ax.scatter(xs, ys, s=14)
    ax.set_title(title)
    ax.set_ylabel("Shares, billions")
    ax.set_xlabel("Market-available time (accepted/filed/provider observed)")
    ax.grid(True, linewidth=0.4, alpha=0.35)
    ax.legend(loc="best", fontsize=8)
    ax.xaxis.set_major_locator(mdates.YearLocator())
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    fig.autofmt_xdate()
    fig.tight_layout()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path)
    plt.close(fig)
    return output_path


def render_pil_plot(rows: list[dict[str, Any]], output_path: Path, *, title: str) -> None:
    from PIL import Image, ImageDraw, ImageFont

    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row["dimension_code"]), []).append(row)
    if not grouped:
        raise RuntimeError("No rows returned; cannot render plot.")
    series: list[tuple[str, str, list[tuple[datetime, float]]]] = []
    for idx, (code, items) in enumerate(sorted(grouped.items())):
        ordered = sorted(items, key=lambda item: str(item["observed_at_utc"]))
        label = str(ordered[0].get("dimension_label") or code)
        points = [(parse_ch_datetime(str(item["observed_at_utc"])), float(item["value"]) / 1_000_000_000) for item in ordered]
        series.append((label, palette(idx), points))
    xs = [point[0].timestamp() for _label, _color, points in series for point in points]
    ys = [point[1] for _label, _color, points in series for point in points]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    if max_x == min_x:
        max_x = min_x + 1
    if max_y == min_y:
        max_y = min_y + 1
    width, height = 1600, 900
    left, right, top, bottom = 110, 360, 86, 100
    plot_w = width - left - right
    plot_h = height - top - bottom

    def sx(dt: datetime) -> float:
        return left + ((dt.timestamp() - min_x) / (max_x - min_x)) * plot_w

    def sy(value: float) -> float:
        return top + (1 - ((value - min_y) / (max_y - min_y))) * plot_h

    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default()
    title_font = ImageFont.load_default(size=20)
    draw.text((left, 30), title, fill="#111827", font=title_font)
    for i in range(6):
        y = top + (i / 5) * plot_h
        value = max_y - (i / 5) * (max_y - min_y)
        draw.line((left, y, left + plot_w, y), fill="#e5e7eb", width=1)
        draw.text((12, y - 7), f"{value:.1f}B", fill="#374151", font=font)
    start_year = datetime.fromtimestamp(min_x, UTC).year
    end_year = datetime.fromtimestamp(max_x, UTC).year
    for year in range(start_year, end_year + 1):
        dt = datetime(year, 1, 1, tzinfo=UTC)
        x = sx(dt)
        if left <= x <= left + plot_w:
            draw.line((x, top, x, top + plot_h), fill="#eef2f7", width=1)
            draw.text((x - 12, top + plot_h + 16), str(year), fill="#374151", font=font)
    draw.line((left, top, left, top + plot_h), fill="#6b7280", width=1)
    draw.line((left, top + plot_h, left + plot_w, top + plot_h), fill="#6b7280", width=1)
    for label, color, points in series:
        rgb = hex_to_rgb(color)
        if len(points) == 1:
            px = int(sx(points[0][0]))
            py = int(sy(points[0][1]))
            draw.ellipse((px - 4, py - 4, px + 4, py + 4), fill=rgb)
        else:
            prev_x = sx(points[0][0])
            prev_y = sy(points[0][1])
            for dt, value in points[1:]:
                x = sx(dt)
                y = sy(value)
                draw.line((prev_x, prev_y, x, prev_y), fill=rgb, width=3)
                draw.line((x, prev_y, x, y), fill=rgb, width=3)
                prev_x, prev_y = x, y
            for dt, value in points:
                px = int(sx(dt))
                py = int(sy(value))
                draw.ellipse((px - 3, py - 3, px + 3, py + 3), fill=rgb)
    legend_x, legend_y = left + plot_w + 26, top + 8
    for idx, (label, color, points) in enumerate(series):
        y = legend_y + idx * 28
        rgb = hex_to_rgb(color)
        draw.rectangle((legend_x, y - 10, legend_x + 14, y + 4), fill=rgb)
        draw.text((legend_x + 22, y - 10), f"{shorten_label(label)} ({len(points)})", fill="#111827", font=font)
    draw.text((left, height - 36), "Y-axis: billions of shares. Lines are step-functions; values carry forward until the next source observation.", fill="#374151", font=font)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path)


def render_svg_plot(rows: list[dict[str, Any]], output_path: Path, *, title: str) -> None:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row["dimension_code"]), []).append(row)
    if not grouped:
        raise RuntimeError("No rows returned; cannot render plot.")
    series: list[tuple[str, str, list[tuple[datetime, float]]]] = []
    for idx, (code, items) in enumerate(sorted(grouped.items())):
        ordered = sorted(items, key=lambda item: str(item["observed_at_utc"]))
        label = str(ordered[0].get("dimension_label") or code)
        points = [(parse_ch_datetime(str(item["observed_at_utc"])), float(item["value"]) / 1_000_000_000) for item in ordered]
        series.append((label, palette(idx), points))
    xs = [point[0].timestamp() for _label, _color, points in series for point in points]
    ys = [point[1] for _label, _color, points in series for point in points]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    if max_x == min_x:
        max_x = min_x + 1
    if max_y == min_y:
        max_y = min_y + 1
    width, height = 1400, 780
    left, right, top, bottom = 90, 280, 72, 90
    plot_w = width - left - right
    plot_h = height - top - bottom

    def sx(dt: datetime) -> float:
        return left + ((dt.timestamp() - min_x) / (max_x - min_x)) * plot_w

    def sy(value: float) -> float:
        return top + (1 - ((value - min_y) / (max_y - min_y))) * plot_h

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        "<style>text{font-family:Inter,Arial,sans-serif}.axis{stroke:#6b7280;stroke-width:1}.grid{stroke:#e5e7eb;stroke-width:1}.label{fill:#111827;font-size:18px;font-weight:700}.tick{fill:#374151;font-size:12px}.legend{fill:#111827;font-size:13px}.line{fill:none;stroke-width:2.4}</style>",
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        f'<text x="{left}" y="36" class="label">{escape_xml(title)}</text>',
    ]
    for i in range(6):
        y = top + (i / 5) * plot_h
        value = max_y - (i / 5) * (max_y - min_y)
        parts.append(f'<line x1="{left}" x2="{left + plot_w}" y1="{y:.1f}" y2="{y:.1f}" class="grid"/>')
        parts.append(f'<text x="{left - 10}" y="{y + 4:.1f}" class="tick" text-anchor="end">{value:.1f}B</text>')
    start_year = datetime.fromtimestamp(min_x, UTC).year
    end_year = datetime.fromtimestamp(max_x, UTC).year
    for year in range(start_year, end_year + 1):
        dt = datetime(year, 1, 1, tzinfo=UTC)
        x = sx(dt)
        if left <= x <= left + plot_w:
            parts.append(f'<line x1="{x:.1f}" x2="{x:.1f}" y1="{top}" y2="{top + plot_h}" class="grid"/>')
            parts.append(f'<text x="{x:.1f}" y="{top + plot_h + 24}" class="tick" text-anchor="middle">{year}</text>')
    parts.append(f'<line x1="{left}" x2="{left}" y1="{top}" y2="{top + plot_h}" class="axis"/>')
    parts.append(f'<line x1="{left}" x2="{left + plot_w}" y1="{top + plot_h}" y2="{top + plot_h}" class="axis"/>')
    for label, color, points in series:
        if len(points) == 1:
            x, y = sx(points[0][0]), sy(points[0][1])
            path = f"M{x:.1f},{y:.1f}"
        else:
            commands = [f"M{sx(points[0][0]):.1f},{sy(points[0][1]):.1f}"]
            prev_y = sy(points[0][1])
            for dt, value in points[1:]:
                x = sx(dt)
                y = sy(value)
                commands.append(f"H{x:.1f} V{y:.1f}")
                prev_y = y
            path = " ".join(commands)
        parts.append(f'<path d="{path}" class="line" stroke="{color}"/>')
        for dt, value in points:
            parts.append(f'<circle cx="{sx(dt):.1f}" cy="{sy(value):.1f}" r="3" fill="{color}"/>')
    legend_x, legend_y = left + plot_w + 24, top + 8
    for idx, (label, color, points) in enumerate(series):
        y = legend_y + idx * 24
        parts.append(f'<rect x="{legend_x}" y="{y - 10}" width="13" height="13" fill="{color}"/>')
        parts.append(f'<text x="{legend_x + 20}" y="{y + 1}" class="legend">{escape_xml(shorten_label(label))} ({len(points)})</text>')
    parts.append(f'<text x="{left}" y="{height - 24}" class="tick">Y-axis: billions of shares. Lines are step-functions; values carry forward until the next source observation.</text>')
    parts.append("</svg>")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(parts), encoding="utf-8")


def palette(index: int) -> str:
    colors = ("#2563eb", "#dc2626", "#16a34a", "#9333ea", "#f97316", "#0891b2", "#4b5563", "#be123c")
    return colors[index % len(colors)]


def hex_to_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def escape_xml(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def shorten_label(value: str, max_len: int = 34) -> str:
    return value if len(value) <= max_len else value[: max_len - 1] + "..."


def parse_ch_datetime(value: str) -> datetime:
    normalized = value.replace("Z", "+00:00")
    if "." in normalized and "+" not in normalized:
        return datetime.strptime(normalized, "%Y-%m-%d %H:%M:%S.%f").replace(tzinfo=UTC)
    if "+" not in normalized:
        return datetime.strptime(normalized, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
    return datetime.fromisoformat(normalized)


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True, separators=(",", ":"), default=str) + "\n")


if __name__ == "__main__":
    main()
