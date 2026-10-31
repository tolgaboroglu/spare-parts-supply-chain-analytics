"""Synthetic source extracts for a bus manufacturer's spare-parts business.

Simulates two years of daily operations (dealer demand, stock, replenishment,
supplier deliveries) and writes the six feeds the data pipeline ingests, in the
shape the source systems deliver them:

ERP (SAP-style tables)
  MARA.csv        material master: part, category, price, safety stock, lead time
  LFA1.csv        vendor master
  EKKO_EKPO.csv   purchase order header + items
  MARD.csv        month-end stock per storage location

SQL Server databases
  service_DealerOrders.csv  dealer / service-point orders (+ service_Dealers.csv)
  qm_GoodsReceipts.csv      goods receipts with delay and quality rejections

Every part number, supplier, dealer, price and quantity is invented.

    python generator/generate.py --out data/raw
"""

from __future__ import annotations

import argparse
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

CATEGORIES = {
    # category: (code, parts, price range EUR, base daily demand, seasonality)
    "Brake system": ("BRK", ["Brake pad set front", "Brake pad set rear", "Brake disc 430 mm", "Brake caliper repair kit",
                             "ABS wheel speed sensor", "Brake chamber type 24", "Air dryer cartridge", "Brake hose 600 mm"], (18, 420), 1.6, "flat"),
    "Engine": ("ENG", ["Oil filter element", "Fuel filter element", "Air filter element", "V-ribbed belt 8PK",
                       "Turbocharger gasket set", "Coolant pump", "Injector seal kit", "Thermostat 83°C"], (9, 1450), 1.4, "flat"),
    "Electrical": ("ELE", ["Alternator 28V 150A", "Starter motor 24V", "Battery 12V 225Ah", "Headlamp LED unit",
                           "Wiper motor", "Door control unit", "CAN bus harness", "Tail lamp cluster"], (24, 980), 0.9, "winter"),
    "HVAC": ("HVC", ["A/C compressor", "Condenser fan 24V", "Cabin air filter", "Refrigerant receiver drier",
                     "Evaporator blower", "Heater core", "Roof A/C pressure switch", "Defroster motor"], (12, 1900), 0.7, "summer"),
    "Body & doors": ("BDY", ["Door actuator pneumatic", "Side mirror assembly", "Windscreen lower", "Bumper front centre",
                             "Seat cushion passenger", "Handrail stanchion", "Door seal profile 2 m", "Emergency hammer"], (6, 1300), 0.8, "flat"),
    "Suspension": ("SUS", ["Air spring bellows", "Shock absorber front", "Stabiliser link", "Levelling valve",
                           "Axle bushing kit", "Torque rod", "Height sensor", "Kingpin repair kit"], (15, 620), 0.7, "flat"),
    "Transmission": ("TRN", ["Clutch disc 430 mm", "Gearbox oil filter", "Retarder seal kit", "Propshaft cross joint",
                             "Gear selector cable", "Clutch servo", "Transmission mount", "Release bearing"], (20, 1700), 0.5, "flat"),
}
WAREHOUSES = [("WH01", "Central warehouse", 0.6), ("WH02", "Regional hub West", 0.25), ("WH03", "Regional hub East", 0.15)]
COUNTRIES = ["Germany", "Turkey", "Italy", "Poland", "Czechia", "France", "Spain", "Austria"]


def season_factor(kind: str, day_of_year: np.ndarray | int) -> np.ndarray:
    t = 2 * np.pi * (np.asarray(day_of_year) - 15) / 365
    if kind == "summer":
        return 1 - 0.55 * np.cos(t)          # peak in July
    if kind == "winter":
        return 1 + 0.45 * np.cos(t)          # peak in January
    return np.ones_like(t, dtype=float)


def build_masters(rng: np.random.Generator):
    suppliers = []
    for i in range(24):
        suppliers.append({
            "LIFNR": f"{100100 + i * 7}",
            "NAME1": f"Supplier {chr(65 + i % 26)}{'' if i < 26 else i}" + [" Components", " Automotive", " Industrie", " Parts", " Systems", " Technik"][i % 6],
            "LAND1": COUNTRIES[i % len(COUNTRIES)],
            "_on_time": float(np.clip(rng.normal(0.86, 0.08), 0.55, 0.99)),
            "_mean_delay": float(rng.uniform(2, 12)),
            "_reject": float(np.clip(rng.normal(0.012, 0.01), 0.0, 0.05)),
        })
    parts = []
    n = 0
    for cat, (code, names, (lo, hi), base, season) in CATEGORIES.items():
        for name in names:
            for variant in ["City", "Coach", "Midi", "Euro 6", "Electric"][: rng.integers(2, 6)] + (["Legacy"] if rng.random() < 0.25 else []):
                n += 1
                price = float(np.round(np.exp(rng.uniform(np.log(lo), np.log(hi))), 2))
                demand = base * float(rng.lognormal(0, 0.8)) * (0.03 if variant == "Legacy" else 1.0) / (1 + price / 400)
                lead = int(rng.choice([7, 10, 14, 21, 28, 35, 45, 60], p=[0.1, 0.12, 0.2, 0.2, 0.15, 0.1, 0.08, 0.05]))
                parts.append({
                    "MATNR": f"BP-{code}-{n:04d}",
                    "MAKTX": f"{name} ({variant})",
                    "MATKL": cat,
                    "MEINS": "PC",
                    "STPRS": price,
                    "PLIFZ": lead,
                    "LIFNR": suppliers[int(rng.integers(0, len(suppliers)))]["LIFNR"],
                    "_demand": demand,
                    "_season": season,
                    "_legacy": variant == "Legacy",
                })
    for p in parts:  # safety stock covers demand variability over the lead time
        p["EISBE"] = max(1, int(round(1.65 * np.sqrt(p["_demand"] * p["PLIFZ"]) + 0.3 * p["_demand"] * p["PLIFZ"])))
    dealers = []
    cities = [("Berlin", "Germany", "WH01"), ("Munich", "Germany", "WH01"), ("Hamburg", "Germany", "WH01"), ("Cologne", "Germany", "WH01"),
              ("Istanbul", "Turkey", "WH03"), ("Ankara", "Turkey", "WH03"), ("Izmir", "Turkey", "WH03"), ("Milan", "Italy", "WH02"),
              ("Rome", "Italy", "WH02"), ("Warsaw", "Poland", "WH01"), ("Prague", "Czechia", "WH01"), ("Lyon", "France", "WH02"),
              ("Paris", "France", "WH02"), ("Madrid", "Spain", "WH02"), ("Vienna", "Austria", "WH01"), ("Bursa", "Turkey", "WH03")]
    for i, (city, country, wh) in enumerate(cities):
        dealers.append({"DealerId": 5000 + i, "DealerName": f"Service Point {city}", "City": city, "Country": country, "HomeWarehouse": wh})
    return parts, suppliers, dealers


def simulate(start: date, days: int, seed: int = 42) -> dict[str, pd.DataFrame]:
    rng = np.random.default_rng(seed)
    parts, suppliers, dealers = build_masters(rng)
    sup = {s["LIFNR"]: s for s in suppliers}
    P, W = len(parts), len(WAREHOUSES)
    demand = np.array([p["_demand"] for p in parts])
    share = np.array([w[2] for w in WAREHOUSES])
    lead = np.array([p["PLIFZ"] for p in parts])
    safety = np.array([p["EISBE"] for p in parts])[:, None] * share[None, :]
    reorder_point = safety + (demand[:, None] * share[None, :]) * lead[:, None]
    order_qty = np.maximum(1, np.round(demand[:, None] * share[None, :] * 30)).astype(int)
    stock = np.round(reorder_point * rng.uniform(0.9, 1.6, size=(P, W))).astype(int)
    legacy = np.array([p["_legacy"] for p in parts])
    stock[legacy] = rng.integers(8, 60, size=(legacy.sum(), W))  # slow movers from discontinued models
    on_order = np.zeros((P, W), dtype=int)
    arrivals: dict[int, list] = {}
    dealers_by_wh = {w[0]: [d for d in dealers if d["HomeWarehouse"] == w[0]] for w in WAREHOUSES}

    po_rows, gr_rows, order_rows, stock_rows = [], [], [], []
    po_no = 4500000000
    order_no = 700000
    for d in range(days):
        today = start + timedelta(days=d)
        doy = today.timetuple().tm_yday
        weekday = today.weekday()
        # 1. goods receipts arriving today
        for (p, w, qty, ebeln, ebelp, requested) in arrivals.pop(d, []):
            s = sup[parts[p]["LIFNR"]]
            rejected = int(rng.binomial(qty, s["_reject"]))
            stock[p, w] += qty - rejected
            on_order[p, w] -= qty
            gr_rows.append({"ReceiptId": len(gr_rows) + 1, "PurchaseOrder": ebeln, "PoItem": ebelp, "Material": parts[p]["MATNR"],
                            "Plant": WAREHOUSES[w][0], "ReceiptDate": today, "RequestedDate": requested,
                            "QuantityReceived": qty, "QuantityRejected": rejected})
        # 2. dealer demand
        if weekday < 6:
            season = np.array([season_factor(p["_season"], doy) for p in parts])
            lam = (demand * season)[:, None] * share[None, :] * (0.6 if weekday == 5 else 1.15)
            qty = rng.poisson(lam)
            for p, w in zip(*np.nonzero(qty)):
                q = int(qty[p, w])
                filled = int(min(q, stock[p, w]))
                stock[p, w] -= filled
                dealer = dealers_by_wh[WAREHOUSES[w][0]][int(rng.integers(0, len(dealers_by_wh[WAREHOUSES[w][0]])))]
                order_no += 1
                order_rows.append({"OrderLineId": order_no, "OrderDate": today, "DealerId": dealer["DealerId"],
                                   "Material": parts[p]["MATNR"], "Warehouse": WAREHOUSES[w][0], "QuantityOrdered": q,
                                   "QuantityShipped": filled, "Status": "Shipped" if filled == q else ("Partial" if filled else "Backorder")})
        # 3. replenishment (ERP MRP-style reorder point)
        need = (stock + on_order < reorder_point) & ~legacy[:, None]
        for p, w in zip(*np.nonzero(need)):
            s = sup[parts[p]["LIFNR"]]
            q = int(order_qty[p, w])
            requested = today + timedelta(days=int(lead[p]))
            late = rng.random() > s["_on_time"]
            delay = int(rng.exponential(s["_mean_delay"])) + 1 if late else -int(rng.integers(0, 3))
            po_no += 1
            po_rows.append({"EBELN": str(po_no), "EBELP": 10, "BEDAT": today, "LIFNR": s["LIFNR"], "MATNR": parts[p]["MATNR"],
                            "WERKS": WAREHOUSES[w][0], "MENGE": q, "NETPR": round(parts[p]["STPRS"] * float(rng.uniform(0.92, 1.03)), 2),
                            "EINDT": requested})
            on_order[p, w] += q
            arrivals.setdefault(d + int(lead[p]) + delay, []).append((p, w, q, str(po_no), 10, requested))
        # 4. month-end stock snapshot (MARD)
        if (today + timedelta(days=1)).day == 1:
            for p in range(P):
                for w in range(W):
                    stock_rows.append({"MATNR": parts[p]["MATNR"], "WERKS": WAREHOUSES[w][0], "LGORT": "0001",
                                       "SNAPSHOT_DATE": today, "LABST": int(stock[p, w])})

    mara = pd.DataFrame([{k: v for k, v in p.items() if not k.startswith("_")} for p in parts])
    lfa1 = pd.DataFrame([{k: v for k, v in s.items() if not k.startswith("_")} for s in suppliers])
    dealers_df = pd.DataFrame(dealers)
    return {
        "MARA": mara, "LFA1": lfa1, "EKKO_EKPO": pd.DataFrame(po_rows), "MARD": pd.DataFrame(stock_rows),
        "service_Dealers": dealers_df, "service_DealerOrders": pd.DataFrame(order_rows), "qm_GoodsReceipts": pd.DataFrame(gr_rows),
        "T001W": pd.DataFrame([{"WERKS": w[0], "NAME1": w[1]} for w in WAREHOUSES]),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/raw")
    ap.add_argument("--start", default="2024-01-01")
    ap.add_argument("--days", type=int, default=731)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    tables = simulate(date.fromisoformat(args.start), args.days, args.seed)
    for name, df in tables.items():
        df.to_csv(out / f"{name}.csv", index=False)
        print(f"{name:22s} {len(df):>8,d} rows")


if __name__ == "__main__":
    main()
