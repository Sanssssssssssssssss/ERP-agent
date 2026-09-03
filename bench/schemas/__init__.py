"""
Pydantic schemas for Odoo training scenario validation
"""

from .models import (
    BOMComponentData,
    BOMData,
    CustomerData,
    ExistingSalesOrderData,
    ProductData,
    ScenarioData,
    StockLevelData,
    SystemParameterData,
    VendorData,
    VendorInfoData,
    WarehouseData,
)

__all__ = [
    "VendorData",
    "CustomerData",
    "WarehouseData",
    "VendorInfoData",
    "ProductData",
    "BOMComponentData",
    "BOMData",
    "StockLevelData",
    "ExistingSalesOrderData",
    "SystemParameterData",
    "ScenarioData",
]
