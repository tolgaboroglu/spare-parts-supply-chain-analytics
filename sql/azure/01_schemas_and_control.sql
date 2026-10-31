-- Azure SQL Database: schemas, control tables and staging tables
-- stg      raw copies from the lake (truncated on every load)
-- dw       star schema used by Power BI
-- control  watermarks, run log and data quality results

CREATE SCHEMA stg;
GO
CREATE SCHEMA dw;
GO
CREATE SCHEMA control;
GO

CREATE TABLE control.Watermark (
    SourceTable     varchar(128) NOT NULL PRIMARY KEY,
    WatermarkValue  datetime2(0) NOT NULL,
    UpdatedAt       datetime2(0) NOT NULL DEFAULT SYSUTCDATETIME()
);
INSERT INTO control.Watermark (SourceTable, WatermarkValue) VALUES
    ('service.Dealers',      '1900-01-01'),
    ('service.DealerOrders', '1900-01-01'),
    ('qm.GoodsReceipts',     '1900-01-01');
GO

CREATE OR ALTER PROCEDURE control.usp_UpdateWatermark
    @SourceTable varchar(128), @WatermarkValue datetime2(0)
AS
BEGIN
    SET NOCOUNT ON;
    UPDATE control.Watermark
       SET WatermarkValue = @WatermarkValue, UpdatedAt = SYSUTCDATETIME()
     WHERE SourceTable = @SourceTable;
END;
GO

CREATE TABLE control.DataQualityResult (
    RunAt      datetime2(0) NOT NULL DEFAULT SYSUTCDATETIME(),
    CheckName  varchar(100) NOT NULL,
    FailedRows int          NOT NULL,
    Severity   varchar(10)  NOT NULL   -- 'error' stops the pipeline, 'warning' is logged
);
GO

-- Staging: column names kept as in the source systems
CREATE TABLE stg.MARA (MATNR varchar(18), MAKTX nvarchar(80), MATKL nvarchar(40), MEINS varchar(3),
                       STPRS decimal(12,2), PLIFZ int, LIFNR varchar(10), EISBE int);
CREATE TABLE stg.T001W (WERKS varchar(4), NAME1 nvarchar(60));
CREATE TABLE stg.LFA1 (LIFNR varchar(10), NAME1 nvarchar(80), LAND1 nvarchar(40));
CREATE TABLE stg.EKKO (EBELN varchar(10), BEDAT date, LIFNR varchar(10), AEDAT date);
CREATE TABLE stg.EKPO (EBELN varchar(10), EBELP int, MATNR varchar(18), WERKS varchar(4),
                       MENGE int, NETPR decimal(12,2), EINDT date, AEDAT date);
CREATE TABLE stg.MARD (MATNR varchar(18), WERKS varchar(4), LGORT varchar(4), SNAPSHOT_DATE date, LABST int);
CREATE TABLE stg.Dealers (DealerId int, DealerName nvarchar(80), City nvarchar(60), Country nvarchar(40),
                          HomeWarehouse varchar(4), ModifiedAt datetime2(0));
CREATE TABLE stg.DealerOrders (OrderLineId bigint, OrderDate date, DealerId int, Material varchar(18),
                               Warehouse varchar(4), QuantityOrdered int, QuantityShipped int,
                               Status varchar(12), ModifiedAt datetime2(0));
CREATE TABLE stg.GoodsReceipts (ReceiptId bigint, PurchaseOrder varchar(10), PoItem int, Material varchar(18),
                                Plant varchar(4), ReceiptDate date, RequestedDate date,
                                QuantityReceived int, QuantityRejected int, ModifiedAt datetime2(0));
GO
