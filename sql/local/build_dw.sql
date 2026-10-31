-- DuckDB version of the warehouse load (same model as sql/azure, for running locally).
-- Expects the raw feeds loaded as stg.* tables by pipeline/run_local.py.

CREATE SCHEMA IF NOT EXISTS dw;

CREATE OR REPLACE TABLE dw.dim_date AS
SELECT CAST(strftime(d, '%Y%m%d') AS INTEGER) AS DateKey, CAST(d AS DATE) AS "Date",
       year(d) AS "Year", quarter(d) AS "Quarter", month(d) AS "Month", strftime(d, '%b') AS MonthName,
       strftime(d, '%Y-%m') AS YearMonth, dayofweek(d) NOT IN (0) AS IsWorkday
  FROM range(DATE '2024-01-01', DATE '2026-01-01', INTERVAL 1 DAY) AS t(d);

CREATE OR REPLACE TABLE dw.dim_part AS
SELECT row_number() OVER (ORDER BY MATNR) AS PartKey, MATNR AS PartNumber, MAKTX AS PartName, MATKL AS Category,
       MEINS AS UnitOfMeasure, STPRS AS StandardPrice, PLIFZ AS LeadTimeDays, EISBE AS SafetyStock, LIFNR AS MainSupplierNumber
  FROM stg.MARA;

CREATE OR REPLACE TABLE dw.dim_supplier AS
SELECT row_number() OVER (ORDER BY LIFNR) AS SupplierKey, LIFNR AS SupplierNumber, NAME1 AS SupplierName, LAND1 AS Country
  FROM stg.LFA1;

CREATE OR REPLACE TABLE dw.dim_warehouse AS
SELECT row_number() OVER (ORDER BY WERKS) AS WarehouseKey, WERKS AS WarehouseCode, NAME1 AS WarehouseName
  FROM stg.T001W;

CREATE OR REPLACE TABLE dw.dim_dealer AS
SELECT row_number() OVER (ORDER BY DealerId) AS DealerKey, DealerId, DealerName, City, Country, HomeWarehouse
  FROM stg.Dealers;

CREATE OR REPLACE TABLE dw.fact_dealer_order_line AS
SELECT o.OrderLineId, CAST(strftime(o.OrderDate, '%Y%m%d') AS INTEGER) AS OrderDateKey,
       d.DealerKey, p.PartKey, w.WarehouseKey, o.QuantityOrdered, o.QuantityShipped,
       round(o.QuantityShipped * p.StandardPrice, 2) AS SalesValue, o.Status
  FROM stg.DealerOrders o
  JOIN dw.dim_dealer d    ON d.DealerId = o.DealerId
  JOIN dw.dim_part p      ON p.PartNumber = o.Material
  JOIN dw.dim_warehouse w ON w.WarehouseCode = o.Warehouse;

CREATE OR REPLACE TABLE dw.fact_purchase_order_line AS
SELECT i.EBELN AS PurchaseOrder, i.EBELP AS PoItem,
       CAST(strftime(i.BEDAT, '%Y%m%d') AS INTEGER) AS OrderDateKey,
       CAST(strftime(i.EINDT, '%Y%m%d') AS INTEGER) AS RequestedDateKey,
       s.SupplierKey, p.PartKey, w.WarehouseKey, i.MENGE AS QuantityOrdered, i.NETPR AS NetPrice,
       round(i.MENGE * i.NETPR, 2) AS OrderValue
  FROM stg.EKKO_EKPO i
  JOIN dw.dim_supplier s  ON s.SupplierNumber = i.LIFNR
  JOIN dw.dim_part p      ON p.PartNumber = i.MATNR
  JOIN dw.dim_warehouse w ON w.WarehouseCode = i.WERKS;

CREATE OR REPLACE TABLE dw.fact_goods_receipt AS
SELECT r.ReceiptId, r.PurchaseOrder, r.PoItem,
       CAST(strftime(r.ReceiptDate, '%Y%m%d') AS INTEGER) AS ReceiptDateKey,
       CAST(strftime(r.RequestedDate, '%Y%m%d') AS INTEGER) AS RequestedDateKey,
       po.SupplierKey, po.PartKey, po.WarehouseKey, r.QuantityReceived, r.QuantityRejected,
       date_diff('day', r.RequestedDate, r.ReceiptDate) AS DelayDays,
       date_diff('day', r.RequestedDate, r.ReceiptDate) <= 0 AS IsOnTime
  FROM stg.GoodsReceipts r
  JOIN dw.fact_purchase_order_line po ON po.PurchaseOrder = r.PurchaseOrder AND po.PoItem = r.PoItem;

CREATE OR REPLACE TABLE dw.fact_stock_monthly AS
SELECT CAST(strftime(m.SNAPSHOT_DATE, '%Y%m%d') AS INTEGER) AS SnapshotDateKey, p.PartKey, w.WarehouseKey,
       sum(m.LABST) AS QuantityOnHand, round(sum(m.LABST) * p.StandardPrice, 2) AS StockValue
  FROM stg.MARD m
  JOIN dw.dim_part p      ON p.PartNumber = m.MATNR
  JOIN dw.dim_warehouse w ON w.WarehouseCode = m.WERKS
 GROUP BY m.SNAPSHOT_DATE, p.PartKey, w.WarehouseKey, p.StandardPrice;
