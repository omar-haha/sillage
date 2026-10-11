from pathlib import Path
from zipfile import ZipFile

import numpy as np
import pandas as pd
import pytest

from sillage.backtest.factor_screen import diagnose, monthly_nav_returns, read_aqr, residual


def workbook(path: Path, *, fmt=10, missing=False, duplicate=False):
    ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    with ZipFile(path, "w") as z:
        z.writestr(
            "xl/sharedStrings.xml",
            f"<sst {ns}><si><t>DATE</t></si><si><t>USA</t></si><si><t>01/31/2006</t></si></sst>",
        )
        z.writestr(
            "xl/workbook.xml",
            f'<workbook {ns} xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="QMJ" r:id="rId1"/></sheets></workbook>',
        )
        z.writestr(
            "xl/_rels/workbook.xml.rels",
            '<Relationships><Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>',
        )
        z.writestr(
            "xl/styles.xml",
            f'<styleSheet {ns}><cellXfs><xf numFmtId="{fmt}"/></cellXfs></styleSheet>',
        )
        data = '<row r="2"><c r="A2" t="s"><v>2</v></c>'
        if not missing:
            data += '<c r="B2" s="0"><v>0.0123</v></c>'
        data += "</row>"
        if duplicate:
            data += data.replace('r="2"', 'r="3"').replace("A2", "A3").replace("B2", "B3")
        z.writestr(
            "xl/worksheets/sheet1.xml",
            f'<worksheet {ns}><sheetData><row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c></row>{data}</sheetData></worksheet>',
        )


def test_xlsx_decimal_percentage_not_divided_twice(tmp_path):
    path = tmp_path / "factor.xlsx"
    workbook(path)
    result = read_aqr(path, "QMJ", "USA")
    assert result.iloc[0] == pytest.approx(0.0123)
    assert str(result.index[0]) == "2006-01"


@pytest.mark.parametrize("options", [{"fmt": 0}, {"missing": True}, {"duplicate": True}])
def test_xlsx_fails_closed(tmp_path, options):
    path = tmp_path / "factor.xlsx"
    workbook(path, **options)
    with pytest.raises(ValueError):
        read_aqr(path, "QMJ", "USA")


def test_monthly_nav_compounds_and_preserves_previous_close():
    nav = pd.Series(
        [100.0, 110.0, 121.0, 108.9],
        index=pd.to_datetime(["2005-12-30", "2006-01-03", "2006-01-31", "2006-02-28"]),
    )
    result = monthly_nav_returns(nav, "2006-01", "2006-02")
    assert result.to_list() == pytest.approx([0.21, -0.1])
    with pytest.raises(ValueError):
        monthly_nav_returns(nav.iloc[1:], "2006-01", "2006-02")


def test_missing_nav_month_refused():
    nav = pd.Series([100.0, 110.0], index=pd.to_datetime(["2005-12-30", "2006-02-28"]))
    with pytest.raises(ValueError):
        monthly_nav_returns(nav, "2006-01", "2006-02")


def test_market_residual_removes_intercept_and_beta():
    market = np.arange(20, dtype=float)
    assert np.max(np.abs(residual(3 + 2 * market, market))) < 1e-12


def test_diagnostics_repeatable_and_gate_not_individual_selection():
    rng = np.random.default_rng(1)
    n = 240
    core = rng.normal(0.005, 0.02, n)
    frame = pd.DataFrame(
        {
            "core_excess": core,
            "market": rng.normal(0, 0.05, n),
            "value": rng.normal(0.01, 0.01, n),
            "quality": core,
            "composite": core,
        },
        index=pd.period_range("2006-01", periods=n, freq="M"),
    )
    spec = {
        "bootstrap": {"seed": 42, "block_months": 12, "replicates": 50},
        "criteria": {
            "minimum_common_months": 180,
            "maximum_core_correlation": 0.5,
            "maximum_half_core_correlation": 0.65,
            "maximum_market_residual_correlation": 0.5,
            "maximum_bootstrap_correlation_upper_95": 0.7,
        },
    }
    result = diagnose(frame, spec)
    assert result == diagnose(frame, spec)
    assert not result["continue_to_implementation_feasibility"]
    assert result["diagnostics"]["composite"]["core_correlation"] == pytest.approx(1.0)
    with pytest.raises(ValueError):
        diagnose(frame.drop(frame.index[1]), spec)
    with pytest.raises(ValueError):
        diagnose(frame.iloc[:0], spec)
