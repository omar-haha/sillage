"""Non-investable published-factor diagnostics; never an execution adapter."""

from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import numpy as np
import pandas as pd

NS = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def read_aqr(path: Path, sheet: str, column: str, *, start: str | None = None) -> pd.Series:
    """Read cached numeric XLSX cells, requiring decimal percentage formatting.

    No optional Excel dependency, formula evaluation, guessed units or forward fill.
    Layout/header changes and missing selected observations fail closed.
    """
    with ZipFile(path) as archive:
        strings = [
            "".join(t.text or "" for t in item.findall(".//s:t", NS))
            for item in ET.fromstring(archive.read("xl/sharedStrings.xml"))
        ]
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        sheets = workbook.find("s:sheets", NS)
        relationships = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        targets = {item.attrib["Id"]: item.attrib["Target"] for item in relationships}
        selected = next((item for item in sheets if item.attrib["name"] == sheet), None)
        if selected is None:
            raise ValueError(f"missing sheet {sheet}")
        rid = selected.attrib[
            "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
        ]
        target = targets[rid]
        target = target.lstrip("/") if target.startswith("/") else "xl/" + target
        root = ET.fromstring(archive.read(target))
        styles = ET.fromstring(archive.read("xl/styles.xml"))
        custom = {
            int(x.attrib["numFmtId"]): x.attrib["formatCode"]
            for x in styles.findall("s:numFmts/s:numFmt", NS)
        }
        formats = styles.find("s:cellXfs", NS)

        def value(cell):
            raw = cell.find("s:v", NS)
            if raw is None:
                return None
            return strings[int(raw.text)] if cell.attrib.get("t") == "s" else raw.text

        selected_col = None
        dates, values = [], []
        for row in root.findall("s:sheetData/s:row", NS):
            cells = {"".join(c for c in item.attrib["r"] if c.isalpha()): item for item in row}
            if "A" not in cells:
                continue
            day = value(cells["A"])
            if day == "DATE":
                matches = [key for key, item in cells.items() if value(item) == column]
                if len(matches) != 1:
                    raise ValueError(f"missing or ambiguous column {column}")
                selected_col = matches[0]
                continue
            if selected_col is None or day is None or not str(day).strip():
                continue
            # Published workbooks use text dates; never guess numeric Excel serials.
            try:
                stamp = pd.to_datetime(day, format="%m/%d/%Y").to_period("M")
            except ValueError as exc:
                raise ValueError("unexpected date/footer after header") from exc
            if start is not None and stamp < pd.Period(start, freq="M"):
                continue
            cell = cells.get(selected_col)
            if cell is None or value(cell) is None:
                raise ValueError(f"missing {column} at {stamp}")
            fmt = int(formats[int(cell.attrib.get("s", 0))].attrib.get("numFmtId", 0))
            if fmt not in (9, 10) and "%" not in custom.get(fmt, ""):
                raise ValueError("unverified return units: expected percentage-format decimal")
            dates.append(stamp)
            values.append(float(value(cell)))
    result = pd.Series(values, index=pd.PeriodIndex(dates, freq="M"), name=column)
    if result.empty or result.index.has_duplicates or not result.index.is_monotonic_increasing:
        raise ValueError("empty, duplicate or unordered factor dates")
    if not np.isfinite(result.to_numpy()).all():
        raise ValueError("nonfinite factor returns")
    return result


def monthly_nav_returns(nav: pd.Series, start: str, end: str) -> pd.Series:
    """Month-end NAV ratios, retaining the preceding month as the denominator."""
    if nav.index.has_duplicates or not nav.index.is_monotonic_increasing:
        raise ValueError("NAV dates must be unique and ordered")
    if not np.isfinite(nav.to_numpy()).all() or (nav <= 0).any():
        raise ValueError("NAV must be finite and positive")
    index = pd.DatetimeIndex(nav.index)
    if index.tz is not None:
        index = index.tz_localize(None)
    series = pd.Series(nav.to_numpy(), index=index)
    monthly = series.groupby(series.index.to_period("M")).last().pct_change(fill_method=None)
    selected = monthly.loc[start:end]
    expected = pd.period_range(start, end, freq="M")
    if not selected.index.equals(expected) or selected.isna().any():
        raise ValueError("missing month or preceding month-end NAV")
    return selected


def residual(values: np.ndarray, market: np.ndarray) -> np.ndarray:
    design = np.column_stack((np.ones(len(market)), market))
    return values - design @ np.linalg.lstsq(design, values, rcond=None)[0]


def diagnose(frame: pd.DataFrame, spec: dict) -> dict:
    """Paired monthly diagnostics and frozen heuristic continuation gate."""
    if (
        len(frame) < 4
        or frame.index.has_duplicates
        or not frame.index.equals(pd.period_range(frame.index[0], frame.index[-1], freq="M"))
        or not np.isfinite(frame.to_numpy()).all()
    ):
        raise ValueError("diagnostic panel must be finite, contiguous and unique")
    core = frame["core_excess"].to_numpy()
    market = frame["market"].to_numpy()
    rows = {}
    for name in ("value", "quality", "composite"):
        factor = frame[name].to_numpy()
        if min(np.std(factor), np.std(core), np.std(market)) <= 0:
            raise ValueError("constant series cannot support correlation")
        halves = np.array_split(np.arange(len(frame)), 2)
        rng = np.random.default_rng(spec["bootstrap"]["seed"])
        boot = []
        block = spec["bootstrap"]["block_months"]
        for _ in range(spec["bootstrap"]["replicates"]):
            starts = rng.integers(0, len(frame), size=int(np.ceil(len(frame) / block)))
            indexes = ((starts[:, None] + np.arange(block)) % len(frame)).ravel()[: len(frame)]
            boot.append(np.corrcoef(core[indexes], factor[indexes])[0, 1])
        tail = core <= np.quantile(core, 0.1)
        crises = {}
        for label, start, end in (
            ("GFC", "2008-01", "2009-12"),
            ("covid_year", "2020-01", "2020-12"),
            ("inflation_year", "2022-01", "2022-12"),
        ):
            subset = frame.loc[start:end]
            crises[label] = {
                "months": len(subset),
                "factor_mean": float(subset[name].mean()),
                "both_negative_months": int(((subset.core_excess < 0) & (subset[name] < 0)).sum()),
            }
        rows[name] = {
            "market_beta": float(np.cov(factor, market, ddof=1)[0, 1] / np.var(market, ddof=1)),
            "mean_monthly_factor": float(factor.mean()),
            "annualized_factor_mean_over_vol": float(
                factor.mean() / factor.std(ddof=1) * np.sqrt(12)
            ),
            "annualized_factor_vol": float(factor.std(ddof=1) * np.sqrt(12)),
            "core_correlation": float(np.corrcoef(core, factor)[0, 1]),
            "monthly_covariance": float(np.cov(core, factor, ddof=1)[0, 1]),
            "half_core_correlations": [
                float(np.corrcoef(core[h], factor[h])[0, 1]) for h in halves
            ],
            "half_factor_means": [float(factor[h].mean()) for h in halves],
            "market_residual_correlation": float(
                np.corrcoef(residual(core, market), residual(factor, market))[0, 1]
            ),
            "paired_block_correlation_interval_95": np.quantile(boot, [0.025, 0.975]).tolist(),
            "core_worst_decile": {
                "months": int(tail.sum()),
                "factor_mean": float(factor[tail].mean()),
                "negative_factor_share": float((factor[tail] < 0).mean()),
            },
            "crisis_windows": crises,
        }
    primary = rows["composite"]
    limits = spec["criteria"]
    gates = {
        "enough_months": len(frame) >= limits["minimum_common_months"],
        "full_correlation": primary["core_correlation"] <= limits["maximum_core_correlation"],
        "half_correlations": max(primary["half_core_correlations"])
        <= limits["maximum_half_core_correlation"],
        "residual_correlation": primary["market_residual_correlation"]
        <= limits["maximum_market_residual_correlation"],
        "uncertainty": primary["paired_block_correlation_interval_95"][1]
        <= limits["maximum_bootstrap_correlation_upper_95"],
        "positive_halves": min(primary["half_factor_means"]) > 0,
    }
    return {
        "core_market_beta": float(np.cov(core, market, ddof=1)[0, 1] / np.var(market, ddof=1)),
        "half_periods": [
            {"start": str(frame.index[h[0]]), "end": str(frame.index[h[-1]]), "months": len(h)}
            for h in np.array_split(np.arange(len(frame)), 2)
        ],
        "diagnostics": rows,
        "gates": gates,
        "continue_to_implementation_feasibility": all(gates.values()),
    }
