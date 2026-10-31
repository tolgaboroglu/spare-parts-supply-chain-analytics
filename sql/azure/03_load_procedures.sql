-- Load procedures called by ADF pipeline pl_load_dw

CREATE OR ALTER PROCEDURE dw.usp_LoadDimensions
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;
    BEGIN TRANSACTION;

    MERGE dw.dim_part AS t
    USING (SELECT MATNR, MAKTX, MATKL, MEINS, STPRS, PLIFZ, EISBE, LIFNR FROM stg.MARA) AS s
       ON t.PartNumber = s.MATNR
    WHEN MATCHED AND (t.StandardPrice <> s.STPRS OR t.LeadTimeDays <> s.PLIFZ OR t.SafetyStock <> s.EISBE
                      OR t.PartName <> s.MAKTX OR ISNULL(t.MainSupplierNumber, '') <> ISNULL(s.LIFNR, ''))
        THEN UPDATE SET PartName = s.MAKTX, Category = s.MATKL, UnitOfMeasure = s.MEINS, StandardPrice = s.STPRS,
                        LeadTimeDays = s.PLIFZ, SafetyStock = s.EISBE, MainSupplierNumber = s.LIFNR,
                        ValidFrom = SYSUTCDATETIME()
    WHEN NOT MATCHED BY TARGET
        THEN INSERT (PartNumber, PartName, Category, UnitOfMeasure, StandardPrice, LeadTimeDays, SafetyStock, MainSupplierNumber)
             VALUES (s.MATNR, s.MAKTX, s.MATKL, s.MEINS, s.STPRS, s.PLIFZ, s.EISBE, s.LIFNR);

    MERGE dw.dim_supplier AS t
    USING stg.LFA1 AS s ON t.SupplierNumber = s.LIFNR
    WHEN MATCHED AND (t.SupplierName <> s.NAME1 OR t.Country <> s.LAND1)
        THEN UPDATE SET SupplierName = s.NAME1, Country = s.LAND1
    WHEN NOT MATCHED BY TARGET
        THEN INSERT (SupplierNumber, SupplierName, Country) VALUES (s.LIFNR, s.NAME1, s.LAND1);

    -- Dealers arrive incrementally: only changed rows are in staging
    MERGE dw.dim_dealer AS t
    USING stg.Dealers AS s ON t.DealerId = s.DealerId
    WHEN MATCHED
        THEN UPDATE SET DealerName = s.DealerName, City = s.City, Country = s.Country, HomeWarehouse = s.HomeWarehouse
    WHEN NOT MATCHED BY TARGET
        THEN INSERT (DealerId, DealerName, City, Country, HomeWarehouse)
             VALUES (s.DealerId, s.DealerName, s.City, s.Country, s.HomeWarehouse);

    MERGE dw.dim_warehouse AS t
    USING stg.T001W AS s ON t.WarehouseCode = s.WERKS
    WHEN MATCHED AND t.WarehouseName <> s.NAME1 THEN UPDATE SET WarehouseName = s.NAME1
    WHEN NOT MATCHED BY TARGET THEN INSERT (WarehouseCode, WarehouseName) VALUES (s.WERKS, s.NAME1);

    COMMIT;
END;
GO

CREATE OR ALTER PROCEDURE dw.usp_LoadFacts
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;
    BEGIN TRANSACTION;

    -- Dealer orders: upsert changed lines (status changes from Backorder to Shipped)
    MERGE dw.fact_dealer_order_line AS t
    USING (
        SELECT o.OrderLineId, CONVERT(int, FORMAT(o.OrderDate, 'yyyyMMdd')) AS OrderDateKey,
               d.DealerKey, p.PartKey, w.WarehouseKey, o.QuantityOrdered, o.QuantityShipped,
               CAST(o.QuantityShipped * p.StandardPrice AS decimal(14,2)) AS SalesValue, o.Status
          FROM stg.DealerOrders AS o
          JOIN dw.dim_dealer    AS d ON d.DealerId      = o.DealerId
          JOIN dw.dim_part      AS p ON p.PartNumber    = o.Material
          JOIN dw.dim_warehouse AS w ON w.WarehouseCode = o.Warehouse
    ) AS s ON t.OrderLineId = s.OrderLineId
    WHEN MATCHED THEN UPDATE SET QuantityShipped = s.QuantityShipped, SalesValue = s.SalesValue, Status = s.Status
    WHEN NOT MATCHED BY TARGET THEN
        INSERT (OrderLineId, OrderDateKey, DealerKey, PartKey, WarehouseKey, QuantityOrdered, QuantityShipped, SalesValue, Status)
        VALUES (s.OrderLineId, s.OrderDateKey, s.DealerKey, s.PartKey, s.WarehouseKey, s.QuantityOrdered, s.QuantityShipped, s.SalesValue, s.Status);

    -- Purchase orders: header (EKKO) + item (EKPO)
    MERGE dw.fact_purchase_order_line AS t
    USING (
        SELECT i.EBELN, i.EBELP,
               CONVERT(int, FORMAT(h.BEDAT, 'yyyyMMdd')) AS OrderDateKey,
               CONVERT(int, FORMAT(i.EINDT, 'yyyyMMdd')) AS RequestedDateKey,
               s.SupplierKey, p.PartKey, w.WarehouseKey, i.MENGE, i.NETPR,
               CAST(i.MENGE * i.NETPR AS decimal(14,2)) AS OrderValue
          FROM stg.EKPO AS i
          JOIN stg.EKKO          AS h ON h.EBELN          = i.EBELN
          JOIN dw.dim_supplier   AS s ON s.SupplierNumber = h.LIFNR
          JOIN dw.dim_part       AS p ON p.PartNumber     = i.MATNR
          JOIN dw.dim_warehouse  AS w ON w.WarehouseCode  = i.WERKS
    ) AS s ON t.PurchaseOrder = s.EBELN AND t.PoItem = s.EBELP
    WHEN MATCHED THEN UPDATE SET RequestedDateKey = s.RequestedDateKey, QuantityOrdered = s.MENGE,
                                 NetPrice = s.NETPR, OrderValue = s.OrderValue
    WHEN NOT MATCHED BY TARGET THEN
        INSERT (PurchaseOrder, PoItem, OrderDateKey, RequestedDateKey, SupplierKey, PartKey, WarehouseKey, QuantityOrdered, NetPrice, OrderValue)
        VALUES (s.EBELN, s.EBELP, s.OrderDateKey, s.RequestedDateKey, s.SupplierKey, s.PartKey, s.WarehouseKey, s.MENGE, s.NETPR, s.OrderValue);

    -- Goods receipts: supplier comes from the purchase order the receipt belongs to
    MERGE dw.fact_goods_receipt AS t
    USING (
        SELECT r.ReceiptId, r.PurchaseOrder, r.PoItem,
               CONVERT(int, FORMAT(r.ReceiptDate, 'yyyyMMdd'))   AS ReceiptDateKey,
               CONVERT(int, FORMAT(r.RequestedDate, 'yyyyMMdd')) AS RequestedDateKey,
               po.SupplierKey, po.PartKey, po.WarehouseKey, r.QuantityReceived, r.QuantityRejected,
               DATEDIFF(day, r.RequestedDate, r.ReceiptDate) AS DelayDays
          FROM stg.GoodsReceipts AS r
          JOIN dw.fact_purchase_order_line AS po ON po.PurchaseOrder = r.PurchaseOrder AND po.PoItem = r.PoItem
    ) AS s ON t.ReceiptId = s.ReceiptId
    WHEN NOT MATCHED BY TARGET THEN
        INSERT (ReceiptId, PurchaseOrder, PoItem, ReceiptDateKey, RequestedDateKey, SupplierKey, PartKey, WarehouseKey,
                QuantityReceived, QuantityRejected, DelayDays, IsOnTime)
        VALUES (s.ReceiptId, s.PurchaseOrder, s.PoItem, s.ReceiptDateKey, s.RequestedDateKey, s.SupplierKey, s.PartKey, s.WarehouseKey,
                s.QuantityReceived, s.QuantityRejected, s.DelayDays, CASE WHEN s.DelayDays <= 0 THEN 1 ELSE 0 END);

    -- Month-end stock snapshots: replace the months present in staging
    DELETE f
      FROM dw.fact_stock_monthly AS f
     WHERE f.SnapshotDateKey IN (SELECT DISTINCT CONVERT(int, FORMAT(SNAPSHOT_DATE, 'yyyyMMdd')) FROM stg.MARD);

    INSERT INTO dw.fact_stock_monthly (SnapshotDateKey, PartKey, WarehouseKey, QuantityOnHand, StockValue)
    SELECT CONVERT(int, FORMAT(m.SNAPSHOT_DATE, 'yyyyMMdd')), p.PartKey, w.WarehouseKey,
           SUM(m.LABST), CAST(SUM(m.LABST) * p.StandardPrice AS decimal(14,2))
      FROM stg.MARD AS m
      JOIN dw.dim_part      AS p ON p.PartNumber    = m.MATNR
      JOIN dw.dim_warehouse AS w ON w.WarehouseCode = m.WERKS
     GROUP BY m.SNAPSHOT_DATE, p.PartKey, w.WarehouseKey, p.StandardPrice;

    COMMIT;
END;
GO

CREATE OR ALTER PROCEDURE control.usp_DataQualityChecks
AS
BEGIN
    SET NOCOUNT ON;
    DECLARE @r TABLE (CheckName varchar(100), FailedRows int, Severity varchar(10));

    INSERT INTO @r
    SELECT 'dealer order line with unknown part', COUNT(*), 'error'
      FROM stg.DealerOrders AS o WHERE NOT EXISTS (SELECT 1 FROM dw.dim_part AS p WHERE p.PartNumber = o.Material)
    UNION ALL
    SELECT 'goods receipt without purchase order', COUNT(*), 'error'
      FROM stg.GoodsReceipts AS r
     WHERE NOT EXISTS (SELECT 1 FROM dw.fact_purchase_order_line AS po WHERE po.PurchaseOrder = r.PurchaseOrder AND po.PoItem = r.PoItem)
    UNION ALL
    SELECT 'shipped more than ordered', COUNT(*), 'error'
      FROM stg.DealerOrders WHERE QuantityShipped > QuantityOrdered
    UNION ALL
    SELECT 'rejected more than received', COUNT(*), 'error'
      FROM stg.GoodsReceipts WHERE QuantityRejected > QuantityReceived
    UNION ALL
    SELECT 'negative stock', COUNT(*), 'error'
      FROM stg.MARD WHERE LABST < 0
    UNION ALL
    SELECT 'part without price', COUNT(*), 'warning'
      FROM dw.dim_part WHERE StandardPrice <= 0;

    INSERT INTO control.DataQualityResult (CheckName, FailedRows, Severity) SELECT * FROM @r;

    IF EXISTS (SELECT 1 FROM @r WHERE Severity = 'error' AND FailedRows > 0)
        THROW 50001, 'Data quality checks failed, see control.DataQualityResult', 1;
END;
GO
