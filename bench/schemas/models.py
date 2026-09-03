#!/usr/bin/env python3
"""
Pydantic models for procurement scenario data validation.
Supports both Pydantic validation and JSON Schema export.
"""

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from .procurement_invoicing_validation import validate_procurement_invoicing_policy


class VendorData(BaseModel):
    """Vendor/supplier partner information"""

    name: str = Field(..., description="Vendor company name")
    ref: str = Field(..., description="Unique reference code for the vendor")
    supplier_rank: int = Field(default=1, description="Supplier ranking (1 = active supplier)")
    comment: str | None = Field(None, description="Additional notes about the vendor")


class CustomerData(BaseModel):
    """Customer partner information"""

    name: str = Field(..., description="Customer name (company or individual)")
    ref: str = Field(..., description="Unique reference code for the customer")
    email: str = Field(..., description="Customer email address")
    budget_dollars: float | None = Field(
        None, description="Customer budget ceiling used by procurement scenarios"
    )
    is_company: bool = Field(default=True, description="Whether this is a company or individual")
    comment: str | None = Field(None, description="Additional notes about the customer")
    credit_limit: float | None = Field(
        None, description="Customer credit limit (native Odoo field)"
    )
    payment_term: str | None = Field(None, description="Payment term code used by the scenario")
    tags: list[str] = Field(
        default_factory=list, description="Partner tags/categories (e.g., 'new_customer', 'vip')"
    )


ProcurementPaymentTermLiteral = Literal["immediate", "net_30"]
ProcurementDownpaymentModeLiteral = Literal["percentage", "fixed_amount"]


class ProcurementInvoicingPolicyData(BaseModel):
    """Procurement task invoicing policy."""

    invoice_required: bool = Field(
        default=False, description="Whether retained procurement orders must be invoiced"
    )
    payment_term: ProcurementPaymentTermLiteral | None = Field(
        default=None,
        description="Global payment term policy for task customers when invoicing is required",
    )
    downpayment_required: bool = Field(
        default=False,
        description="Whether some retained orders must use the down payment invoice flow",
    )
    downpayment_threshold_amount: float | None = Field(
        default=None,
        description="Untaxed sales-order amount threshold that triggers down payment invoicing",
    )
    downpayment_mode: ProcurementDownpaymentModeLiteral | None = Field(
        default=None, description="Down payment policy mode"
    )
    downpayment_value: float | None = Field(
        default=None,
        description="Down payment percentage or fixed amount depending on downpayment_mode",
    )

    @model_validator(mode="after")
    def validate_policy(self) -> "ProcurementInvoicingPolicyData":
        validate_procurement_invoicing_policy(
            invoice_required=self.invoice_required,
            payment_term=self.payment_term,
            downpayment_required=self.downpayment_required,
            downpayment_threshold_amount=self.downpayment_threshold_amount,
            downpayment_mode=self.downpayment_mode,
            downpayment_value=self.downpayment_value,
        )
        return self


class WarehouseData(BaseModel):
    """Warehouse configuration"""

    name: str = Field(..., description="Warehouse name")
    code: str = Field(..., description="Short code for the warehouse (e.g., SEA, CHI)")
    reception_steps: Literal["one_step", "two_steps", "three_steps"] = Field(
        default="one_step",
        description="Reception process: one_step (receive), two_steps (receive+QC), three_steps (receive+QC+stock)",
    )
    delivery_steps: Literal["ship_only", "pick_ship", "pick_pack_ship"] = Field(
        default="ship_only", description="Delivery process steps"
    )


class VendorInfoData(BaseModel):
    """Product vendor relationship (supplier info)"""

    partner_ref: str = Field(..., description="Reference to vendor (must match VendorData.ref)")
    delay: int = Field(..., description="Lead time in days")
    min_qty: float = Field(default=1.0, description="Minimum order quantity")
    max_qty: float | None = Field(
        None,
        description="Maximum order quantity supported by this vendor (None means unbounded)",
    )
    price: float = Field(..., description="Unit price from this vendor")
    name: str | None = Field(
        None, description="Description (e.g., 'Expedited Delivery', 'Standard')"
    )


class ProductData(BaseModel):
    """Product definition"""

    name: str = Field(..., description="Product name")
    code: str = Field(..., description="Internal reference code (SKU)")
    category: str = Field(default="Office Furniture", description="Product category")
    type: Literal["consu", "product", "service"] = Field(
        default="consu",
        description="Product type: consu (consumable/storable), product (stockable), service",
    )
    list_price: float = Field(..., description="Sales price")
    standard_price: float = Field(..., description="Cost price")
    routes: list[Literal["buy", "mto", "manufacture", "dropship"]] = Field(
        default_factory=list,
        description="Product routes: buy (purchase), mto (make-to-order), manufacture, dropship (vendor-to-customer)",
    )
    vendor_info: list[VendorInfoData] = Field(
        default_factory=list,
        description="Vendor information (can have multiple for same vendor with different lead times/prices)",
    )


class BOMComponentData(BaseModel):
    """Bill of Materials component line"""

    component_code: str = Field(..., description="Product code of the component")
    quantity: float = Field(..., description="Quantity needed per unit of finished product")


class BOMData(BaseModel):
    """Bill of Materials definition"""

    product_code: str = Field(..., description="Product code for the finished product")
    type: Literal["normal"] = Field(default="normal", description="BOM type")
    quantity: float = Field(default=1.0, description="Output quantity produced by this BOM")
    components: list[BOMComponentData] = Field(..., description="List of components needed")
    warehouse_code: str | None = Field(
        None,
        description="Warehouse code where manufacturing happens (sets operation type and component locations)",
    )


class WorkcenterData(BaseModel):
    """Manufacturing work center configuration for assembly operations."""

    product_code: str = Field(
        ..., description="Finished product code associated with this work center"
    )
    code: str = Field(..., description="Unique work center code (e.g., 'WC_SPP001')")
    name: str = Field(..., description="Display name of the work center")
    unit_cost: float = Field(..., description="Assembly cost per finished unit")
    costs_hour: float = Field(..., description="Hourly cost rate for the work center")
    time_per_unit_minutes: float = Field(..., description="Processing time per unit in minutes")
    time_efficiency: float = Field(
        default=100.0, description="Efficiency percentage (100.0 = 100%)"
    )
    time_start: float = Field(default=0.0, description="Setup time in minutes prior to production")
    time_stop: float = Field(default=0.0, description="Cleanup time in minutes after production")
    oee_target: float = Field(default=1.0, description="Overall equipment effectiveness target")
    note: str | None = Field(None, description="Operational notes for the workcenter")
    capacity_minutes: float = Field(
        ..., description="Total processing minutes this workcenter can provide across the scenario horizon"
    )
    lead_days: float | None = Field(
        default=None,
        description="Lead time window in days associated with the assembly plan (optional metadata)",
    )
    operation_name: str | None = Field(
        default=None,
        description="Logical BoM operation name for this product/workcenter pairing",
    )
    sequence: int = Field(
        default=100,
        description="BoM operation sequence for this product/workcenter pairing",
    )
    is_primary: bool = Field(
        default=False,
        description="Whether this row defines the primary workcenter for the logical operation",
    )
    alternative_workcenter_codes: list[str] = Field(
        default_factory=list,
        description="Alternative workcenter codes allowed for the same logical operation",
    )


class StockLevelData(BaseModel):
    """Initial stock level configuration"""

    product_code: str = Field(..., description="Product code to set stock for")
    warehouse_code: str | None = Field(None, description="Warehouse code (None = main warehouse)")
    quantity: float = Field(..., description="Stock quantity to set")
    location_type: Literal["stock", "input", "qc", "output"] = Field(
        default="stock", description="Location type within warehouse"
    )


class ExistingPurchaseOrderData(BaseModel):
    """Existing purchase order that must already be present in the system."""

    ref: str = Field(..., description="Stable external reference for this seeded PO")
    plan_ref: str | None = Field(
        None, description="Baseline solver plan ref for this seeded PO"
    )
    vendor_ref: str = Field(..., description="Reference to the vendor")
    product_code: str = Field(..., description="Product code being purchased")
    quantity: float = Field(..., description="Order quantity")
    price_unit: float = Field(..., description="Seeded unit purchase price")
    planned_arrival_days: int = Field(
        ..., description="Days from the scenario anchor when the PO is planned to arrive"
    )
    origin_customer_refs: list[str] = Field(
        default_factory=list,
        description="Customer refs whose sales orders should appear in the PO origin",
    )
    origin_plan_refs: list[str] = Field(
        default_factory=list,
        description="Baseline plan refs whose live document names should appear in the PO origin",
    )
    supply_role: Literal["finished", "component"] = Field(
        default="finished",
        description="Whether this seeded PO covers finished goods or components",
    )
    offer_key: str | None = Field(
        None,
        description="Resolved supplier-offer identity used for repair verification",
    )
    state: Literal["purchase"] = Field(
        default="purchase",
        description="Initial seeded PO state (confirmed purchase order)",
    )
    note: str | None = Field(None, description="Traceability note stored on the seeded PO")


class ExistingManufacturingOrderData(BaseModel):
    """Existing manufacturing order that must already be present in the system."""

    ref: str = Field(..., description="Stable external reference for this seeded MO")
    plan_ref: str | None = Field(
        None, description="Baseline solver plan ref for this seeded MO"
    )
    product_code: str = Field(..., description="Product code being manufactured")
    quantity: float = Field(..., description="Manufacturing quantity")
    workcenter_code: str = Field(..., description="Assigned workcenter code")
    start_days: int = Field(
        ..., description="Days from the scenario anchor when work should start"
    )
    deadline_days: int = Field(
        ..., description="Days from the scenario anchor when work should finish"
    )
    origin_customer_refs: list[str] = Field(
        default_factory=list,
        description="Customer refs whose sales orders should appear in the MO origin",
    )
    origin_plan_refs: list[str] = Field(
        default_factory=list,
        description="Baseline plan refs whose live document names should appear in the MO origin",
    )
    level: int = Field(
        default=0,
        description="Manufacturing depth level used when seeding the baseline commitment graph",
    )
    state: Literal["confirmed"] = Field(
        default="confirmed",
        description="Initial seeded MO state (confirmed manufacturing order)",
    )
    note: str | None = Field(None, description="Traceability note stored on the seeded MO")


class ExistingSalesOrderData(BaseModel):
    """Existing sales order (already in system)"""

    ref: str = Field(..., description="Stable external order reference for this seeded SO")
    customer_ref: str = Field(..., description="Reference to customer")
    product_code: str = Field(..., description="Product code ordered")
    quantity: float = Field(..., description="Order quantity")
    order_days_ago: int = Field(..., description="How many days ago this order was placed")
    commitment_days: int = Field(..., description="Days from order date when delivery is promised")
    warehouse_code: str | None = Field(None, description="Warehouse code for delivery (optional)")
    state: Literal["draft", "sent", "sale"] = Field(
        default="draft",
        description="Initial pre-procurement sales order state",
    )
    price_unit: float = Field(..., description="Seeded unit sale price")
    note: str | None = Field(None, description="Traceability note stored on the seeded order")


ProcurementRepairKindLiteral = Literal["supplier_cancellation", "workcenter_outage"]


class ProcurementRepairData(BaseModel):
    """Repair-specific context baked into the seeded task state."""

    kind: ProcurementRepairKindLiteral
    broken_supplier_offer_key: str | None = Field(
        None, description="Supplier offer that is no longer valid for repair scenarios"
    )
    broken_supplier_vendor_ref: str | None = Field(
        None, description="Vendor ref associated with the broken supplier offer"
    )
    broken_workcenter_code: str | None = Field(
        None, description="Workcenter code that is no longer usable"
    )
    impacted_customer_refs: list[str] = Field(
        default_factory=list,
        description="Customer refs whose committed fulfillment path was directly disrupted",
    )
    seeded_sales_order_refs: list[str] = Field(
        default_factory=list,
        description="External refs of seeded sales orders that must be reused",
    )
    seeded_purchase_order_refs: list[str] = Field(
        default_factory=list,
        description="External refs of all seeded purchase-order commitments in the baseline",
    )
    seeded_manufacturing_order_refs: list[str] = Field(
        default_factory=list,
        description="External refs of all seeded manufacturing-order commitments in the baseline",
    )
    broken_purchase_order_refs: list[str] = Field(
        default_factory=list,
        description="External refs of seeded purchase orders that must be canceled during repair",
    )
    broken_manufacturing_order_refs: list[str] = Field(
        default_factory=list,
        description="External refs of seeded manufacturing orders that must be canceled during repair",
    )


class SystemParameterData(BaseModel):
    """System-wide configuration parameters stored in ir.config_parameter"""

    key: str = Field(..., description="Parameter key (e.g., 'erp_bench.min_margin_percent')")
    value: str = Field(..., description="Parameter value (stored as string)")


class ScenarioData(BaseModel):
    """Complete training scenario definition"""

    scenario_number: int = Field(..., description="Scenario number (e.g. 1, 2, 3, ...)")
    name: str = Field(..., description="Scenario name/title")
    instruction: str = Field(..., description="The instruction/request given to the AI agent")
    seed: int | None = Field(
        None,
        description="Seed that fully reproduces this scenario. Run generator with this seed to recreate.",
    )
    background_and_policy: str | None = Field(
        None,
        description=(
            "Scenario-specific background context and policies the agent must follow "
            "that are not encoded in the ERP"
        ),
    )
    invoicing_policy: ProcurementInvoicingPolicyData = Field(
        default_factory=ProcurementInvoicingPolicyData,
        description="Procurement invoicing rules for retained sales orders",
    )
    warehouses: list[WarehouseData] = Field(
        default_factory=list, description="Warehouses needed for this scenario"
    )
    vendors: list[VendorData] = Field(
        default_factory=list, description="Vendors needed for this scenario"
    )
    customers: list[CustomerData] = Field(..., description="Customers for this scenario")
    products: list[ProductData] = Field(..., description="Products (finished goods and components)")
    boms: list[BOMData] = Field(default_factory=list, description="Bills of materials")
    workcenters: list[WorkcenterData] = Field(
        default_factory=list, description="Manufacturing work centers required for assembly"
    )
    stock_levels: list[StockLevelData] = Field(
        default_factory=list, description="Initial stock to set"
    )
    existing_purchase_orders: list[ExistingPurchaseOrderData] = Field(
        default_factory=list,
        description="Purchase orders that already exist in the system",
    )
    existing_manufacturing_orders: list[ExistingManufacturingOrderData] = Field(
        default_factory=list,
        description="Manufacturing orders that already exist in the system",
    )
    existing_sales_orders: list[ExistingSalesOrderData] = Field(
        default_factory=list,
        description="Sales orders that already exist in the system (will be created as real SOs)",
    )
    repair_context: ProcurementRepairData | None = Field(
        default=None,
        description="Repair-specific disruption metadata baked into the task state",
    )
    system_parameters: list[SystemParameterData] = Field(
        default_factory=list,
        description="System-wide configuration parameters (business policies, thresholds)",
    )


def export_json_schema(output_file: str = "schemas/schema.json"):
    """Export JSON Schema for ScenarioData model"""
    import json
    from pathlib import Path

    schema = ScenarioData.model_json_schema()

    # Ensure directory exists
    Path(output_file).parent.mkdir(parents=True, exist_ok=True)

    with open(output_file, "w") as f:
        json.dump(schema, f, indent=2)

    print(f"JSON Schema exported to {output_file}")


if __name__ == "__main__":
    export_json_schema()
