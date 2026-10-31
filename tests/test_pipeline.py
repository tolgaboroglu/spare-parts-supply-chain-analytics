import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pipeline.run_local import kpis, load, quality_checks  # noqa: E402


@pytest.fixture(scope="session")
def con(tmp_path_factory):
    raw = tmp_path_factory.mktemp("raw")
    subprocess.run([sys.executable, str(ROOT / "generator/generate.py"), "--out", str(raw), "--days", "400", "--seed", "7"],
                   check=True, capture_output=True)
    return load(raw)


def test_all_feeds_land_in_the_star_schema(con):
    for table in ["dim_part", "dim_supplier", "dim_dealer", "dim_warehouse", "fact_dealer_order_line",
                  "fact_purchase_order_line", "fact_goods_receipt", "fact_stock_monthly"]:
        assert con.execute(f"SELECT count(*) FROM dw.{table}").fetchone()[0] > 0, table


def test_no_rows_lost_between_staging_and_facts(con):
    assert con.execute("SELECT count(*) FROM stg.DealerOrders").fetchone() == \
           con.execute("SELECT count(*) FROM dw.fact_dealer_order_line").fetchone()
    assert con.execute("SELECT count(*) FROM stg.GoodsReceipts").fetchone() == \
           con.execute("SELECT count(*) FROM dw.fact_goods_receipt").fetchone()


def test_data_quality_checks_pass(con):
    assert quality_checks(con) == {k: 0 for k in quality_checks(con)}


def test_kpis_are_plausible(con):
    k = kpis(con)
    assert 0.9 < k["fill_rate"] <= 1
    assert 0.5 < k["on_time_delivery"] < 1
    assert 0 <= k["rejection_rate"] < 0.05
    assert k["stock_value"] > 0 and k["stock_turnover"] > 0
    assert 0 <= k["slow_moving_share"] < 1


def test_adf_references_resolve():
    adf = ROOT / "adf"
    names = {p.stem for p in adf.rglob("*.json")}
    text = "\n".join(p.read_text() for p in adf.rglob("*.json"))
    for doc in (json.loads(p.read_text()) for p in adf.rglob("*.json")):
        assert doc["name"] in names
    import re
    for ref in re.findall(r'"referenceName": "([^"]+)"', text):
        if ref != "ir-selfhosted-onprem":  # integration runtime is created in the portal
            assert ref in names, ref
