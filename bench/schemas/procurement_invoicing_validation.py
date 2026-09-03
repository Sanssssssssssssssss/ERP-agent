from __future__ import annotations

from enum import Enum


def _enum_value(value: object) -> object:
    if isinstance(value, Enum):
        return value.value
    return value


def validate_procurement_invoicing_policy(
    *,
    invoice_required: bool,
    payment_term: object | None,
    downpayment_required: bool,
    downpayment_threshold_amount: float | None,
    downpayment_mode: object | None,
    downpayment_value: float | None,
    invoice_required_label: str = "invoice_required",
    payment_term_label: str = "payment_term",
    downpayment_required_label: str = "downpayment_required",
    downpayment_fields_name: str = "downpayment fields",
    downpayment_threshold_amount_label: str = "downpayment_threshold_amount",
    downpayment_mode_label: str = "downpayment_mode",
    downpayment_value_label: str = "downpayment_value",
    percentage_downpayment_value_label: str = "downpayment_value",
) -> None:
    if not invoice_required:
        if (
            payment_term is not None
            or downpayment_required
            or downpayment_threshold_amount is not None
            or downpayment_mode is not None
            or downpayment_value is not None
        ):
            raise ValueError(
                f"payment_term and {downpayment_fields_name} must be omitted when "
                f"{invoice_required_label} is false"
            )
        return
    if payment_term is None:
        raise ValueError(f"{payment_term_label} is required when {invoice_required_label} is true")
    if not downpayment_required:
        if (
            downpayment_threshold_amount is not None
            or downpayment_mode is not None
            or downpayment_value is not None
        ):
            raise ValueError(
                f"{downpayment_fields_name} must be omitted when {downpayment_required_label} is false"
            )
        return
    if downpayment_threshold_amount is None:
        raise ValueError(
            f"{downpayment_threshold_amount_label} is required when {downpayment_required_label} is true"
        )
    if downpayment_mode is None:
        raise ValueError(
            f"{downpayment_mode_label} is required when {downpayment_required_label} is true"
        )
    if downpayment_value is None:
        raise ValueError(
            f"{downpayment_value_label} is required when {downpayment_required_label} is true"
        )
    if downpayment_threshold_amount <= 0:
        raise ValueError(f"{downpayment_threshold_amount_label} must be > 0")
    if downpayment_value <= 0:
        raise ValueError(f"{downpayment_value_label} must be > 0")
    if _enum_value(downpayment_mode) == "percentage" and downpayment_value > 100:
        raise ValueError(f"percentage {percentage_downpayment_value_label} must be <= 100")
