-- Star schema for the spare-parts Power BI model

CREATE TABLE dw.dim_date (
    DateKey      int      NOT NULL PRIMARY KEY,   -- yyyymmdd
    [Date]       date     NOT NULL,
    [Year]       smallint NOT NULL,
    [Quarter]    tinyint  NOT NULL,
    [Month]      tinyint  NOT NULL,
    MonthName    varchar(10) NOT NULL,
    YearMonth    char(7)  NOT NULL,               -- 2025-03
    IsWorkday    bit      NOT NULL
);

CREATE TABLE dw.dim_part (
    PartKey           int IDENTITY PRIMARY KEY,
    PartNumber        varchar(18)  NOT NULL UNIQUE,
    PartName          nvarchar(80) NOT NULL,
    Category          nvarchar(40) NOT NULL,
    UnitOfMeasure     varchar(3)   NOT NULL,
    StandardPrice     decimal(12,2) NOT NULL,
    LeadTimeDays      int NOT NULL,
    SafetyStock       int NOT NULL,
    MainSupplierNumber varchar(10) NULL,
    ValidFrom         datetime2(0) NOT NULL DEFAULT SYSUTCDATETIME()
);

CREATE TABLE dw.dim_supplier (
    SupplierKey    int IDENTITY PRIMARY KEY,
    SupplierNumber varchar(10)  NOT NULL UNIQUE,
    SupplierName   nvarchar(80) NOT NULL,
    Country        nvarchar(40) NOT NULL
);

CREATE TABLE dw.dim_warehouse (
    WarehouseKey  int IDENTITY PRIMARY KEY,
    WarehouseCode varchar(4)   NOT NULL UNIQUE,
    WarehouseName nvarchar(60) NOT NULL
);

CREATE TABLE dw.dim_dealer (
    DealerKey     int IDENTITY PRIMARY KEY,
    DealerId      int          NOT NULL UNIQUE,
    DealerName    nvarchar(80) NOT NULL,
    City          nvarchar(60) NOT NULL,
    Country       nvarchar(40) NOT NULL,
    HomeWarehouse varchar(4)   NOT NULL
);

-- Demand: one row per dealer order line
CREATE TABLE dw.fact_dealer_order_line (
    OrderLineId     bigint NOT NULL PRIMARY KEY,
    OrderDateKey    int NOT NULL REFERENCES dw.dim_date (DateKey),
    DealerKey       int NOT NULL REFERENCES dw.dim_dealer (DealerKey),
    PartKey         int NOT NULL REFERENCES dw.dim_part (PartKey),
    WarehouseKey    int NOT NULL REFERENCES dw.dim_warehouse (WarehouseKey),
    QuantityOrdered int NOT NULL,
    QuantityShipped int NOT NULL,
    SalesValue      decimal(14,2) NOT NULL,          -- shipped × standard price
    Status          varchar(12) NOT NULL
);

-- Supply: one row per purchase order item
CREATE TABLE dw.fact_purchase_order_line (
    PurchaseOrder   varchar(10) NOT NULL,
    PoItem          int NOT NULL,
    OrderDateKey    int NOT NULL REFERENCES dw.dim_date (DateKey),
    RequestedDateKey int NOT NULL REFERENCES dw.dim_date (DateKey),
    SupplierKey     int NOT NULL REFERENCES dw.dim_supplier (SupplierKey),
    PartKey         int NOT NULL REFERENCES dw.dim_part (PartKey),
    WarehouseKey    int NOT NULL REFERENCES dw.dim_warehouse (WarehouseKey),
    QuantityOrdered int NOT NULL,
    NetPrice        decimal(12,2) NOT NULL,
    OrderValue      decimal(14,2) NOT NULL,
    CONSTRAINT PK_fact_purchase_order_line PRIMARY KEY (PurchaseOrder, PoItem)
);

-- Supplier performance: one row per goods receipt
CREATE TABLE dw.fact_goods_receipt (
    ReceiptId        bigint NOT NULL PRIMARY KEY,
    PurchaseOrder    varchar(10) NOT NULL,
    PoItem           int NOT NULL,
    ReceiptDateKey   int NOT NULL REFERENCES dw.dim_date (DateKey),
    RequestedDateKey int NOT NULL REFERENCES dw.dim_date (DateKey),
    SupplierKey      int NOT NULL REFERENCES dw.dim_supplier (SupplierKey),
    PartKey          int NOT NULL REFERENCES dw.dim_part (PartKey),
    WarehouseKey     int NOT NULL REFERENCES dw.dim_warehouse (WarehouseKey),
    QuantityReceived int NOT NULL,
    QuantityRejected int NOT NULL,
    DelayDays        int NOT NULL,                  -- receipt date − requested date
    IsOnTime         bit NOT NULL                   -- DelayDays <= 0
);

-- Inventory: month-end stock snapshot per part and warehouse
CREATE TABLE dw.fact_stock_monthly (
    SnapshotDateKey int NOT NULL REFERENCES dw.dim_date (DateKey),
    PartKey         int NOT NULL REFERENCES dw.dim_part (PartKey),
    WarehouseKey    int NOT NULL REFERENCES dw.dim_warehouse (WarehouseKey),
    QuantityOnHand  int NOT NULL,
    StockValue      decimal(14,2) NOT NULL,
    CONSTRAINT PK_fact_stock_monthly PRIMARY KEY (SnapshotDateKey, PartKey, WarehouseKey)
);
GO
