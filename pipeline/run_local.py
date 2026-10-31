"""Run the warehouse load locally with DuckDB instead of Azure.

Loads the raw feeds as stg.* tables, builds the same star schema as sql/azure
(sql/local/build_dw.sql), runs the data quality checks and computes the KPIs
behind the dashboard (same definitions as powerbi/measures.dax).

    python pipeline/run_local.py --raw data/raw --db data/spareparts.duckdb
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]

FEEDS = {  # staging table -> raw file
    "MARA": "MARA.csv", "LFA1": "LFA1.csv", "T001W": "T001W.csv", "EKKO_EKPO": "EKKO_EKPO.csv", "MARD": "MARD.csv",
    "Dealers": "service_Dealers.csv", "DealerOrders": "service_DealerOrders.csv", "GoodsReceipts": "qm_GoodsReceipts.csv",
}
TEXT_COLUMNS = {"MATNR": "VARCHAR", "LIFNR": "VARCHAR", "EBELN": "VARCHAR", "LGORT": "VARCHAR", "WERKS": "VARCHAR",
                "PurchaseOrder": "VARCHAR", "Material": "VARCHAR", "Plant": "VARCHAR", "Warehouse": "VARCHAR"}

DQ_CHECKS = {
    "dealer order line with unknown part":
        "SELECT count(*) FROM stg.DealerOrders o WHERE NOT EXISTS (SELECT 1 FROM dw.dim_part p WHERE p.PartNumber = o.Material)",
    "goods receipt without purchase order":
        "SELECT count(*) FROM stg.GoodsReceipts r WHERE NOT EXISTS (SELECT 1 FROM dw.fact_purchase_order_line po "
        "WHERE po.PurchaseOrder = r.PurchaseOrder AND po.PoItem = r.PoItem)",
    "shipped more than ordered": "SELECT count(*) FROM stg.DealerOrders WHERE QuantityShipped > QuantityOrdered",
    "rejected more than received": "SELECT count(*) FROM stg.GoodsReceipts WHERE QuantityRejected > QuantityReceived",
    "negative stock": "SELECT count(*) FROM stg.MARD WHERE LABST < 0",
}


def load(raw: Path, db: Path | str = ":memory:") -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(str(db))
    con.execute("CREATE SCHEMA IF NOT EXISTS stg")
    for table, file in FEEDS.items():
        path = raw / file
        header = path.read_text().split("\n", 1)[0].split(",")
        types = {c: TEXT_COLUMNS[c] for c in header if c in TEXT_COLUMNS}
        opts = ", types={" + ", ".join(f"'{c}': '{t}'" for c, t in types.items()) + "}" if types else ""
        con.execute(f"CREATE OR REPLACE TABLE stg.{table} AS SELECT * FROM read_csv(?, header=true{opts})", [str(path)])
    con.execute((ROOT / "sql/local/build_dw.sql").read_text())
    return con


def quality_checks(con) -> dict[str, int]:
    return {name: con.execute(sql).fetchone()[0] for name, sql in DQ_CHECKS.items()}


def kpis(con) -> dict:
    """Headline numbers for the last 12 months up to the latest stock snapshot."""
    q = lambda sql: con.execute(sql).fetchone()  # noqa: E731
    (last_key,) = q("SELECT max(SnapshotDateKey) FROM dw.fact_stock_monthly")
    window = f"BETWEEN {last_key - 10000 + 1} AND {last_key}"  # e.g. 20250101..20251231

    stock_value, = q(f"SELECT sum(StockValue) FROM dw.fact_stock_monthly WHERE SnapshotDateKey = {last_key}")
    avg_stock, = q(f"SELECT sum(StockValue) / count(DISTINCT SnapshotDateKey) FROM dw.fact_stock_monthly WHERE SnapshotDateKey {window}")
    sales, ordered, shipped = q(f"SELECT sum(SalesValue), sum(QuantityOrdered), sum(QuantityShipped) "
                                f"FROM dw.fact_dealer_order_line WHERE OrderDateKey {window}")
    critical, = q(f"""SELECT count(*) FROM (
                        SELECT p.PartKey FROM dw.fact_stock_monthly s JOIN dw.dim_part p USING (PartKey)
                         WHERE s.SnapshotDateKey = {last_key} GROUP BY p.PartKey, p.SafetyStock
                        HAVING sum(s.QuantityOnHand) < p.SafetyStock)""")
    slow_value, slow_parts = q(f"""WITH st AS (SELECT PartKey, sum(QuantityOnHand) AS qty, sum(StockValue) AS val
                                                  FROM dw.fact_stock_monthly WHERE SnapshotDateKey = {last_key} GROUP BY PartKey),
                                          d AS (SELECT PartKey, sum(QuantityShipped) AS qty
                                                  FROM dw.fact_dealer_order_line WHERE OrderDateKey {window} GROUP BY PartKey)
                                   SELECT coalesce(sum(st.val), 0), count(*) FROM st LEFT JOIN d USING (PartKey)
                                    WHERE st.qty > 0 AND st.qty > coalesce(d.qty, 0)""")  # more than 12 months of cover
    receipts, on_time, avg_delay_late, received, rejected = q(
        f"SELECT count(*), avg(IsOnTime::INT), avg(CASE WHEN DelayDays > 0 THEN DelayDays END), "
        f"sum(QuantityReceived), sum(QuantityRejected) FROM dw.fact_goods_receipt WHERE ReceiptDateKey {window}")
    turnover = sales / avg_stock
    return {
        "as_of": str(last_key), "stock_value": round(stock_value), "critical_parts": critical,
        "parts": q("SELECT count(*) FROM dw.dim_part")[0], "suppliers": q("SELECT count(*) FROM dw.dim_supplier")[0],
        "fill_rate": round(shipped / ordered, 4), "sales_value_12m": round(sales), "order_lines_12m":
            q(f"SELECT count(*) FROM dw.fact_dealer_order_line WHERE OrderDateKey {window}")[0],
        "stock_turnover": round(turnover, 2), "days_of_inventory": round(365 / turnover, 1),
        "slow_moving_value": round(slow_value), "slow_moving_parts": slow_parts,
        "slow_moving_share": round(slow_value / stock_value, 4),
        "receipts_12m": receipts, "on_time_delivery": round(on_time, 4), "avg_delay_late_days": round(avg_delay_late, 1),
        "rejection_rate": round(rejected / received, 4),
        "rows": {t: q(f"SELECT count(*) FROM dw.{t}")[0] for t in
                 ["dim_part", "dim_supplier", "dim_dealer", "dim_warehouse", "dim_date", "fact_dealer_order_line",
                  "fact_purchase_order_line", "fact_goods_receipt", "fact_stock_monthly"]},
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", type=Path, default=ROOT / "data/raw")
    ap.add_argument("--db", default=ROOT / "data/spareparts.duckdb")
    ap.add_argument("--kpis", type=Path, default=ROOT / "docs/kpis.json")
    args = ap.parse_args()

    con = load(args.raw, args.db)
    failed = {k: v for k, v in quality_checks(con).items() if v}
    if failed:
        raise SystemExit(f"Data quality checks failed: {failed}")
    result = kpis(con)
    args.kpis.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
