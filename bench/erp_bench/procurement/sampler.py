#!/usr/bin/env python3
from __future__ import annotations

import logging
import math
import secrets
import statistics
import time
from collections import defaultdict
from collections.abc import Sequence
from hashlib import blake2s
from pathlib import Path
from typing import Literal, cast

import numpy as _np
from numpy.random import Generator
from pydantic import BaseModel, ConfigDict, Field, model_validator

from schemas.models import (
    SystemParameterData,
)
from schemas.procurement_invoicing_validation import validate_procurement_invoicing_policy

from ..harbor import HarborTaskExporter
from .adjacent_data import (
    infer_domain_from_blueprint as _infer_domain_from_blueprint_impl,
)
from .adjacent_data import (
    inject_adjacent_data as _inject_adjacent_data_impl,
)
from .config import (
    AvailableRoutes,
    BomStructure,
    OrderAcceptanceMode,
    ProcurementDatasetConfig,
    ProcurementDatasetEntry,
    ProcurementScenarioConfig,
    RepairScenarioKind,
    SeededOrderCoverage,
    WorkcenterChoicePath,
    load_procurement_config,
)
from .presets import DIFFICULTY_PRESETS
from .procurement_objectives import (
    OPTIMIZING_PROCUREMENT_OBJECTIVES,
    ProcurementObjectiveKind,
)
from .product_domains import (
    DEFAULT_PRODUCT_DOMAIN_KEYS,
    PRODUCT_DOMAINS,
    ComponentTemplate,
    ProductDomain,
)
from .prompts import build_background_and_policy, build_instruction
from .solver import (
    ComponentSpec,
    CustomerSpec,
    ExistingManufacturingOrderData,
    ExistingPurchaseOrderData,
    ManufacturingOnlyConfig,
    ManufacturingProductSpec,
    ProcurementInvoicingPolicy,
    ProcurementRepairContext,
    ProductSpec,
    RepairManufacturingQuantity,
    RepairOfferQuantity,
    ScenarioBlueprint,
    ScenarioBuild,
    ScenarioGenerator,
    UnsatAcceptancePolicy,
    VendorSpec,
    WorkcenterChoiceSpec,
    WorkcenterSpec,
    format_margin_percent,
    log_plan,
    log_plan_guidance,
)
from .topologies import build_manufacturing_graph

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

DEFAULT_BASE_SEED = 42

MEDIUM_MANUFACTURING_PATTERNS: tuple[str, ...] = (
    "cost_driven",
    "capability_restricted",
    "split_by_capacity",
    "multilevel_basic",
    "multilevel_capability",
)

HARD_MANUFACTURING_PATTERNS: tuple[str, ...] = (
    "cost_driven",
    "capability_restricted",
    "split_by_capacity",
    "multilevel_shared_capacity",
    "multilevel_multi_branch",
    "multilevel_deep_mixed",
    "multilevel_shared_leaf",
)

MULTI_WORKCENTER_ROUTING_PATTERNS: tuple[str, ...] = (
    "cost_driven",
    "capability_restricted",
    "split_by_capacity",
)

ROUTING_PATTERN_COVERAGE_DIFFICULTIES: tuple[str, ...] = ("medium", "hard")
OBJECTIVE_CERTIFICATION_THRESHOLDS: dict[ProcurementObjectiveKind, float] = {
    ProcurementObjectiveKind.vendor_consolidation: 1.0,
    ProcurementObjectiveKind.capacity_preservation: 1.0,
}


def _opaque_namespace_token(scenario_number: int, channel: int) -> str:
    payload = f"{scenario_number}:{channel}".encode()
    return blake2s(payload, digest_size=5).hexdigest().upper()


def _code_namespace_prefix_for_channel(scenario_number: int, channel: int) -> str:
    return f"P{_opaque_namespace_token(scenario_number, channel)}-"


def _ref_namespace_prefix_for_channel(scenario_number: int, channel: int) -> str:
    return f"r{_opaque_namespace_token(scenario_number, channel)}_"


def _money(value: float) -> float:
    return round(float(value), 2)


class VendorCategory(BaseModel):
    key: str
    label: str
    price_factor: tuple[float, float]
    lead_shift: tuple[int, int]
    min_qty_range: tuple[int, int]
    max_qty_range: tuple[int, int] | None
    anchor: Literal["min", "median", "max"]


class ComponentVendorCategory(BaseModel):
    key: str
    label: str
    price_factor: tuple[float, float]
    lead_range: tuple[int, int]
    min_qty_range: tuple[int, int]
    max_qty_range: tuple[int, int] | None


NAME_PREFIXES: tuple[str, ...] = (
    # -- formerly COMPANY_PREFIXES --
    "Nexus",
    "Orbital",
    "Skyline",
    "Vertex",
    "Lumen",
    "Atlas",
    "Quantum",
    "Aurora",
    "Summit",
    "Baseline",
    "Cipher",
    "Prism",
    "Ember",
    "Vantage",
    "Trellis",
    # -- formerly VENDOR_PREFIXES --
    "Trident",
    "Metro",
    "Zenith",
    "Onyx",
    "Cosmo",
    "Echo",
    "Stratos",
    "Polar",
    "Horizon",
    "Catalyst",
    "Velocity",
    "Anvil",
    "Forge",
    "Titan",
    "Nimbus",
    "Apex",
    # -- formerly _ADJACENT_VENDOR_PREFIXES --
    "Northbridge",
    "Cascade",
    "Sterling",
    "Pacific",
    "Meridian",
    "Alpine",
    "Granite",
    "Ironwood",
    "Silverline",
    "Crestview",
    "Bayshore",
    "Pinnacle",
    "Redstone",
    "Cobalt",
    "Evergreen",
    # -- formerly _ADJACENT_CUSTOMER_PREFIXES --
    "Westfield",
    "Clearwater",
    "Ironside",
    "Bridgeway",
    "Hartland",
    "Ashford",
    "Stonewall",
    "Millbrook",
    "Fairview",
    "Ridgeline",
    "Brookfield",
    "Oakmont",
    "Lakewood",
    "Riverdale",
    "Thornton",
    # -- expansion pool --
    "Aegis",
    "Aether",
    "Alloy",
    "Arbor",
    "Arc",
    "Arrow",
    "Axis",
    "Baltic",
    "Beacon",
    "Blaze",
    "Borough",
    "Bronze",
    "Canton",
    "Cardinal",
    "Cedar",
    "Citadel",
    "Chrome",
    "Circuit",
    "Comet",
    "Compass",
    "Conduit",
    "Copper",
    "Coral",
    "Crest",
    "Crown",
    "Drift",
    "Dune",
    "Eclipse",
    "Element",
    "Equinox",
    "Flint",
    "Flux",
    "Garnet",
    "Gateway",
    "Globe",
    "Grove",
    "Haven",
    "Helix",
    "Indigo",
    "Iron",
    "Ivory",
    "Jade",
    "Keystone",
    "Lance",
    "Lattice",
    "Ledger",
    "Marble",
    "Matrix",
    "Monarch",
    "Mosaic",
    "Noble",
    "Opal",
    "Osprey",
    "Oxide",
    "Peak",
    "Pivot",
    "Quartz",
    "Raven",
    "Ridge",
    "Sapphire",
    "Scion",
    "Sentinel",
    "Sierra",
    "Slate",
    "Solace",
    "Spark",
    "Spectra",
    "Spire",
    "Steel",
    "Terra",
)
CUSTOMER_SUFFIXES: tuple[str, ...] = (
    "Studios",
    "Collective",
    "Workspaces",
    "Architects",
    "Dynamics",
    "Systems",
    "Labs",
    "Ventures",
    "Group",
    "Enterprises",
    "Partners",
    "Agency",
    "Guild",
    "Foundry",
    "Alliance",
    # -- expansion pool --
    "Robotics",
    "Innovations",
    "Clinics",
    "Studios East",
    "Studios West",
    "Technologies",
    "Creative",
    "Collective East",
    "Outfitters",
    "Bureau",
    "Workshop",
    "Institute",
    "Academy",
    "Consortium",
    "Forum",
    "Reserve",
    "Atelier",
    "Boutique",
    "Trust",
    "Cooperative",
    "Publishing",
    "Supply Co",
    "Holdings",
    "Trading Co",
    "Society",
    "Fabricators",
    "Research",
    "Designs",
    "Solutions Group",
    "Advisory",
    "Council",
    "Initiative",
    "Brands",
    "Office",
    "Analytics",
    "Studios North",
    "Studios South",
    "Hub",
    "Exchange",
    "Media",
    "Pictures",
    "Interactive",
    "Engineering",
    "Sciences",
    "Productions",
    "Practice",
    "Chambers",
    "Observatory",
    "Pavilion",
    "Greenhouse",
    "Bazaar",
    "Arena",
    "Archive",
    "Museum",
    "Gallery",
    "Theater",
    "Lyceum",
    "Conservatory",
    "Refinery",
    "Manufactory",
    "Works",
)
VENDOR_SUFFIXES: tuple[str, ...] = (
    "Logistics",
    "Freight",
    "Microfactory",
    "Supply",
    "Carriers",
    "Industries",
    "Consolidators",
    "Transit",
    "Networks",
    "Solutions",
    "Depot",
    "Haulage",
    "Movers",
    "Warehouse",
    "Provisions",
    "Distribution",
    "Sourcing",
    "Procurement",
    "Fulfillment",
    "Transport",
    "Warehousing",
    "Shipping",
    "Manufacturing",
    "Fabrication",
    "Assemblies",
    "Components",
    "Materials",
    "Importers",
    "Exporters",
    "Wholesalers",
    "Distributors",
    "Operations",
    "Merchants",
    "Commodities",
    "Packagers",
    "Forwarders",
    "Conveyance",
    "Integrators",
    "Resources",
    "Partners",
    "Holdings",
)


VENDOR_CATEGORY_CONFIG: dict[str, VendorCategory] = {
    "priority": VendorCategory(
        key="priority",
        label="Priority Shuttle",
        price_factor=(0.88, 1.08),
        lead_shift=(0, 1),
        min_qty_range=(5, 14),
        max_qty_range=(8, 18),
        anchor="min",
    ),
    "express": VendorCategory(
        key="express",
        label="Express Air",
        price_factor=(1.2, 1.5),
        lead_shift=(-3, -1),
        min_qty_range=(1, 5),
        max_qty_range=(3, 8),
        anchor="min",
    ),
    "hybrid": VendorCategory(
        key="hybrid",
        label="Hybrid Expedite",
        price_factor=(1.08, 1.3),
        lead_shift=(-1, 2),
        min_qty_range=(3, 9),
        max_qty_range=(6, 12),
        anchor="median",
    ),
    "regional": VendorCategory(
        key="regional",
        label="Regional Cross-Dock",
        price_factor=(0.85, 1.08),
        lead_shift=(1, 4),
        min_qty_range=(4, 10),
        max_qty_range=(6, 14),
        anchor="median",
    ),
    "scheduled": VendorCategory(
        key="scheduled",
        label="Scheduled Freight",
        price_factor=(0.78, 0.98),
        lead_shift=(2, 6),
        min_qty_range=(2, 10),
        max_qty_range=None,
        anchor="max",
    ),
    "economy": VendorCategory(
        key="economy",
        label="Economy Ocean",
        price_factor=(0.65, 0.9),
        lead_shift=(6, 11),
        min_qty_range=(6, 14),
        max_qty_range=None,
        anchor="max",
    ),
    "microfactory": VendorCategory(
        key="microfactory",
        label="Microfactory Build-to-Order",
        price_factor=(1.15, 1.38),
        lead_shift=(-2, 0),
        min_qty_range=(2, 6),
        max_qty_range=(4, 10),
        anchor="min",
    ),
    "charter": VendorCategory(
        key="charter",
        label="Air Charter",
        price_factor=(1.35, 1.65),
        lead_shift=(-3, -2),
        min_qty_range=(1, 3),
        max_qty_range=(2, 5),
        anchor="min",
    ),
    "bulk": VendorCategory(
        key="bulk",
        label="Bulk Container",
        price_factor=(0.5, 0.7),
        lead_shift=(6, 12),
        min_qty_range=(16, 28),
        max_qty_range=None,
        anchor="max",
    ),
}


COMPONENT_VENDOR_CATEGORIES: tuple[ComponentVendorCategory, ...] = (
    ComponentVendorCategory(
        key="rapid",
        label="Rapid Works",
        price_factor=(1.15, 1.4),
        lead_range=(2, 4),
        min_qty_range=(4, 12),
        max_qty_range=(18, 32),
    ),
    ComponentVendorCategory(
        key="domestic",
        label="Domestic Supply",
        price_factor=(0.92, 1.15),
        lead_range=(4, 7),
        min_qty_range=(10, 28),
        max_qty_range=(30, 54),
    ),
    ComponentVendorCategory(
        key="hybrid",
        label="Hybrid Fabricators",
        price_factor=(1.08, 1.3),
        lead_range=(4, 7),
        min_qty_range=(8, 20),
        max_qty_range=(26, 44),
    ),
    ComponentVendorCategory(
        key="import",
        label="Import Consolidator",
        price_factor=(0.78, 1.0),
        lead_range=(8, 12),
        min_qty_range=(24, 48),
        max_qty_range=None,
    ),
)


class SamplerSettings(BaseModel):
    """Range-driven knobs that control how complex a sampled scenario can become.

    All tuple ranges use the same semantics as ``numpy.random.Generator.integers``:
    the lower bound is inclusive and the upper bound is exclusive. Setting ``(3, 4)``
    therefore forces the sampler to always pick ``3``.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    customer_count_range: tuple[int, int] = (4, 7)
    deadline_range: tuple[int, int] = (4, 13)
    demand_range: tuple[int, int] = (6, 24)
    stock_ratio_range: tuple[float, float] = (0.35, 0.6)
    finished_vendor_target_range: tuple[int, int] | None = None
    finished_vendor_limit: int | None = None
    include_components: bool = True
    component_count_range: tuple[int, int] | None = (
        3,
        6,
    )  # Random range; overrides max_component_templates
    max_component_templates: int | None = (
        None  # Legacy: fixed cap (use component_count_range instead)
    )
    component_vendor_category_count_range: tuple[int, int] = (2, 4)
    vendor_capacity_primary_ratio_range: tuple[float, float] = (0.12, 0.55)
    sampling_tightness: float = 0.5
    allow_assembly: bool = True
    objective_kind: ProcurementObjectiveKind = ProcurementObjectiveKind.min_new_spend
    assembly_capacity_buffer_range: tuple[int, int] = (3, 8)
    assembly_capacity_ratio_range: tuple[float, float] = (0.55, 0.9)
    workcenter_count_range: tuple[int, int] | None = None
    manufacturing_pattern: str | None = None
    difficulty_key: Literal["easy", "medium", "hard"] | None = None
    unsat_demand_enabled: bool = False
    unsat_full_seed_probability: float = 0.5
    unsat_seeded_ratio_range: tuple[float, float] = (0.45, 0.8)
    unsat_reject_rules: frozenset[str] = Field(
        default_factory=lambda: frozenset(
            {"budget_below_list_price", "quantity_outside_window", "lead_time_below_minimum"}
        )
    )
    # Product domain selection
    product_domain_keys: tuple[str, ...] | None = None  # None = all domains
    fixed_product_domain: str | None = None  # Force specific domain (overrides random)
    manufacturing_only_family: Literal["policy_forbidden"] | None = None
    manufacturing_only_no_buy_route: bool = False
    manufacturing_only_no_available_vendors: bool = False
    repair_kind: RepairScenarioKind | None = None
    invoice_required: bool = False
    payment_term: Literal["immediate", "net_30"] | None = None
    downpayment_required: bool = False
    downpayment_threshold_amount: float | None = None
    downpayment_mode: Literal["percentage", "fixed_amount"] | None = None
    downpayment_value: float | None = None

    @property
    def manufacturing_only_enabled(self) -> bool:
        return (
            self.manufacturing_only_family is not None
            or self.manufacturing_only_no_buy_route
            or self.manufacturing_only_no_available_vendors
        )

    @model_validator(mode="after")
    def validate_invoicing(self) -> SamplerSettings:
        validate_procurement_invoicing_policy(
            invoice_required=self.invoice_required,
            payment_term=self.payment_term,
            downpayment_required=self.downpayment_required,
            downpayment_threshold_amount=self.downpayment_threshold_amount,
            downpayment_mode=self.downpayment_mode,
            downpayment_value=self.downpayment_value,
        )
        if (
            self.objective_kind is ProcurementObjectiveKind.constraint_only
            and self.difficulty_key is not None
            and self.difficulty_key != "easy"
        ):
            raise ValueError("constraint_only objective requires difficulty_key='easy'")
        return self


class ProcurementSampler:
    def __init__(self, *, seed: int | None, settings: SamplerSettings | None = None):
        self.rng: Generator = _np.random.default_rng(seed)
        self.settings = settings or SamplerSettings()
        self._domain_keys = self._resolve_domain_keys()

    def _resolve_domain_keys(self) -> tuple[str, ...]:
        if self.settings.fixed_product_domain:
            if self.settings.fixed_product_domain not in PRODUCT_DOMAINS:
                raise ValueError(f"Unknown domain: {self.settings.fixed_product_domain}")
            return (self.settings.fixed_product_domain,)
        if self.settings.product_domain_keys:
            for key in self.settings.product_domain_keys:
                if key not in PRODUCT_DOMAINS:
                    raise ValueError(f"Unknown domain: {key}")
            return self.settings.product_domain_keys
        return DEFAULT_PRODUCT_DOMAIN_KEYS

    def _select_domain(self) -> ProductDomain:
        key = self._choice(self._domain_keys)
        return PRODUCT_DOMAINS[key]

    def _customer_name(self, used_prefixes: set[str], used_suffixes: set[str]) -> str:
        for _ in range(256):
            prefix = self._choice(NAME_PREFIXES)
            suffix = self._choice(CUSTOMER_SUFFIXES)
            if prefix not in used_prefixes and suffix not in used_suffixes:
                used_prefixes.add(prefix)
                used_suffixes.add(suffix)
                return f"{prefix} {suffix}"
        raise RuntimeError(
            "Customer name pool exhausted after 256 attempts "
            f"(used {len(used_prefixes)} prefixes, {len(used_suffixes)} suffixes; "
            f"available {len(NAME_PREFIXES)} prefixes, {len(CUSTOMER_SUFFIXES)} suffixes). "
            "Expand NAME_PREFIXES or CUSTOMER_SUFFIXES."
        )

    def _vendor_name(self, used_prefixes: set[str], used_suffixes: set[str]) -> str:
        for _ in range(256):
            prefix = self._choice(NAME_PREFIXES)
            suffix = self._choice(VENDOR_SUFFIXES)
            if prefix not in used_prefixes and suffix not in used_suffixes:
                used_prefixes.add(prefix)
                used_suffixes.add(suffix)
                return f"{prefix} {suffix}"
        raise RuntimeError(
            "Vendor name pool exhausted after 256 attempts "
            f"(used {len(used_prefixes)} prefixes, {len(used_suffixes)} suffixes; "
            f"available {len(NAME_PREFIXES)} prefixes, {len(VENDOR_SUFFIXES)} suffixes). "
            "Expand NAME_PREFIXES or VENDOR_SUFFIXES."
        )

    def _code_namespace_prefix(self, scenario_number: int) -> str:
        return _code_namespace_prefix_for_channel(scenario_number, channel=0)

    def _ref_namespace_prefix(self, scenario_number: int) -> str:
        return _ref_namespace_prefix_for_channel(scenario_number, channel=0)

    def _namespaced_code(self, scenario_number: int, base_code: str) -> str:
        return f"{self._code_namespace_prefix(scenario_number)}{base_code}"

    def _customer_ref(self, scenario_number: int, idx: int) -> str:
        return f"{self._ref_namespace_prefix(scenario_number)}c{idx + 1:02d}"

    def _vendor_ref(self, scenario_number: int, idx: int) -> str:
        return f"{self._ref_namespace_prefix(scenario_number)}q{idx:02d}"

    def _task_order_ref(self, scenario_number: int, idx: int) -> str:
        return f"{self._ref_namespace_prefix(scenario_number)}o{idx + 1:02d}"

    def _sample_unsat_acceptance_policy(
        self,
        *,
        customers: Sequence[CustomerSpec],
        enabled_rules: frozenset[str],
    ) -> UnsatAcceptancePolicy:
        qty_enabled = "quantity_outside_window" in enabled_rules
        lead_enabled = "lead_time_below_minimum" in enabled_rules
        demand_values = sorted({customer.demand for customer in customers})
        deadline_values = sorted({customer.deadline for customer in customers})
        min_qty_candidates = sorted({demand + 1 for demand in demand_values[:-1]})
        max_qty_candidates = sorted({demand - 1 for demand in demand_values[1:]})
        min_lead_candidates = sorted({deadline + 1 for deadline in deadline_values[:-1]})
        if qty_enabled and (not min_qty_candidates or not max_qty_candidates):
            raise RuntimeError(
                "Unsat demand requires enough demand spread to define a quantity-window rule"
            )
        if lead_enabled and not min_lead_candidates:
            raise RuntimeError(
                "Unsat demand requires enough deadline spread to define a lead-time rule"
            )

        for _ in range(64):
            if qty_enabled:
                min_order_quantity = int(self._choice(min_qty_candidates))
                max_candidates = [v for v in max_qty_candidates if v >= min_order_quantity]
                if not max_candidates:
                    continue
                max_order_quantity = int(self._choice(max_candidates))
            else:
                min_order_quantity = 1
                max_order_quantity = 10**9
            if lead_enabled:
                min_lead_time_days = int(self._choice(min_lead_candidates))
            else:
                min_lead_time_days = 0
            kept_candidates = [
                customer
                for customer in customers
                if min_order_quantity <= customer.demand <= max_order_quantity
                and customer.deadline >= min_lead_time_days
            ]
            if not kept_candidates:
                continue
            if qty_enabled:
                if not any(customer.demand < min_order_quantity for customer in customers):
                    continue
                if not any(customer.demand > max_order_quantity for customer in customers):
                    continue
            if lead_enabled:
                if not any(customer.deadline < min_lead_time_days for customer in customers):
                    continue
            return UnsatAcceptancePolicy(
                min_order_quantity=min_order_quantity,
                max_order_quantity=max_order_quantity,
                min_lead_time_days=min_lead_time_days,
                reject_rules=enabled_rules,
            )

        raise RuntimeError(
            "Unsat demand could not sample a global acceptance policy with both kept and rejected orders"
        )

    def _select_unsat_budget_blocker_idx(
        self,
        *,
        customers: Sequence[CustomerSpec],
        product: ProductSpec,
        acceptance_policy: UnsatAcceptancePolicy,
        seeded_ranked: Sequence[int],
    ) -> int | None:
        kept_indices = {
            idx
            for idx, customer in enumerate(customers)
            if not acceptance_policy.reject_reasons_for(customer=customer, product=product)
        }
        seeded_rejected = [idx for idx in seeded_ranked if idx not in kept_indices]
        if seeded_rejected:
            return seeded_rejected[0]
        seeded_kept = [idx for idx in seeded_ranked if idx in kept_indices]
        if seeded_kept and len(kept_indices) > 1:
            return seeded_kept[0]
        return None

    def _apply_unsat_demand_overlay(
        self,
        *,
        scenario_number: int,
        customers: Sequence[CustomerSpec],
        product: ProductSpec,
    ) -> tuple[UnsatAcceptancePolicy, list[CustomerSpec]]:
        if len(customers) < 2:
            raise RuntimeError("Unsatisfiable demand requires at least two customer requests")

        mutated = [customer.model_copy() for customer in customers]
        ranked = sorted(
            range(len(mutated)),
            key=lambda idx: (mutated[idx].demand, mutated[idx].deadline, -idx),
            reverse=True,
        )
        fully_seeded = self.rng.random() < self.settings.unsat_full_seed_probability
        if fully_seeded:
            seeded_indices = set(range(len(mutated)))
            prompt_only_indices: set[int] = set()
        else:
            seeded_ratio = self._sample_ratio_in_range(self.settings.unsat_seeded_ratio_range)
            seeded_count = int(round(len(mutated) * seeded_ratio))
            seeded_count = min(len(mutated) - 1, max(1, seeded_count))
            seeded_indices = set(ranked[-seeded_count:])
            prompt_only_indices = set(range(len(mutated))) - seeded_indices
            if not prompt_only_indices:
                seeded_indices.remove(ranked[0])
                prompt_only_indices.add(ranked[0])

        seeded_ranked = [idx for idx in reversed(ranked) if idx in seeded_indices]
        if not seeded_ranked:
            raise RuntimeError("Unsatisfiable demand requires at least one seeded task order")
        enabled_rules = self.settings.unsat_reject_rules
        budget_enabled = "budget_below_list_price" in enabled_rules
        for _ in range(64):
            acceptance_policy = self._sample_unsat_acceptance_policy(
                customers=mutated, enabled_rules=enabled_rules
            )
            budget_blocker_idx: int | None = None
            budget_ceiling: float | None = None
            if budget_enabled:
                budget_blocker_idx = self._select_unsat_budget_blocker_idx(
                    customers=mutated,
                    product=product,
                    acceptance_policy=acceptance_policy,
                    seeded_ranked=seeded_ranked,
                )
                if budget_blocker_idx is None:
                    continue
                list_revenue = product.list_price_dollars * mutated[budget_blocker_idx].demand
                budget_ceiling = max(1.0, round(list_revenue * self.rng.uniform(0.7, 0.95), 2))
            else:
                if not any(
                    acceptance_policy.reject_reasons_for(customer=mutated[idx], product=product)
                    for idx in seeded_ranked
                ):
                    continue

            overlaid: list[CustomerSpec] = []
            for idx, customer in enumerate(mutated):
                updates: dict[str, object] = {
                    "task_order_source": "seeded" if idx in seeded_indices else "prompt_only",
                    "task_order_ref": self._task_order_ref(scenario_number, idx),
                    "seeded_order_state": "draft" if idx in seeded_indices else None,
                }
                if budget_blocker_idx is not None and idx == budget_blocker_idx:
                    updates["budget_dollars"] = budget_ceiling
                updated_customer = customer.model_copy(update=updates)
                reject_reasons = acceptance_policy.reject_reasons_for(
                    customer=updated_customer,
                    product=product,
                )
                reject_reason_text = acceptance_policy.reject_reason_text_for(
                    customer=updated_customer,
                    product=product,
                )
                overlaid.append(
                    updated_customer.model_copy(
                        update={
                            "reject_reasons": reject_reasons,
                            "reject_reason_text": reject_reason_text,
                        }
                    )
                )
            if any(not customer.reject_reasons for customer in overlaid):
                return acceptance_policy, overlaid

        raise RuntimeError(
            "Unsat demand could not preserve an accepted order after applying the seeded budget blocker"
        )

    def _validate_namespace_isolation(
        self, *, scenario_number: int, blueprint: ScenarioBlueprint
    ) -> None:
        expected_code_prefix = self._code_namespace_prefix(scenario_number)
        expected_ref_prefix = self._ref_namespace_prefix(scenario_number)

        product_codes = [blueprint.product.code]
        for manufacturing_product in blueprint.manufactured_products:
            product_codes.append(manufacturing_product.code)
            for component in manufacturing_product.components:
                product_codes.append(component.product.code)
        unique_product_codes = set(product_codes)
        if any(not code.startswith(expected_code_prefix) for code in unique_product_codes):
            raise ValueError(
                f"Product code namespace violation: expected prefix {expected_code_prefix}"
            )

        refs = [customer.ref for customer in blueprint.customers] + [
            vendor.ref for vendor in blueprint.vendors
        ]
        if len(refs) != len(set(refs)):
            raise ValueError("Duplicate partner refs detected in sampled scenario")
        if any(not ref.startswith(expected_ref_prefix) for ref in refs):
            raise ValueError(
                f"Partner ref namespace violation: expected prefix {expected_ref_prefix}"
            )

        allowed_codes = set(product_codes)
        if any(vendor.product_code not in allowed_codes for vendor in blueprint.vendors):
            raise ValueError("Vendor product_code points outside scenario product namespace")

    def build_blueprint(self, scenario_number: int) -> ScenarioBlueprint:
        last_error: RuntimeError | None = None
        failure_counts: dict[str, int] = {}
        for attempt in range(MAX_RESAMPLE_ATTEMPTS):
            try:
                blueprint = self._sample_once(scenario_number)
                return blueprint
            except RuntimeError as exc:
                last_error = exc
                failure_key = self._sampling_failure_reason(exc)
                failure_counts[failure_key] = failure_counts.get(failure_key, 0) + 1
                if self._should_log_sampling_attempt(attempt):
                    logger.info(
                        "Resampling scenario=%s attempt=%s/%s reason=%s key=%s",
                        scenario_number,
                        attempt + 1,
                        MAX_RESAMPLE_ATTEMPTS,
                        exc,
                        failure_key,
                    )
        if last_error is not None:
            if failure_counts:
                logger.warning(
                    "Sampling failure summary scenario=%s %s",
                    scenario_number,
                    ", ".join(f"{key}:{count}" for key, count in sorted(failure_counts.items())),
                )
            raise RuntimeError(
                f"Failed to sample feasible procurement blueprint {scenario_number} "
                f"after {MAX_RESAMPLE_ATTEMPTS} attempts"
            ) from last_error
        raise RuntimeError(f"Failed to sample procurement blueprint {scenario_number}")

    def _should_log_sampling_attempt(self, attempt: int) -> bool:
        checkpoints = {0, 4, 9, 19, 39, 59, 79, 99}
        return attempt in checkpoints

    def _invoicing_policy(self) -> ProcurementInvoicingPolicy:
        return ProcurementInvoicingPolicy(
            invoice_required=self.settings.invoice_required,
            payment_term=self.settings.payment_term,
            downpayment_required=self.settings.downpayment_required,
            downpayment_threshold_amount=self.settings.downpayment_threshold_amount,
            downpayment_mode=self.settings.downpayment_mode,
            downpayment_value=self.settings.downpayment_value,
        )

    @staticmethod
    def _retained_customers(customers: Sequence[CustomerSpec]) -> list[CustomerSpec]:
        return [customer for customer in customers if not customer.is_rejected_upfront]

    @staticmethod
    def _customer_order_amount_untaxed(*, customer: CustomerSpec, product: ProductSpec) -> float:
        return round(product.list_price_dollars * customer.demand, 2)

    def _validate_invoicing_shape(
        self,
        *,
        customers: Sequence[CustomerSpec],
        product: ProductSpec,
        invoicing_policy: ProcurementInvoicingPolicy,
    ) -> None:
        if not invoicing_policy.downpayment_required:
            return
        retained_customers = self._retained_customers(customers)
        if not retained_customers:
            raise RuntimeError("Downpayment scenarios require at least one retained order")
        threshold_hits: list[tuple[CustomerSpec, float]] = []
        threshold_misses: list[tuple[CustomerSpec, float]] = []
        for customer in retained_customers:
            amount = self._customer_order_amount_untaxed(customer=customer, product=product)
            if invoicing_policy.threshold_applies(amount):
                threshold_hits.append((customer, amount))
            else:
                threshold_misses.append((customer, amount))
        if not threshold_hits or not threshold_misses:
            raise RuntimeError(
                "Downpayment scenarios require retained orders on both sides of the threshold"
            )
        if (
            invoicing_policy.downpayment_mode == "fixed_amount"
            and invoicing_policy.downpayment_value is not None
        ):
            fixed_amount = float(invoicing_policy.downpayment_value)
            if any(amount + 0.01 < fixed_amount for _, amount in threshold_hits):
                raise RuntimeError(
                    "Fixed-amount downpayment exceeds at least one threshold-applicable retained order"
                )
        for _customer, amount in threshold_hits:
            invoicing_policy.regular_invoice_amount_for(amount)

    def _sample_once(self, scenario_number: int) -> ScenarioBlueprint:
        domain = self._select_domain()
        margin = round(self.rng.uniform(0.24, 0.3), 3)
        customer_count = self._randint(
            self.settings.customer_count_range[0], self.settings.customer_count_range[1]
        )
        deadlines = sorted(
            self._randints(
                self.settings.deadline_range[0],
                self.settings.deadline_range[1],
                customer_count,
            )
        )
        demands = self._sample_customer_demands(customer_count=customer_count, deadlines=deadlines)
        product = domain.build_product(self.rng)
        product.code = self._namespaced_code(scenario_number, product.code)

        used_prefixes: set[str] = set()
        used_cust_suffixes: set[str] = set()
        used_vendor_suffixes: set[str] = set()
        customers: list[CustomerSpec] = []
        total_demand = sum(demands)
        prices: list[float] = []
        budgets: list[float] = []

        for idx in range(customer_count):
            cust_name = self._customer_name(used_prefixes, used_cust_suffixes)
            demand = demands[idx]
            deadline = deadlines[idx]
            price = round(product.list_price_dollars + self.rng.normal(0, 12), 2)
            price = max(
                product.list_price_dollars - 25.0,
                min(price, product.list_price_dollars + 45.0),
            )
            prices.append(price)
            list_revenue = product.list_price_dollars * demand
            budget_multiplier = self.rng.uniform(1.02, 1.12)
            budget = round(list_revenue * budget_multiplier, 2)
            budgets.append(budget)

            customer = CustomerSpec(
                name=cust_name,
                ref=self._customer_ref(scenario_number, idx),
                email=f"orders@{cust_name.lower().replace(' ', '')}.io",
                demand=demand,
                deadline=deadline,
                budget_dollars=budget,
            )
            customers.append(customer)

        deadline_buckets = self._deadline_buckets(customers)

        pinned_force_pattern: str | None = None
        if self.settings.difficulty_key == "hard" and self.settings.manufacturing_pattern in {
            "capability_restricted",
            "split_by_capacity",
        }:
            pinned_force_pattern = self.settings.manufacturing_pattern
        force_restricted = pinned_force_pattern == "capability_restricted"
        if pinned_force_pattern is not None:
            stock_ratio_range = (0.20, 0.25) if force_restricted else (0.25, 0.30)
            finished_aggregate_cap_ratio: float | None = 0.22 if force_restricted else 0.30
            exclude_bulk_vendor = True
        else:
            stock_ratio_range = self.settings.stock_ratio_range
            finished_aggregate_cap_ratio = None
            exclude_bulk_vendor = False

        stock_units = max(
            1,
            math.floor(total_demand * self._sample_ratio_in_range(stock_ratio_range)),
        )
        stock_cost = round(product.standard_price_dollars + self.rng.normal(0, 6), 2)

        # Keep generated procurement anchors below the spend-backed margin bound implied
        # by sampled customer prices. Stock remains a reasonable accounting cost, but it
        # does not participate in the hard feasibility rule.
        cost_ceiling = round(min(price * (1 - margin) for price in prices), 2)
        margin_safe_ceiling = max(1.0, round(cost_ceiling * 0.98, 2))
        if margin_safe_ceiling <= 1.0:
            raise RuntimeError(
                f"Margin-feasible cost ceiling too low for scenario {scenario_number}: {margin_safe_ceiling}"
            )

        stock_floor = max(1.0, round(margin_safe_ceiling * 0.55, 2))
        stock_cost = max(stock_floor, min(stock_cost, margin_safe_ceiling))
        anchor_low = max(1.0, round(margin_safe_ceiling * 0.78, 2))
        anchor_high = max(anchor_low, round(margin_safe_ceiling * 0.95, 2))
        procurement_anchor = round(self.rng.uniform(anchor_low, anchor_high), 2)
        logger.debug(
            "New-spend margin bounds scenario=%s margin=%.3f min_price=%.2f ceiling=%.2f stock=%.2f anchor=%.2f",
            scenario_number,
            margin,
            min(prices),
            margin_safe_ceiling,
            stock_cost,
            procurement_anchor,
        )
        product.standard_price_dollars = round(stock_cost, 2)
        manufacturing_only = self._manufacturing_only_config()
        if self.settings.manufacturing_only_no_available_vendors:
            finished_vendors = []
            next_vendor_idx = 1
        else:
            finished_vendors, next_vendor_idx = self._build_finished_vendors(
                product=product,
                scenario_number=scenario_number,
                deadlines=deadlines,
                procurement_anchor=procurement_anchor,
                total_demand=total_demand,
                deadline_buckets=deadline_buckets,
                stock_units=stock_units,
                start_index=1,
                used_prefixes=used_prefixes,
                used_suffixes=used_vendor_suffixes,
                exclude_bulk=exclude_bulk_vendor,
                aggregate_cap_ratio=finished_aggregate_cap_ratio,
            )

        components, component_stock = self._build_components(
            domain=domain,
            scenario_number=scenario_number,
        )

        manufacturing_enabled = self.settings.allow_assembly or manufacturing_only.enabled
        manufacturing_pattern = (
            self._select_manufacturing_pattern() if manufacturing_enabled else None
        )

        if manufacturing_pattern and self.settings.allow_assembly:
            for component in components:
                component_stock[component.product.code] = max(
                    component_stock.get(component.product.code, 0),
                    max(component.per_unit * 2, 4),
                )

        if (
            manufacturing_pattern
            in {
                "capability_restricted",
                "multilevel_basic",
                "multilevel_capability",
                "multilevel_shared_capacity",
                "multilevel_multi_branch",
                "multilevel_deep_mixed",
                "multilevel_shared_leaf",
            }
            and pinned_force_pattern is None
        ):
            if self.settings.difficulty_key == "medium":
                stock_units = max(stock_units, math.ceil(total_demand * 0.52))
            elif self.settings.difficulty_key == "hard":
                stock_units = max(stock_units, math.ceil(total_demand * 0.4))

        direct_components, manufacturing_products, workcenters = self._build_manufacturing_graph(
            scenario_number=scenario_number,
            product=product,
            components=components,
            pattern=manufacturing_pattern,
            total_demand=total_demand,
            stock_units=stock_units,
            deadlines=deadlines,
            manufacturing_only=manufacturing_only,
        )
        if not manufacturing_products:
            direct_components = ()
            components = []
            component_stock = {}
            component_vendors: list[VendorSpec] = []
        else:
            used_codes = {product.code}
            for component in direct_components:
                used_codes.add(component.product.code)
            for manufacturing_product in manufacturing_products:
                used_codes.add(manufacturing_product.code)
                for component in manufacturing_product.components:
                    used_codes.add(component.product.code)
            component_stock = {
                code: qty for code, qty in component_stock.items() if code in used_codes
            }
            components = [
                component for component in components if component.product.code in used_codes
            ]
            per_unit_by_code: dict[str, int] = {}
            for mp in manufacturing_products:
                for comp in mp.components:
                    per_unit_by_code.setdefault(comp.product.code, comp.per_unit)
            component_vendors, next_vendor_idx = self._build_component_vendors(
                scenario_number=scenario_number,
                manufacturing_products=manufacturing_products,
                final_product_code=product.code,
                deadlines=deadlines,
                deadline_buckets=deadline_buckets,
                total_demand=total_demand,
                per_unit_by_code=per_unit_by_code,
                component_stock=component_stock,
                start_index=next_vendor_idx,
                used_prefixes=used_prefixes,
                used_suffixes=used_vendor_suffixes,
            )
        component_vendor_codes = {vendor.product_code for vendor in component_vendors}
        for component in components:
            code = component.product.code
            if code in component_vendor_codes:
                continue
            missing_vendor = VendorSpec(
                name=self._vendor_name(used_prefixes, used_vendor_suffixes),
                ref=self._vendor_ref(scenario_number, next_vendor_idx),
                supplier_rank=next_vendor_idx,
                product_code=code,
                price_dollars=_money(component.product.standard_price_dollars * 1.05),
                lead_time=1,
                min_qty=1,
                max_qty=max(total_demand * max(component.per_unit, 1), 1),
                description="Fallback component source (1d)",
                supply_role="component",
            )
            component_vendors.append(missing_vendor)
            component_vendor_codes.add(code)
            next_vendor_idx += 1

        unsat_demand = self.settings.unsat_demand_enabled
        acceptance_policy: UnsatAcceptancePolicy | None = None
        if unsat_demand:
            acceptance_policy, customers = self._apply_unsat_demand_overlay(
                scenario_number=scenario_number,
                customers=customers,
                product=product,
            )
        invoicing_policy = self._invoicing_policy()
        self._validate_invoicing_shape(
            customers=customers,
            product=product,
            invoicing_policy=invoicing_policy,
        )

        vendors_by_component_code: dict[str, list[VendorSpec]] = {}
        for vendor in component_vendors:
            vendors_by_component_code.setdefault(vendor.product_code, []).append(vendor)

        vendors = finished_vendors + component_vendors
        for component in components:
            component_options = vendors_by_component_code.get(component.product.code, ())
            if component_options:
                component.product.standard_price_dollars = round(
                    min(v.price_dollars for v in component_options), 2
                )

        product_stock_units = {product.code: stock_units, **component_stock}
        product_stock_costs = {product.code: round(product.standard_price_dollars, 2)}
        for component in components:
            component_code = component.product.code
            if component_code in component_stock:
                product_stock_costs[component_code] = round(
                    component.product.standard_price_dollars, 2
                )

        provisional_blueprint = ScenarioBlueprint(
            scenario_number=scenario_number,
            name=product.name,
            instruction="",
            background_and_policy="",
            objective_kind=self.settings.objective_kind,
            margin=margin,
            customers=tuple(customers),
            vendors=tuple(vendors),
            product=product,
            manufactured_products=manufacturing_products,
            workcenters=workcenters,
            unsat_demand=unsat_demand,
            unsat_acceptance_policy=acceptance_policy,
            system_parameters=(
                SystemParameterData(
                    key="erp_bench.min_margin_percent",
                    value=format_margin_percent(margin),
                ),
            ),
            manufacturing_only=manufacturing_only,
            invoicing_policy=invoicing_policy,
            product_stock_units=product_stock_units,
            product_stock_cost_dollars=product_stock_costs,
        )

        self._enforce_capacity_solvability(
            scenario_number=scenario_number,
            customers=customers,
            stock_units=stock_units,
            finished_vendors=[] if manufacturing_only.enabled else finished_vendors,
            blueprint=provisional_blueprint,
            component_vendors=component_vendors,
            component_stock=component_stock,
        )

        blueprint = provisional_blueprint.model_copy(
            update={
                "instruction": self._instruction(provisional_blueprint),
                "background_and_policy": self._background(provisional_blueprint, vendors),
            }
        )

        self._validate_namespace_isolation(
            scenario_number=scenario_number,
            blueprint=blueprint,
        )

        return blueprint

    def _deadline_buckets(
        self,
        customers: Sequence[CustomerSpec],
    ) -> list[tuple[int, int]]:
        buckets: dict[int, int] = {}
        for customer in customers:
            buckets[customer.deadline] = buckets.get(customer.deadline, 0) + customer.demand
        running = 0
        ordered: list[tuple[int, int]] = []
        for deadline in sorted(buckets):
            running += buckets[deadline]
            ordered.append((deadline, running))
        return ordered

    def _shape_demands_for_deadlines(
        self, *, deadlines: Sequence[int], demands: list[int]
    ) -> list[int]:
        if len(demands) <= 2 or len(deadlines) != len(demands):
            return demands
        order = sorted(range(len(deadlines)), key=lambda idx: (deadlines[idx], idx))
        shaped = [int(v) for v in demands]
        for pos, idx in enumerate(order):
            swap_window = min(len(order) - 1, pos + 1)
            if swap_window <= pos or self.rng.random() >= 0.16:
                continue
            jdx = order[self._randint(pos, swap_window + 1)]
            shaped[idx], shaped[jdx] = shaped[jdx], shaped[idx]
        return shaped

    def _sample_customer_demands(
        self, *, customer_count: int, deadlines: Sequence[int]
    ) -> list[int]:
        low, high = self.settings.demand_range
        if customer_count <= 0:
            return []
        if low >= high:
            return [max(low, 1)] * customer_count
        mean_ratio = self._sample_ratio_in_range((0.0, 1.0))
        mean_demand = low + int(round((high - low) * mean_ratio))
        min_total = customer_count * low
        max_total = customer_count * high
        target_total = min(max_total, max(min_total, customer_count * mean_demand))
        capacities = [max(0, high - low) for _ in range(customer_count)]
        extra_total = target_total - min_total
        if extra_total <= 0 or not any(capacities):
            return [low for _ in range(customer_count)]
        rank_weights = [1.0 for _ in range(customer_count)]
        if deadlines and len(deadlines) == customer_count:
            order = sorted(range(customer_count), key=lambda idx: (deadlines[idx], idx))
            denom = max(customer_count - 1, 1)
            for pos, idx in enumerate(order):
                rank_weights[idx] = 0.7 + (0.6 * (pos / denom))
        alpha_scale = self._dirichlet_alpha_scale()
        alpha = [max(1e-3, alpha_scale * weight) for weight in rank_weights]
        extras = self._bounded_dirichlet_split(
            total=extra_total,
            capacities=capacities,
            alpha=alpha,
            fail_reason="Sampler demand split infeasible",
        )
        return self._shape_demands_for_deadlines(
            deadlines=deadlines,
            demands=[low + extra for extra in extras],
        )

    def _vendor_capacity(self, vendor: VendorSpec) -> int:
        if vendor.max_qty is None:
            return 10**9
        return max(int(vendor.max_qty), 0)

    def _refresh_finished_vendor_description(self, vendor: VendorSpec) -> None:
        if vendor.supply_role != "finished":
            return
        if vendor.description and " lane (" in vendor.description:
            lane_label = vendor.description.split(" lane (", 1)[0].strip()
        else:
            lane_label = (vendor.description or vendor.name or "Supplier").strip()
        if not lane_label:
            lane_label = "Supplier"
        vendor.description = self._offer_description(
            lane_label,
            int(vendor.lead_time),
            int(vendor.min_qty),
            vendor.max_qty,
        )

    def _leaf_requirements_for_product(
        self,
        blueprint: ScenarioBlueprint,
        product_code: str,
        cache: dict[str, dict[str, int]] | None = None,
    ) -> dict[str, int]:
        memo = cache if cache is not None else {}
        if product_code in memo:
            return memo[product_code]
        manufacturing_product = blueprint.manufacturing_by_code.get(product_code)
        if manufacturing_product is None:
            memo[product_code] = {product_code: 1}
            return memo[product_code]
        requirements: dict[str, int] = {}
        for component in manufacturing_product.components:
            child_requirements = self._leaf_requirements_for_product(
                blueprint,
                component.product.code,
                memo,
            )
            for code, qty in child_requirements.items():
                requirements[code] = requirements.get(code, 0) + (component.per_unit * qty)
        memo[product_code] = requirements
        return requirements

    def _leaf_build_lead_before_product_start(
        self,
        blueprint: ScenarioBlueprint,
        product_code: str,
        cache: dict[str, dict[str, int]] | None = None,
    ) -> dict[str, int]:
        memo = cache if cache is not None else {}
        if product_code in memo:
            return memo[product_code]
        manufacturing_product = blueprint.manufacturing_by_code.get(product_code)
        if manufacturing_product is None:
            memo[product_code] = {product_code: 0}
            return memo[product_code]
        leaf_build_leads: dict[str, int] = {}
        for component in manufacturing_product.components:
            child_leads = self._leaf_build_lead_before_product_start(
                blueprint,
                component.product.code,
                memo,
            )
            component_product = blueprint.manufacturing_by_code.get(component.product.code)
            component_lead = component_product.min_lead_days if component_product is not None else 0
            for code, lead_days in child_leads.items():
                total_lead = component_lead + lead_days
                current = leaf_build_leads.get(code)
                if current is None or total_lead < current:
                    leaf_build_leads[code] = total_lead
        memo[product_code] = leaf_build_leads
        return leaf_build_leads

    def _manufacturing_cap_by_deadline(
        self,
        *,
        deadline: int,
        blueprint: ScenarioBlueprint,
        component_vendors: Sequence[VendorSpec],
        component_stock: dict[str, int],
    ) -> int:
        final_product = blueprint.manufacturing_by_code.get(blueprint.product.code)
        if final_product is None:
            return 0
        final_choices = [
            choice
            for choice in final_product.workcenter_choices
            if choice.lead_time_days <= deadline
        ]
        if not final_choices:
            return 0
        leaf_requirements = self._leaf_requirements_for_product(blueprint, blueprint.product.code)
        if not leaf_requirements:
            workcenter_cap = 0
            for choice in final_choices:
                pool = blueprint.workcenters_by_code.get(choice.workcenter_code)
                if pool is None:
                    continue
                workcenter_cap += int(
                    pool.capacity_minutes // max(choice.time_per_unit_minutes, 1.0)
                )
            return workcenter_cap
        leaf_build_leads = self._leaf_build_lead_before_product_start(
            blueprint,
            blueprint.product.code,
        )
        vendors_by_code: dict[str, list[VendorSpec]] = defaultdict(list)
        for vendor in component_vendors:
            vendors_by_code[vendor.product_code].append(vendor)
        consumed_leaf_units = dict.fromkeys(leaf_requirements, 0)
        manufacturing_cap = 0
        sorted_choices = sorted(
            final_choices, key=lambda choice: choice.lead_time_days, reverse=True
        )
        for code, _per_unit in leaf_requirements.items():
            if code not in leaf_build_leads:
                raise RuntimeError(f"Missing leaf build lead for component {code}")
        for choice in sorted_choices:
            pool = blueprint.workcenters_by_code.get(choice.workcenter_code)
            if pool is None:
                continue
            choice_cap = int(pool.capacity_minutes // max(choice.time_per_unit_minutes, 1.0))
            if choice_cap <= 0:
                continue
            component_caps = []
            for code, per_unit in leaf_requirements.items():
                component_arrival_cutoff = deadline - choice.lead_time_days - leaf_build_leads[code]
                available_units = 0
                if component_arrival_cutoff >= 0:
                    available_units = int(component_stock.get(code, 0))
                    for vendor in vendors_by_code.get(code, ()):
                        if vendor.lead_time <= component_arrival_cutoff:
                            available_units += self._vendor_capacity(vendor)
                remaining_units = max(available_units - consumed_leaf_units[code], 0)
                component_caps.append(remaining_units // max(int(per_unit), 1))
            build_units = min(choice_cap, min(component_caps) if component_caps else choice_cap)
            manufacturing_cap += build_units
            for code, per_unit in leaf_requirements.items():
                consumed_leaf_units[code] += build_units * max(int(per_unit), 1)
        return manufacturing_cap

    def _supply_cap_by_deadline(
        self,
        *,
        deadline: int,
        stock_units: int,
        finished_vendors: Sequence[VendorSpec],
        blueprint: ScenarioBlueprint,
        component_vendors: Sequence[VendorSpec],
        component_stock: dict[str, int],
    ) -> int:
        stock_cap, finished_cap, manufacturing_cap = self._supply_cap_breakdown_by_deadline(
            deadline=deadline,
            stock_units=stock_units,
            finished_vendors=finished_vendors,
            blueprint=blueprint,
            component_vendors=component_vendors,
            component_stock=component_stock,
        )
        return stock_cap + finished_cap + manufacturing_cap

    def _supply_cap_breakdown_by_deadline(
        self,
        *,
        deadline: int,
        stock_units: int,
        finished_vendors: Sequence[VendorSpec],
        blueprint: ScenarioBlueprint,
        component_vendors: Sequence[VendorSpec],
        component_stock: dict[str, int],
    ) -> tuple[int, int, int]:
        finished_cap = sum(
            self._vendor_capacity(vendor)
            for vendor in finished_vendors
            if vendor.lead_time <= deadline
        )
        manufacturing_cap = self._manufacturing_cap_by_deadline(
            deadline=deadline,
            blueprint=blueprint,
            component_vendors=component_vendors,
            component_stock=component_stock,
        )
        return int(stock_units), finished_cap, manufacturing_cap

    def _assign_correlated_vendor_caps(
        self,
        *,
        vendors: list[VendorSpec],
        required_qty: int,
        deadline_buckets: Sequence[tuple[int, int]] = (),
        base_deadline_supply: int = 0,
        aggregate_cap_override: float | None = None,
    ) -> None:
        if not vendors:
            return

        required_qty = max(int(required_qty), 1)
        prices = [float(v.price_dollars) for v in vendors]
        leads = [int(v.lead_time) for v in vendors]
        min_price, max_price = min(prices), max(prices)
        min_lead, max_lead = min(leads), max(leads)

        def _norm(value: float, low: float, high: float) -> float:
            span = high - low
            if span <= 1e-9:
                return 0.0
            return (value - low) / span

        scored = [
            (
                idx,
                (0.65 * _norm(v.price_dollars, min_price, max_price))
                + (0.35 * _norm(v.lead_time, min_lead, max_lead)),
            )
            for idx, v in enumerate(vendors)
        ]
        ranked = [idx for idx, _ in sorted(scored, key=lambda item: (item[1], item[0]))]
        rank_pos = {idx: pos for pos, idx in enumerate(ranked)}
        denom = max(len(vendors) - 1, 1)
        reach_weights = self._deadline_reach_weights(vendors, deadline_buckets, required_qty)
        tight_deadline_ratio = 0.0
        earliest_deadline: int | None = None
        if deadline_buckets:
            earliest_deadline, earliest_cumulative_demand = deadline_buckets[0]
            earliest_target = max(
                int(earliest_cumulative_demand) - max(int(base_deadline_supply), 0),
                0,
            )
            tight_deadline_ratio = min(1.0, earliest_target / required_qty)
        min_caps = [max(0, int(vendor.min_qty)) for vendor in vendors]
        max_caps: list[int] = []
        for idx, _vendor in enumerate(vendors):
            cap = max(min_caps[idx], required_qty - 1) if required_qty > 1 else min_caps[idx]
            max_caps.append(cap)
        floor_total = sum(min_caps)
        ceiling_total = sum(max_caps)
        if floor_total > ceiling_total:
            raise RuntimeError("Sampler vendor floor exceeds cap ceiling")
        ratio = self._sample_ratio_in_range(self.settings.vendor_capacity_primary_ratio_range)
        ratio = min(1.0, ratio + (0.12 * tight_deadline_ratio))
        if aggregate_cap_override is not None:
            aggregate_ratio = aggregate_cap_override
        else:
            # Interpret vendor ratio as per-vendor pressure and scale by vendor count.
            aggregate_ratio = ratio * max(1.0, float(len(vendors)))
        target_total = int(math.ceil(required_qty * aggregate_ratio))
        target_total = min(ceiling_total, max(floor_total, target_total))
        extra_total = target_total - floor_total
        max_extra = [
            max_cap - min_cap for max_cap, min_cap in zip(max_caps, min_caps, strict=False)
        ]
        alpha_scale = self._dirichlet_alpha_scale()
        alpha: list[float] = []
        for idx, vendor in enumerate(vendors):
            rank_priority = 1.0 - (rank_pos[idx] / denom)
            weight = 0.2 + (0.5 * reach_weights[idx]) + (0.3 * rank_priority)
            if earliest_deadline is not None and vendor.lead_time <= earliest_deadline:
                weight += 0.35 * tight_deadline_ratio
            alpha.append(max(1e-3, alpha_scale * weight))
        extras = self._bounded_dirichlet_split(
            total=extra_total,
            capacities=max_extra,
            alpha=alpha,
            fail_reason="Sampler vendor cap split infeasible",
        )
        for idx, vendor in enumerate(vendors):
            vendor.max_qty = min_caps[idx] + extras[idx]

    def _deadline_reach_weights(
        self,
        vendors: Sequence[VendorSpec],
        deadline_buckets: Sequence[tuple[int, int]],
        required_qty: int,
    ) -> list[float]:
        if not vendors:
            return []
        if not deadline_buckets or required_qty <= 0:
            return [1.0 for _ in vendors]
        incremental: list[tuple[int, int]] = []
        prev = 0
        for deadline, cumulative_demand in deadline_buckets:
            current_cumulative = max(int(cumulative_demand), prev)
            incremental.append((int(deadline), current_cumulative - prev))
            prev = current_cumulative
        if not incremental:
            return [1.0 for _ in vendors]
        weights: list[float] = []
        for vendor in vendors:
            reachable_demand = sum(
                demand for deadline, demand in incremental if vendor.lead_time <= deadline
            )
            weights.append(min(1.0, max(0.0, reachable_demand / required_qty)))
        return weights

    def _sampling_failure_reason(self, err: RuntimeError) -> str:
        msg = str(err).lower()
        if "at deadline" in msg:
            return "deadline_cap"
        if "total capacity infeasible" in msg:
            return "total_cap"
        if "margin-feasible cost ceiling" in msg:
            return "margin_ceiling"
        if "no fulfillable supply sources" in msg:
            return "no_supply"
        return "other"

    def _enforce_capacity_solvability(
        self,
        *,
        scenario_number: int | None = None,
        customers: Sequence[CustomerSpec],
        stock_units: int,
        finished_vendors: list[VendorSpec],
        blueprint: ScenarioBlueprint,
        component_vendors: Sequence[VendorSpec],
        component_stock: dict[str, int],
    ) -> None:
        """Validate capacity feasibility and defer recovery to caller resampling."""
        if not customers:
            return
        if (
            not finished_vendors
            and blueprint.manufacturing_by_code.get(blueprint.product.code) is None
        ):
            raise RuntimeError("Sampler generated no fulfillable supply sources")

        buckets = self._deadline_buckets(customers)
        total_demand = sum(customer.demand for customer in customers)

        for deadline, cumulative_demand in buckets:
            stock_cap, finished_cap, manufacturing_cap = self._supply_cap_breakdown_by_deadline(
                deadline=deadline,
                stock_units=stock_units,
                finished_vendors=finished_vendors,
                blueprint=blueprint,
                component_vendors=component_vendors,
                component_stock=component_stock,
            )
            current_cap = stock_cap + finished_cap + manufacturing_cap
            deficit = cumulative_demand - current_cap
            if deficit > 0:
                logger.info(
                    "Capacity reject scenario=%s deadline=%s demand=%s cap=%s stock=%s finished=%s manufacturing=%s deficit=%s",
                    scenario_number if scenario_number is not None else "unknown",
                    deadline,
                    cumulative_demand,
                    current_cap,
                    stock_cap,
                    finished_cap,
                    manufacturing_cap,
                    deficit,
                )
                raise RuntimeError(
                    f"Sampler capacity infeasible at deadline {deadline}: "
                    f"demand={cumulative_demand} cap={current_cap}"
                )

        final_cap = self._supply_cap_by_deadline(
            deadline=max(deadline for deadline, _ in buckets),
            stock_units=stock_units,
            finished_vendors=finished_vendors,
            blueprint=blueprint,
            component_vendors=component_vendors,
            component_stock=component_stock,
        )
        if final_cap < total_demand:
            raise RuntimeError(
                f"Sampler total capacity infeasible: demand={total_demand} cap={final_cap}"
            )

    def _build_finished_vendors(
        self,
        *,
        product: ProductSpec,
        scenario_number: int,
        deadlines: list[int],
        procurement_anchor: float,
        total_demand: int,
        deadline_buckets: Sequence[tuple[int, int]],
        stock_units: int,
        start_index: int,
        used_prefixes: set[str],
        used_suffixes: set[str],
        exclude_bulk: bool = False,
        aggregate_cap_ratio: float | None = None,
    ) -> tuple[list[VendorSpec], int]:
        base_keys: list[str] = ["priority", "express"]
        optional_keys = [
            key for key in VENDOR_CATEGORY_CONFIG.keys() if key not in {"priority", "express"}
        ]
        max_vendors = min(len(VENDOR_CATEGORY_CONFIG), 9)
        if self.settings.finished_vendor_limit is not None:
            max_vendors = min(max_vendors, self.settings.finished_vendor_limit)

        if self.settings.finished_vendor_target_range is None:
            target_low, target_high = 6, max_vendors + 1
        else:
            target_low, target_high = self.settings.finished_vendor_target_range
        target_high = min(target_high, max_vendors + 1)
        target_low = min(target_low, target_high)
        target_count = self._randint(target_low, target_high)
        additional_needed = max(0, target_count - len(base_keys))
        bulk_eligible = total_demand >= 30 and not exclude_bulk
        if bulk_eligible and "bulk" not in base_keys:
            base_keys.append("bulk")
        selected_optional: list[str] = []
        if additional_needed:
            population = [key for key in optional_keys if key != "bulk" or bulk_eligible]
            size = min(additional_needed, len(population))
            selected_optional = self._sample_optional(population, size)
        category_keys = base_keys + selected_optional
        category_keys = category_keys[:max_vendors]

        vendors: list[VendorSpec] = []

        for offset, key in enumerate(category_keys):
            category = VENDOR_CATEGORY_CONFIG[key]
            lead_anchor = self._lead_anchor(category.anchor, deadlines)
            shift = self._randint(category.lead_shift[0], category.lead_shift[1] + 1)
            lead_time = max(1, lead_anchor + shift)
            price_factor = self.rng.uniform(*category.price_factor)
            price = round(procurement_anchor * price_factor, 2)

            min_qty = self._randint(category.min_qty_range[0], category.min_qty_range[1] + 1)
            if key == "bulk":
                min_qty = max(min_qty, total_demand // 2)
            max_qty: int | None
            if category.max_qty_range is None:
                max_qty = None
            else:
                max_low = max(min_qty, category.max_qty_range[0])
                max_high = category.max_qty_range[1] + 1
                if max_low >= max_high:
                    max_qty = max_low
                else:
                    max_qty = self._randint(max_low, max_high)

            name = self._vendor_name(used_prefixes, used_suffixes)
            supplier_idx = start_index + offset
            vendor = VendorSpec(
                name=name,
                ref=self._vendor_ref(scenario_number, supplier_idx),
                supplier_rank=supplier_idx,
                product_code=product.code,
                price_dollars=price,
                lead_time=lead_time,
                min_qty=min_qty,
                max_qty=max_qty,
                description=self._offer_description(category.label, lead_time, min_qty, max_qty),
                supply_role="finished",
            )
            vendors.append(vendor)

        if self.settings.difficulty_key == "hard" and len(vendors) > 1:
            ordered = sorted(range(len(vendors)), key=lambda idx: vendors[idx].price_dollars)
            span = max(len(ordered) - 1, 1)
            for rank, idx in enumerate(ordered):
                stretch = 1.0 + (((rank / span) - 0.5) * 0.18)
                vendors[idx].price_dollars = _money(max(1.0, vendors[idx].price_dollars * stretch))

        self._assign_correlated_vendor_caps(
            vendors=vendors,
            required_qty=total_demand,
            deadline_buckets=deadline_buckets,
            base_deadline_supply=max(int(stock_units), 0),
            aggregate_cap_override=aggregate_cap_ratio,
        )
        for vendor in vendors:
            self._refresh_finished_vendor_description(vendor)

        return vendors, start_index + len(vendors)

    def _build_components(
        self,
        *,
        domain: ProductDomain,
        scenario_number: int,
    ) -> tuple[list[ComponentSpec], dict[str, int]]:
        if not self.settings.include_components:
            return [], {}

        all_templates = list(domain.component_templates)
        if self.settings.component_count_range is not None:
            min_ct, max_ct = self.settings.component_count_range
            max_ct = min(max_ct, len(all_templates))
            min_ct = min(min_ct, max_ct)
            count = self._randint(min_ct, max_ct + 1)
            indices = list(range(len(all_templates)))
            self.rng.shuffle(indices)
            templates: Sequence[ComponentTemplate] = [
                all_templates[i] for i in sorted(indices[:count])
            ]
        elif self.settings.max_component_templates is not None:
            n = min(self.settings.max_component_templates, len(all_templates))
            indices = list(range(len(all_templates)))
            self.rng.shuffle(indices)
            templates = [all_templates[i] for i in sorted(indices[:n])]
        else:
            templates = all_templates

        components: list[ComponentSpec] = []
        component_stock: dict[str, int] = {}
        for template in templates:
            component, stock_map = self._materialize_component(
                template=template,
                category=domain.product_category,
                scenario_number=scenario_number,
            )
            components.append(component)
            component_stock.update(stock_map)
        return components, component_stock

    def _build_component_vendors(
        self,
        *,
        scenario_number: int,
        manufacturing_products: Sequence[ManufacturingProductSpec],
        final_product_code: str,
        deadlines: list[int],
        deadline_buckets: Sequence[tuple[int, int]],
        total_demand: int,
        per_unit_by_code: dict[str, int],
        component_stock: dict[str, int],
        start_index: int,
        used_prefixes: set[str],
        used_suffixes: set[str],
    ) -> tuple[list[VendorSpec], int]:
        """Sample component vendors with lead times capped to the longest
        feasible arrival window for that component — the earliest consumer MO
        start day. Vendors are constructed in a single pass with the correct
        lead time from the start."""
        max_lead_by_code = self._component_lead_caps(
            manufacturing_products=manufacturing_products,
            final_product_code=final_product_code,
            deadlines=deadlines,
        )

        vendors: list[VendorSpec] = []
        vendor_counter = start_index
        product_specs: dict[str, ProductSpec] = {}
        for mp in manufacturing_products:
            for comp in mp.components:
                product_specs.setdefault(comp.product.code, comp.product)
        for code, product in product_specs.items():
            if code in {mp.product.code for mp in manufacturing_products}:
                continue  # subassemblies are produced in-house, not purchased
            cap = max_lead_by_code.get(code)
            if cap is None:
                continue
            vendor_counter, new_vendors = self._materialize_component_vendors_for_product(
                scenario_number=scenario_number,
                deadlines=deadlines,
                deadline_buckets=deadline_buckets,
                total_demand=total_demand,
                per_unit=per_unit_by_code.get(code, 1),
                product=product,
                component_stock_units=component_stock.get(code, 0),
                vendor_counter=vendor_counter,
                used_prefixes=used_prefixes,
                used_suffixes=used_suffixes,
                max_lead_time=cap,
            )
            vendors.extend(new_vendors)
        return vendors, vendor_counter

    def _component_lead_caps(
        self,
        *,
        manufacturing_products: Sequence[ManufacturingProductSpec],
        final_product_code: str,
        deadlines: Sequence[int],
    ) -> dict[str, int]:
        """Earliest-feasible PO arrival day for each purchased component.

        A component's PO must arrive no later than the earliest start day of
        any MO that consumes it. Start day of a top-level MO is
        ``min(customer deadline) - final product lead``; for a subassembly, it
        is ``earliest start of its consumer MO - subassembly lead``. Returns a
        per-code cap in days-from-now.
        """
        min_deadline = min(deadlines) if deadlines else 0
        mp_by_code = {mp.product.code: mp for mp in manufacturing_products}
        consumers_by_code: dict[str, list[str]] = {}
        for mp in manufacturing_products:
            for comp in mp.components:
                consumers_by_code.setdefault(comp.product.code, []).append(mp.product.code)

        earliest_start_cache: dict[str, int] = {}

        def earliest_start(code: str) -> int:
            if code in earliest_start_cache:
                return earliest_start_cache[code]
            spec = mp_by_code[code]
            if code == final_product_code:
                start = min_deadline - spec.lead_days
            else:
                start = min(earliest_start(cc) for cc in consumers_by_code[code]) - spec.lead_days
            earliest_start_cache[code] = start
            return start

        caps: dict[str, int] = {}
        for component_code, consumer_codes in consumers_by_code.items():
            if component_code in mp_by_code:
                continue
            caps[component_code] = max(1, min(earliest_start(cc) for cc in consumer_codes))
        return caps

    def _materialize_component(
        self,
        *,
        template: ComponentTemplate,
        category: str,
        scenario_number: int,
    ) -> tuple[ComponentSpec, dict[str, int]]:
        product = template.build_product(self.rng)
        product.category = category
        product.code = self._namespaced_code(scenario_number, product.code)
        per_unit = max(1, template.sample_per_unit(self.rng))
        stock_map: dict[str, int] = {}
        base_stock = self._sample_stock_quantity(template.stock_range)
        if base_stock:
            stock_map[product.code] = base_stock
        component = ComponentSpec(product=product, per_unit=per_unit)
        return component, stock_map

    def _materialize_component_vendors_for_product(
        self,
        *,
        scenario_number: int,
        deadlines: list[int],
        deadline_buckets: Sequence[tuple[int, int]],
        total_demand: int,
        per_unit: int,
        product: ProductSpec,
        component_stock_units: int,
        vendor_counter: int,
        used_prefixes: set[str],
        used_suffixes: set[str],
        max_lead_time: int,
    ) -> tuple[int, list[VendorSpec]]:
        selected_categories = self._select_component_vendor_categories()
        component_demand = max(total_demand * max(per_unit, 1), 1)
        vendors: list[VendorSpec] = []

        for category in selected_categories:
            lead_time = self._randint(category.lead_range[0], category.lead_range[1] + 1)
            lead_time = max(1, min(lead_time, max_lead_time))
            anchor_cost = product.standard_price_dollars * self.rng.uniform(0.9, 1.08)
            price_factor = self.rng.uniform(*category.price_factor)
            price = round(anchor_cost * price_factor, 2)
            price = _money(
                max(
                    product.standard_price_dollars * 0.7,
                    min(price, product.standard_price_dollars * 1.6),
                )
            )

            min_qty = self._randint(category.min_qty_range[0], category.min_qty_range[1] + 1)
            min_qty = min(min_qty, component_demand)

            if category.max_qty_range is None:
                max_qty = None
            else:
                max_low = max(min_qty, category.max_qty_range[0])
                max_high = category.max_qty_range[1] + 1
                if max_low >= max_high:
                    max_qty = max_low
                else:
                    max_qty = self._randint(max_low, max_high)

            name = self._vendor_name(used_prefixes, used_suffixes)
            vendor = VendorSpec(
                name=name,
                ref=self._vendor_ref(scenario_number, vendor_counter),
                supplier_rank=vendor_counter,
                product_code=product.code,
                price_dollars=price,
                lead_time=lead_time,
                min_qty=min_qty,
                max_qty=max_qty,
                description=f"{category.label} ({lead_time}d)",
                supply_role="component",
            )
            vendors.append(vendor)
            vendor_counter += 1

        self._assign_correlated_vendor_caps(
            vendors=vendors,
            required_qty=component_demand,
            deadline_buckets=[
                (deadline, cumulative * max(per_unit, 1))
                for deadline, cumulative in deadline_buckets
            ],
            base_deadline_supply=max(int(component_stock_units), 0),
        )
        return vendor_counter, vendors

    def _select_component_vendor_categories(self) -> list[ComponentVendorCategory]:
        min_choices, max_choices = self.settings.component_vendor_category_count_range
        max_allowed = min(len(COMPONENT_VENDOR_CATEGORIES), max_choices)
        min_allowed = min(min_choices, max_allowed)
        vendor_choices = self._randint(min_allowed, max_allowed + 1)
        selected_keys: list[str] = ["rapid"]
        available_extra = [cat.key for cat in COMPONENT_VENDOR_CATEGORIES if cat.key != "rapid"]
        extra_needed = max(0, vendor_choices - len(selected_keys))
        if extra_needed:
            selected_keys.extend(self._sample_optional(available_extra, extra_needed))
        return [
            next(cat for cat in COMPONENT_VENDOR_CATEGORIES if cat.key == key)
            for key in selected_keys
        ]

    def _sample_stock_quantity(self, stock_range: tuple[int, int] | None) -> int:
        if stock_range is None:
            return 0
        low, high = stock_range
        return self._randint(low, high + 1)

    def _sample_workcenter_count(self) -> int:
        if self.settings.workcenter_count_range is not None:
            low, high = self.settings.workcenter_count_range
            return max(1, self._randint(low, high + 1))
        difficulty = self.settings.difficulty_key
        if difficulty == "easy" and self.settings.manufacturing_only_enabled:
            return 2
        if difficulty == "hard":
            return max(3, self._randint(3, 6))
        if difficulty == "medium":
            return max(2, self._randint(2, 4))
        return 0

    def _select_manufacturing_pattern(self) -> str:
        if self.settings.manufacturing_pattern:
            return self.settings.manufacturing_pattern
        difficulty = self.settings.difficulty_key or "medium"
        options = (
            HARD_MANUFACTURING_PATTERNS if difficulty == "hard" else MEDIUM_MANUFACTURING_PATTERNS
        )
        return self._choice(options)

    def _build_workcenter_pools(
        self,
        *,
        scenario_number: int,
        count: int,
        category: str,
    ) -> list[WorkcenterSpec]:
        base_names = (
            "Assembly Line",
            "Fabrication Cell",
            "Precision Bench",
            "Rapid Build Cell",
            "Overflow Cell",
        )
        pools: list[WorkcenterSpec] = []
        for index in range(count):
            pools.append(
                WorkcenterSpec(
                    code=self._namespaced_code(scenario_number, f"WC{index + 1:02d}"),
                    name=f"{base_names[index % len(base_names)]} {index + 1}",
                    capacity_minutes=1,
                    note=f"{category} workcenter pool {index + 1}",
                )
            )
        return pools

    def _subassembly_product(
        self,
        *,
        scenario_number: int,
        product: ProductSpec,
        label: str,
        components: Sequence[ComponentSpec],
    ) -> ProductSpec:
        base_cost = sum(
            component.product.standard_price_dollars * component.per_unit
            for component in components
        )
        code_parts = [part[:3].upper() for part in label.split() if part]
        if not code_parts:
            raise ValueError("Subassembly label must contain at least one token")
        return ProductSpec(
            name=f"{product.name} {label}",
            code=self._namespaced_code(
                scenario_number, f"{''.join(code_parts)}-{len(components):02d}"
            ),
            category=product.category,
            list_price_dollars=_money(max(base_cost * 1.65, product.list_price_dollars * 0.28)),
            standard_price_dollars=_money(max(base_cost * 1.1, 8.0)),
            type="product",
            routes=("manufacture",),
        )

    def _choice_spec(
        self,
        *,
        workcenter: WorkcenterSpec,
        unit_cost: float,
        lead_days: int,
        time_per_unit_minutes: float,
        operation_name: str,
    ) -> WorkcenterChoiceSpec:
        return WorkcenterChoiceSpec(
            workcenter_code=workcenter.code,
            unit_cost_dollars=_money(unit_cost),
            lead_time_days=max(1, int(lead_days)),
            time_per_unit_minutes=round(max(time_per_unit_minutes, 1.0), 2),
            operation_name=operation_name,
        )

    def _assign_pool_alternatives(
        self,
        *,
        pools: list[WorkcenterSpec],
        alternatives_by_code: dict[str, tuple[str, ...]],
    ) -> list[WorkcenterSpec]:
        updated: list[WorkcenterSpec] = []
        for pool in pools:
            updated.append(
                pool.model_copy(
                    update={
                        "alternative_workcenter_codes": alternatives_by_code.get(pool.code, ()),
                    }
                )
            )
        return updated

    def _build_manufacturing_graph(
        self,
        *,
        scenario_number: int,
        product: ProductSpec,
        components: Sequence[ComponentSpec],
        pattern: str | None,
        total_demand: int,
        stock_units: int,
        deadlines: Sequence[int],
        manufacturing_only: ManufacturingOnlyConfig,
    ) -> tuple[
        tuple[ComponentSpec, ...], tuple[ManufacturingProductSpec, ...], tuple[WorkcenterSpec, ...]
    ]:
        return build_manufacturing_graph(
            self,
            scenario_number=scenario_number,
            product=product,
            components=components,
            pattern=pattern,
            total_demand=total_demand,
            stock_units=stock_units,
            deadlines=deadlines,
            manufacturing_only=manufacturing_only,
        )

    def _dirichlet_alpha_scale(self) -> float:
        tau = min(max(float(self.settings.sampling_tightness), 0.0), 1.0)
        return max(0.5, 2.0 - (1.6 * tau))

    def _sample_ratio_in_range(self, ratio_range: tuple[float, float]) -> float:
        low, high = ratio_range
        if high <= low:
            return float(low)
        tau = min(max(float(self.settings.sampling_tightness), 0.0), 1.0)
        concentration = 5.0
        alpha = 1.0 + (concentration * (1.0 - tau))
        beta = 1.0 + (concentration * tau)
        u = float(self.rng.beta(alpha, beta))
        return float(low + ((high - low) * u))

    def _dirichlet_int_split(self, total: int, alpha: Sequence[float]) -> list[int]:
        if total <= 0:
            return [0 for _ in alpha]
        if not alpha:
            return []
        probs = self.rng.dirichlet(_np.array([max(1e-3, float(a)) for a in alpha], dtype=float))
        draw = self.rng.multinomial(int(total), probs)
        return [int(v) for v in draw.tolist()]

    def _bounded_dirichlet_split(
        self,
        *,
        total: int,
        capacities: Sequence[int],
        alpha: Sequence[float],
        fail_reason: str,
    ) -> list[int]:
        if total <= 0:
            return [0 for _ in capacities]
        if len(capacities) != len(alpha):
            raise ValueError("Capacity and alpha inputs are out of sync")
        caps = [max(0, int(cap)) for cap in capacities]
        if total > sum(caps):
            raise RuntimeError(fail_reason)
        proposal = self._dirichlet_int_split(total, alpha)
        assigned = [min(p, cap) for p, cap in zip(proposal, caps, strict=False)]
        remainder = total - sum(assigned)
        if remainder <= 0:
            return assigned
        slack = [cap - val for cap, val in zip(caps, assigned, strict=False)]
        # Redistribute remainder to buckets with available slack, weighted by alpha.
        order = sorted(
            range(len(slack)),
            key=lambda idx: (alpha[idx], slack[idx], -idx),
            reverse=True,
        )
        for idx in order:
            if remainder <= 0:
                break
            room = max(0, slack[idx])
            if room <= 0:
                continue
            add = min(room, remainder)
            assigned[idx] += add
            remainder -= add
        if remainder > 0:
            raise RuntimeError(fail_reason)
        return assigned

    def _lead_anchor(
        self, anchor: Literal["min", "median", "max"], deadlines: Sequence[int]
    ) -> int:
        if anchor == "min":
            return min(deadlines)
        if anchor == "median":
            return int(round(statistics.median(deadlines)))
        return max(deadlines)

    def _offer_description(
        self,
        label: str,
        lead_time: int,
        min_qty: int,
        max_qty: int | None,
    ) -> str:
        qty_text = f"{min_qty}+ units" if max_qty is None else f"{min_qty}-{max_qty} units"
        return f"{label} lane ({lead_time}d transit, {qty_text})"

    def _randint(self, low: int, high: int) -> int:
        value = self.rng.integers(low, high)
        return int(value)

    def _randints(self, low: int, high: int, count: int) -> list[int]:
        values = self.rng.integers(low, high, size=count)
        return [int(v) for v in values.tolist()]

    def _choice(self, population: Sequence[str]) -> str:
        return str(self.rng.choice(population))

    def _sample_optional(self, population: Sequence[str], k: int) -> list[str]:
        if k <= 0:
            return []
        choices = self.rng.choice(population, size=k, replace=False)
        if hasattr(choices, "tolist"):
            choices = choices.tolist()
        return [str(v) for v in choices]

    def _manufacturing_only_config(self) -> ManufacturingOnlyConfig:
        return ManufacturingOnlyConfig(
            family=self.settings.manufacturing_only_family,
            no_buy_route=self.settings.manufacturing_only_no_buy_route,
            no_available_vendors=self.settings.manufacturing_only_no_available_vendors,
        )

    def _instruction(
        self,
        blueprint: ScenarioBlueprint,
    ) -> str:
        return build_instruction(blueprint)

    def _background(
        self,
        blueprint: ScenarioBlueprint,
        _vendors: Sequence[VendorSpec] | None = None,
    ) -> str:
        return build_background_and_policy(blueprint)


def derive_seeds(base_seed: int, count: int) -> list[int]:
    """Derive independent, reproducible seeds from a base seed."""
    rng = _np.random.default_rng(base_seed)
    return [int(rng.integers(0, 2**63)) for _ in range(count)]


MAX_RESAMPLE_ATTEMPTS = 150


def _plan_schedule_by_order(
    blueprint: ScenarioBlueprint,
    plan,
) -> list[dict[str, object]]:
    orders = sorted(
        [order.model_dump() for order in plan.manufacturing_orders],
        key=lambda row: int(row.get("level", 0)),
        reverse=True,
    )
    scheduled: list[dict[str, object]] = []
    for row in orders:
        lead = int(row["lead_time"])
        if row.get("origin_customer_refs"):
            due_day = int(row.get("needed_by_days") or 0)
        elif row.get("origin_plan_refs"):
            origin_refs = set(row.get("origin_plan_refs") or [])
            parent_starts = [
                int(parent["start_day"])
                for parent in scheduled
                if parent.get("plan_ref") in origin_refs
            ]
            if not parent_starts:
                return []
            due_day = min(parent_starts)
        else:
            consumer_codes = set(row.get("consumer_product_codes") or [])
            parent_starts = [
                int(parent["start_day"])
                for parent in scheduled
                if parent["product_code"] in consumer_codes
            ]
            if not parent_starts:
                return []
            due_day = min(parent_starts)
        start_day = due_day - lead
        scheduled.append({**row, "start_day": start_day, "due_day": due_day})
    return scheduled


def _plan_timing_feasible(blueprint: ScenarioBlueprint, plan) -> bool:
    bom_graph = {
        manufacturing_product.code: [
            (component.product.code, int(component.per_unit))
            for component in manufacturing_product.components
        ]
        for manufacturing_product in blueprint.manufactured_products
    }
    scheduled_orders = _plan_schedule_by_order(blueprint, plan)
    if blueprint.manufactured_products and not scheduled_orders and plan.manufacturing_orders:
        return False

    arrivals: dict[str, list[tuple[int, int]]] = defaultdict(list)
    for code, qty in blueprint.product_stock_units.items():
        if qty > 0:
            arrivals[code].append((0, int(qty)))
    for order in plan.purchase_orders:
        if order.quantity > 0:
            arrivals[order.product_code].append((int(order.lead_time), int(order.quantity)))

    for rows in arrivals.values():
        rows.sort(key=lambda item: item[0])

    available: dict[str, int] = defaultdict(int)
    offsets: dict[str, int] = defaultdict(int)

    def apply_arrivals(up_to_day: int) -> None:
        for code, rows in arrivals.items():
            idx = offsets[code]
            while idx < len(rows) and rows[idx][0] <= up_to_day:
                available[code] += rows[idx][1]
                idx += 1
            offsets[code] = idx

    for order in sorted(
        scheduled_orders, key=lambda row: (int(row["start_day"]), int(row["due_day"]))
    ):
        start_day = int(order["start_day"])
        due_day = int(order["due_day"])
        if due_day < start_day:
            return False
        apply_arrivals(start_day)
        for component_code, per_unit in bom_graph.get(order["product_code"], []):
            required = int(per_unit) * int(order["quantity"])
            if available[component_code] < required:
                return False
            available[component_code] -= required
        arrivals[order["product_code"]].append((due_day, int(order["quantity"])))
        arrivals[order["product_code"]].sort(key=lambda item: item[0])

    demand_buckets = {}
    if blueprint.unsat_demand:
        for allocation in plan.allocations:
            if allocation.fulfilled_demand <= 0:
                continue
            demand_buckets[allocation.deadline] = (
                demand_buckets.get(allocation.deadline, 0) + allocation.fulfilled_demand
            )
    else:
        for customer in blueprint.customers:
            demand_buckets[customer.deadline] = (
                demand_buckets.get(customer.deadline, 0) + customer.demand
            )
    cumulative = 0
    for deadline in sorted(demand_buckets):
        cumulative += demand_buckets[deadline]
        apply_arrivals(deadline)
        if available.get(blueprint.product.code, 0) < cumulative:
            return False
    return True


def _seeded_repair_customers(blueprint: ScenarioBlueprint) -> tuple[CustomerSpec, ...]:
    return tuple(
        customer.model_copy(
            update={
                "task_order_source": "seeded",
                "task_order_ref": customer.task_order_ref
                or _repair_sales_order_ref(blueprint.scenario_number, index),
                "seeded_order_state": "sale",
            }
        )
        for index, customer in enumerate(blueprint.customers, start=1)
    )


def _repair_sales_order_ref(scenario_number: int, index: int) -> str:
    return f"{_ref_namespace_prefix_for_channel(scenario_number, channel=0)}o{index:02d}"


def _repair_purchase_order_ref(scenario_number: int, index: int) -> str:
    return f"{_ref_namespace_prefix_for_channel(scenario_number, channel=0)}po{index:02d}"


def _repair_manufacturing_order_ref(scenario_number: int, index: int) -> str:
    return f"{_ref_namespace_prefix_for_channel(scenario_number, channel=0)}mo{index:02d}"


def _repair_offer_quantities(plan) -> tuple[RepairOfferQuantity, ...]:
    totals: dict[str, int] = defaultdict(int)
    for order in plan.purchase_orders:
        if order.quantity <= 0 or not order.offer_key:
            continue
        totals[order.offer_key] += int(order.quantity)
    return tuple(
        RepairOfferQuantity(offer_key=offer_key, quantity=quantity)
        for offer_key, quantity in sorted(totals.items())
    )


def _repair_manufacturing_quantities(plan) -> tuple[RepairManufacturingQuantity, ...]:
    totals: dict[tuple[str, str], int] = defaultdict(int)
    for order in plan.manufacturing_orders:
        if order.quantity <= 0:
            continue
        totals[(order.product_code, order.workcenter_code)] += int(order.quantity)
    return tuple(
        RepairManufacturingQuantity(
            product_code=product_code,
            workcenter_code=workcenter_code,
            quantity=quantity,
        )
        for (product_code, workcenter_code), quantity in sorted(totals.items())
    )


def _repair_purchase_order_sort_key(order) -> tuple[float, str, str, str]:
    return (
        float(order.planned_arrival_days or order.lead_time),
        order.supply_role,
        order.product_code,
        order.plan_ref,
    )


def _repair_manufacturing_order_sort_key(order) -> tuple[int, int, str, str]:
    return (
        -int(order.level),
        int(order.needed_by_days or 0),
        order.product_code,
        order.plan_ref,
    )


def _seeded_repair_purchase_orders(
    baseline_plan,
    *,
    scenario_number: int,
) -> tuple[ExistingPurchaseOrderData, ...]:
    orders = sorted(
        [order for order in baseline_plan.purchase_orders if order.quantity > 0],
        key=_repair_purchase_order_sort_key,
    )
    return tuple(
        ExistingPurchaseOrderData(
            ref=_repair_purchase_order_ref(scenario_number, index),
            plan_ref=order.plan_ref,
            vendor_ref=order.vendor_ref,
            product_code=order.product_code,
            quantity=float(order.quantity),
            price_unit=round(float(order.unit_cost), 2),
            planned_arrival_days=int(
                order.planned_arrival_days
                if order.planned_arrival_days is not None
                else order.lead_time
            ),
            origin_customer_refs=list(order.origin_customer_refs),
            origin_plan_refs=list(order.origin_plan_refs),
            supply_role=order.supply_role,
            offer_key=order.offer_key,
            note=(
                "Existing confirmed purchase commitment that is part of the pre-disruption plan."
            ),
        )
        for index, order in enumerate(orders, start=1)
    )


def _seeded_repair_manufacturing_orders(
    baseline_plan,
    *,
    scenario_number: int,
) -> tuple[ExistingManufacturingOrderData, ...]:
    orders = sorted(
        [order for order in baseline_plan.manufacturing_orders if order.quantity > 0],
        key=_repair_manufacturing_order_sort_key,
    )
    return tuple(
        ExistingManufacturingOrderData(
            ref=_repair_manufacturing_order_ref(scenario_number, index),
            plan_ref=order.plan_ref,
            product_code=order.product_code,
            quantity=float(order.quantity),
            workcenter_code=order.workcenter_code,
            start_days=int((order.needed_by_days or 0) - int(order.lead_time)),
            deadline_days=int(order.needed_by_days or 0),
            origin_customer_refs=list(order.origin_customer_refs),
            origin_plan_refs=list(order.origin_plan_refs),
            level=int(order.level),
            note=(
                "Existing confirmed manufacturing commitment that is part of the pre-disruption plan."
            ),
        )
        for index, order in enumerate(orders, start=1)
    )


def _repair_supplier_context(
    blueprint: ScenarioBlueprint,
    baseline_plan,
) -> ProcurementRepairContext:
    candidates = sorted(
        [
            order
            for order in baseline_plan.purchase_orders
            if order.supply_role == "finished" and order.quantity > 0 and order.offer_key
        ],
        key=lambda order: (-int(order.quantity), str(order.offer_key)),
    )
    if not candidates:
        raise RuntimeError(
            "Repair supplier scenario produced no finished purchase order to invalidate"
        )
    broken_order = candidates[0]
    seeded_purchase_orders = _seeded_repair_purchase_orders(
        baseline_plan,
        scenario_number=blueprint.scenario_number,
    )
    seeded_manufacturing_orders = _seeded_repair_manufacturing_orders(
        baseline_plan,
        scenario_number=blueprint.scenario_number,
    )
    broken_purchase_order_refs = tuple(
        order.ref for order in seeded_purchase_orders if order.plan_ref == broken_order.plan_ref
    )
    if not broken_purchase_order_refs:
        raise RuntimeError(
            "Repair supplier scenario could not map the broken baseline PO into seeded records"
        )
    return ProcurementRepairContext(
        kind="supplier_cancellation",
        broken_supplier_offer_key=broken_order.offer_key,
        broken_supplier_vendor_ref=broken_order.vendor_ref,
        impacted_customer_refs=tuple(sorted(broken_order.origin_customer_refs)),
        baseline_offer_quantities=_repair_offer_quantities(baseline_plan),
        baseline_manufacturing_quantities=_repair_manufacturing_quantities(baseline_plan),
        seeded_purchase_orders=tuple(
            order.model_copy(
                update={
                    "note": (
                        "Existing confirmed purchase commitment that is part of the pre-disruption plan. "
                        "This supplier commitment failed because the supplier canceled the order due to "
                        "overbooking."
                    )
                    if order.ref in broken_purchase_order_refs
                    else order.note
                }
            )
            for order in seeded_purchase_orders
        ),
        seeded_manufacturing_orders=seeded_manufacturing_orders,
        broken_purchase_order_refs=broken_purchase_order_refs,
        baseline_plan=baseline_plan,
    )


def _repair_workcenter_context(
    blueprint: ScenarioBlueprint,
    baseline_plan,
) -> ProcurementRepairContext:
    totals_by_code: dict[str, int] = defaultdict(int)
    for order in baseline_plan.manufacturing_orders:
        if order.product_code != blueprint.product.code or order.quantity <= 0:
            continue
        totals_by_code[order.workcenter_code] += int(order.quantity)
    if not totals_by_code:
        raise RuntimeError(
            "Repair workcenter scenario produced no finished manufacturing path to invalidate"
        )
    broken_workcenter_code = sorted(
        totals_by_code,
        key=lambda code: (-totals_by_code[code], code),
    )[0]
    broken_orders = sorted(
        [
            order
            for order in baseline_plan.manufacturing_orders
            if order.product_code == blueprint.product.code
            and order.workcenter_code == broken_workcenter_code
            and order.quantity > 0
        ],
        key=lambda order: (
            int(order.needed_by_days or 0),
            str(order.plan_ref),
        ),
    )
    if not broken_orders:
        raise RuntimeError("Repair workcenter scenario has no broken manufacturing orders to seed")
    impacted_customer_refs = sorted(
        {customer_ref for order in broken_orders for customer_ref in order.origin_customer_refs}
    )
    seeded_purchase_orders = _seeded_repair_purchase_orders(
        baseline_plan,
        scenario_number=blueprint.scenario_number,
    )
    seeded_manufacturing_orders = _seeded_repair_manufacturing_orders(
        baseline_plan,
        scenario_number=blueprint.scenario_number,
    )
    broken_manufacturing_order_refs = tuple(
        order.ref
        for order in seeded_manufacturing_orders
        if order.plan_ref in {broken_order.plan_ref for broken_order in broken_orders}
    )
    if len(broken_manufacturing_order_refs) != len(broken_orders):
        raise RuntimeError(
            "Repair workcenter scenario could not map all broken baseline MOs into seeded records"
        )
    return ProcurementRepairContext(
        kind="workcenter_outage",
        broken_workcenter_code=broken_workcenter_code,
        impacted_customer_refs=tuple(impacted_customer_refs),
        baseline_offer_quantities=_repair_offer_quantities(baseline_plan),
        baseline_manufacturing_quantities=_repair_manufacturing_quantities(baseline_plan),
        seeded_purchase_orders=seeded_purchase_orders,
        seeded_manufacturing_orders=tuple(
            order.model_copy(
                update={
                    "note": (
                        "Existing confirmed manufacturing commitment that is part of the pre-disruption "
                        "plan. This workcenter path is down and cannot be used for replacement work."
                    )
                    if order.ref in broken_manufacturing_order_refs
                    else order.note
                }
            )
            for order in seeded_manufacturing_orders
        ),
        broken_manufacturing_order_refs=broken_manufacturing_order_refs,
        baseline_plan=baseline_plan,
    )


def _build_repair_blueprint(
    *,
    blueprint: ScenarioBlueprint,
    seed: int,
    repair_kind: RepairScenarioKind,
    num_search_workers: int,
) -> ScenarioBlueprint:
    baseline_blueprint = blueprint.model_copy(
        update={
            "objective_kind": ProcurementObjectiveKind.min_new_spend,
            "repair_context": None,
        }
    )
    baseline_plan = (
        ScenarioGenerator(
            baseline_blueprint,
            seed=seed,
            num_search_workers=num_search_workers,
        )
        .generate()
        .optimal_plan
    )
    if repair_kind is RepairScenarioKind.supplier_cancellation:
        repair_context = _repair_supplier_context(baseline_blueprint, baseline_plan)
    else:
        repair_context = _repair_workcenter_context(baseline_blueprint, baseline_plan)
    repair_blueprint = baseline_blueprint.model_copy(
        update={
            "objective_kind": ProcurementObjectiveKind.repair_plan,
            "customers": _seeded_repair_customers(baseline_blueprint),
            "repair_context": repair_context,
        }
    )
    return repair_blueprint.model_copy(
        update={
            "instruction": build_instruction(repair_blueprint),
            "background_and_policy": build_background_and_policy(repair_blueprint),
        }
    )


def _objective_family_certified(
    *,
    blueprint: ScenarioBlueprint,
    candidate_plan,
    seed: int,
    num_search_workers: int,
) -> bool:
    if blueprint.objective_kind not in OPTIMIZING_PROCUREMENT_OBJECTIVES:
        return True
    threshold = OBJECTIVE_CERTIFICATION_THRESHOLDS.get(blueprint.objective_kind)
    if threshold is None:
        return True
    generator = ScenarioGenerator(
        blueprint,
        seed=seed,
        num_search_workers=num_search_workers,
    )
    spend_plan = generator.solve_plan_for_objective(ProcurementObjectiveKind.min_new_spend)
    spend_frontier_plan = generator.solve_plan_for_objective(
        blueprint.objective_kind,
        fixed_spend_cents=int(round(spend_plan.optimal_new_spend * 100)),
    )
    improvement = round(spend_frontier_plan.objective_value - candidate_plan.objective_value, 2)
    logger.info(
        "Objective certification: scenario=%s objective=%s candidate=%.2f frontier=%.2f improvement=%.2f threshold=%.2f",
        blueprint.scenario_number,
        blueprint.objective_kind,
        candidate_plan.objective_value,
        spend_frontier_plan.objective_value,
        improvement,
        threshold,
    )
    return improvement + 1e-6 >= threshold


def _build_and_solve(
    *,
    blueprint: ScenarioBlueprint,
    seed: int,
    num_search_workers: int = 8,
    settings: SamplerSettings | None = None,
    background_note: str = "",
    include_adjacent_data: bool = True,
) -> tuple[ScenarioBlueprint, ScenarioBuild, int]:
    """Build and solve a scenario, resampling until solver proves optimality."""

    def _with_background_note(bp: ScenarioBlueprint) -> ScenarioBlueprint:
        if not background_note:
            return bp
        note = background_note.strip()
        if note and (bp.background_and_policy or "").rstrip().endswith(note):
            return bp
        return bp.model_copy(
            update={
                "scenario_number": scenario_number,
                "background_and_policy": _merge_background(
                    bp.background_and_policy, background_note
                ),
            }
        )

    current_seed = seed
    resample_rng = _np.random.default_rng(seed)
    scenario_number = blueprint.scenario_number
    current_blueprint = _with_background_note(blueprint)

    domain = _infer_domain_from_blueprint(current_blueprint)
    repair_kind = settings.repair_kind if settings is not None else None
    for attempt in range(MAX_RESAMPLE_ATTEMPTS):
        attempt_started = time.perf_counter()
        logger.info(
            "Build/solve attempt scenario=%s attempt=%s/%s seed=%s repair=%s",
            scenario_number,
            attempt + 1,
            MAX_RESAMPLE_ATTEMPTS,
            current_seed,
            repair_kind.value if repair_kind is not None else "none",
        )
        try:
            solved_blueprint = current_blueprint
            if repair_kind is not None:
                solved_blueprint = _with_background_note(
                    _build_repair_blueprint(
                        blueprint=current_blueprint,
                        seed=current_seed,
                        repair_kind=repair_kind,
                        num_search_workers=num_search_workers,
                    )
                )
            generator = ScenarioGenerator(
                solved_blueprint,
                seed=current_seed,
                num_search_workers=num_search_workers,
            )
            build = generator.generate()
            if solved_blueprint.unsat_demand:
                allocations = build.optimal_plan.allocations
                if not any(allocation.accepted for allocation in allocations):
                    raise RuntimeError("Unsat demand produced no kept orders")
                if not any(not allocation.accepted for allocation in allocations):
                    raise RuntimeError("Unsat demand produced no cancelled orders")
                if not any(allocation.action == "cancel_seeded" for allocation in allocations):
                    raise RuntimeError("Unsat demand produced no seeded order cancellations")
                if any(
                    customer.task_order_source == "prompt_only"
                    for customer in solved_blueprint.customers
                ) and not any(
                    allocation.action == "create_confirm_prompt" for allocation in allocations
                ):
                    raise RuntimeError(
                        "Unsat partial-seed scenario produced no prompt-only kept orders"
                    )
            if include_adjacent_data:
                _inject_adjacent_data(
                    scenario=build.scenario,
                    scenario_number=scenario_number,
                    seed=current_seed,
                    domain=domain,
                )
            if not _plan_timing_feasible(solved_blueprint, build.optimal_plan):
                raise RuntimeError(
                    "Plan timing is infeasible under explicit MO/component scheduling."
                )
            if not _objective_family_certified(
                blueprint=solved_blueprint,
                candidate_plan=build.optimal_plan,
                seed=current_seed,
                num_search_workers=num_search_workers,
            ):
                raise RuntimeError(
                    "Scenario objective collapses against the spend-optimal frontier."
                )
            logger.info(
                "Build/solve accepted scenario=%s seed=%s attempt=%s elapsed=%.2fs",
                scenario_number,
                current_seed,
                attempt + 1,
                time.perf_counter() - attempt_started,
            )
            return solved_blueprint, build, current_seed
        except RuntimeError as e:
            logger.warning(
                "Scenario %s rejected (seed=%s, attempt=%d/%d, elapsed=%.2fs): %s — resampling",
                scenario_number,
                current_seed,
                attempt + 1,
                MAX_RESAMPLE_ATTEMPTS,
                time.perf_counter() - attempt_started,
                e,
            )
            current_seed = int(resample_rng.integers(0, 2**63))
            sampler = ProcurementSampler(seed=current_seed, settings=settings)
            current_blueprint = sampler.build_blueprint(scenario_number)
            if repair_kind is None:
                current_blueprint = _with_background_note(current_blueprint)
            domain = _infer_domain_from_blueprint(current_blueprint)

    raise RuntimeError(
        f"Failed to generate provably-optimal procurement scenario {scenario_number} "
        f"after {MAX_RESAMPLE_ATTEMPTS} attempts"
    )


def _infer_domain_from_blueprint(blueprint: ScenarioBlueprint) -> ProductDomain | None:
    return _infer_domain_from_blueprint_impl(blueprint)


def _inject_adjacent_data(
    *, scenario, scenario_number: int, seed: int, domain: ProductDomain | None = None
) -> None:
    _inject_adjacent_data_impl(
        scenario=scenario,
        scenario_number=scenario_number,
        seed=seed,
        domain=domain,
        name_prefixes=NAME_PREFIXES,
        code_namespace_prefix_for_channel=_code_namespace_prefix_for_channel,
        ref_namespace_prefix_for_channel=_ref_namespace_prefix_for_channel,
    )


def _dataset_entry_config(entry: ProcurementDatasetEntry) -> ProcurementScenarioConfig:
    if entry.config is not None:
        return load_procurement_config(entry.config)
    if entry.scenario is not None:
        return entry.scenario
    raise ValueError("dataset entry requires exactly one of config or scenario")


def _dataset_entry_task_pattern(entry: ProcurementDatasetEntry) -> str | None:
    if entry.config is None:
        return None
    return entry.config.stem


def build_dataset_blueprints(
    *,
    dataset_config: ProcurementDatasetConfig,
) -> list[tuple[ProcurementScenarioConfig, ScenarioBlueprint, int, str | None]]:
    """Build blueprints for an ordered dataset recipe."""
    total_count = sum(entry.count for entry in dataset_config.entries)
    seeds = derive_seeds(dataset_config.seed, total_count)
    results: list[tuple[ProcurementScenarioConfig, ScenarioBlueprint, int, str | None]] = []
    scenario_number = dataset_config.start_number
    seed_idx = 0
    for entry in dataset_config.entries:
        config = _dataset_entry_config(entry)
        task_pattern = _dataset_entry_task_pattern(entry)
        settings = sampler_settings_from_config(config)
        for _ in range(entry.count):
            seed = seeds[seed_idx]
            sampler = ProcurementSampler(seed=seed, settings=settings)
            blueprint = sampler.build_blueprint(scenario_number)
            results.append((config, blueprint, seed, task_pattern))
            scenario_number += 1
            seed_idx += 1
    return results


def _parse_difficulty_mix(mix_str: str) -> dict[str, int]:
    """Parse difficulty mix string like 'easy:75,medium:120,hard:105'."""
    result: dict[str, int] = {}
    for part in mix_str.split(","):
        key, count = part.strip().split(":")
        result[key.strip()] = int(count.strip())
    return result


# =============================================================================
# Difficulty Profiles (easy/medium/hard presets)
# =============================================================================


class ScenarioProfile(BaseModel):
    """Named preset combining sampler settings with metadata."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    key: str
    label: str
    description: str
    sampler_settings: SamplerSettings
    background_note: str = ""


_ALL_REJECT_RULES = frozenset(
    {"budget_below_list_price", "quantity_outside_window", "lead_time_below_minimum"}
)

_MIXED_SEEDED_RATIO_BY_DIFFICULTY: dict[str, tuple[float, float]] = {
    "easy": (0.55, 0.9),
    "medium": (0.35, 0.75),
    "hard": (0.25, 0.6),
}


def _to_internal_int_range(inclusive_range: tuple[int, int] | None) -> tuple[int, int] | None:
    if inclusive_range is None:
        return None
    low, high = inclusive_range
    return (low, high + 1)


def _manufacturing_pattern_from_config(config: ProcurementScenarioConfig) -> str | None:
    manufacture = config.manufacture
    if manufacture is None:
        return None
    if manufacture.workcenter_count == 1:
        if manufacture.bom_structure is BomStructure.single_bom:
            return "cost_driven"
        raise ValueError(
            "single-workcenter manufacture configs are only supported for bom_structure='single_bom'"
        )
    path = manufacture.workcenter_choice_path
    if path is None:
        raise ValueError(
            "workcenter_choice_path is required when workcenter_count is greater than 1"
        )
    mapping: dict[tuple[BomStructure, WorkcenterChoicePath], str] = {
        (BomStructure.single_bom, WorkcenterChoicePath.lowest_cost): "cost_driven",
        (BomStructure.single_bom, WorkcenterChoicePath.split_by_capacity): "split_by_capacity",
        (
            BomStructure.restricted_subassembly,
            WorkcenterChoicePath.qualified_workcenters,
        ): "capability_restricted",
        (BomStructure.single_subassembly, WorkcenterChoicePath.lowest_cost): "multilevel_basic",
        (
            BomStructure.single_subassembly,
            WorkcenterChoicePath.qualified_workcenters,
        ): "multilevel_capability",
        (
            BomStructure.single_subassembly,
            WorkcenterChoicePath.shared_overflow_capacity,
        ): "multilevel_shared_capacity",
        (
            BomStructure.parallel_subassemblies,
            WorkcenterChoicePath.branch_assigned_workcenters,
        ): "multilevel_multi_branch",
        (
            BomStructure.serial_subassemblies,
            WorkcenterChoicePath.branch_assigned_workcenters,
        ): "multilevel_deep_mixed",
        (
            BomStructure.shared_component_subassemblies,
            WorkcenterChoicePath.branch_assigned_workcenters,
        ): "multilevel_shared_leaf",
    }
    pattern = mapping.get((manufacture.bom_structure, path))
    if pattern is None:
        raise ValueError(
            f"unsupported manufacture configuration: bom_structure={manufacture.bom_structure.value} "
            f"workcenter_choice_path={path.value}"
        )
    return pattern


def sampler_settings_from_config(config: ProcurementScenarioConfig) -> SamplerSettings:
    include_components = config.supply.include_components
    manufacture = config.manufacture
    invoicing = config.invoicing
    if config.available_routes is AvailableRoutes.buy_only:
        allow_assembly = False
        manufacturing_only_family = None
        manufacturing_only_no_buy_route = False
        manufacturing_only_no_available_vendors = False
    else:
        allow_assembly = True
        manufacturing_only_family = (
            "policy_forbidden"
            if config.available_routes is AvailableRoutes.manufacture_only_policy_forbidden
            else None
        )
        manufacturing_only_no_buy_route = (
            config.available_routes is AvailableRoutes.manufacture_only_no_buy_route
        )
        manufacturing_only_no_available_vendors = (
            config.available_routes is AvailableRoutes.manufacture_only_no_available_vendors
        )
    if config.order_acceptance.mode is OrderAcceptanceMode.screen_orders_by_policy:
        unsat_demand_enabled = True
        unsat_reject_rules = frozenset(rule.value for rule in config.order_acceptance.reject_rules)
        if config.order_acceptance.seeded_order_coverage is SeededOrderCoverage.all_seeded_orders:
            unsat_full_seed_probability = 1.0
            unsat_seeded_ratio_range = (1.0, 1.0)
        else:
            unsat_full_seed_probability = 0.0
            unsat_seeded_ratio_range = _MIXED_SEEDED_RATIO_BY_DIFFICULTY[config.difficulty]
    else:
        unsat_demand_enabled = False
        unsat_reject_rules = _ALL_REJECT_RULES
        unsat_full_seed_probability = 0.5
        unsat_seeded_ratio_range = (0.45, 0.8)
    return SamplerSettings(
        customer_count_range=_to_internal_int_range(config.customers.count_range) or (4, 7),
        deadline_range=_to_internal_int_range(config.customers.deadline_range) or (4, 13),
        demand_range=_to_internal_int_range(config.customers.demand_range) or (6, 24),
        stock_ratio_range=config.supply.stock_ratio_range,
        finished_vendor_target_range=_to_internal_int_range(
            config.supply.finished_vendor_target_range
        ),
        finished_vendor_limit=config.supply.finished_vendor_limit,
        include_components=include_components,
        component_count_range=_to_internal_int_range(config.supply.component_count_range),
        max_component_templates=0 if not include_components else None,
        component_vendor_category_count_range=_to_internal_int_range(
            config.supply.component_vendor_category_count_range
        )
        or (2, 4),
        vendor_capacity_primary_ratio_range=config.supply.vendor_capacity_primary_ratio_range,
        sampling_tightness=config.supply.sampling_tightness,
        allow_assembly=allow_assembly,
        assembly_capacity_buffer_range=(
            _to_internal_int_range(manufacture.assembly_capacity_buffer_range)
            if manufacture and manufacture.assembly_capacity_buffer_range is not None
            else (3, 8)
        )
        or (3, 8),
        assembly_capacity_ratio_range=(
            manufacture.assembly_capacity_ratio_range
            if manufacture and manufacture.assembly_capacity_ratio_range is not None
            else (0.55, 0.9)
        ),
        workcenter_count_range=(
            (manufacture.workcenter_count, manufacture.workcenter_count + 1)
            if manufacture is not None
            else None
        ),
        manufacturing_pattern=_manufacturing_pattern_from_config(config),
        difficulty_key=config.difficulty,
        unsat_demand_enabled=unsat_demand_enabled,
        unsat_full_seed_probability=unsat_full_seed_probability,
        unsat_seeded_ratio_range=unsat_seeded_ratio_range,
        unsat_reject_rules=unsat_reject_rules,
        fixed_product_domain=(
            None
            if config.export.product_domain.value == "default"
            else config.export.product_domain.value
        ),
        invoice_required=invoicing.required,
        payment_term=(
            cast(Literal["immediate", "net_30"], invoicing.payment_term.value)
            if invoicing.payment_term is not None
            else None
        ),
        downpayment_required=invoicing.downpayment is not None,
        downpayment_threshold_amount=(
            float(invoicing.downpayment.order_threshold_amount)
            if invoicing.downpayment is not None
            else None
        ),
        downpayment_mode=(
            cast(Literal["percentage", "fixed_amount"], invoicing.downpayment.mode.value)
            if invoicing.downpayment is not None
            else None
        ),
        downpayment_value=(
            float(invoicing.downpayment.value) if invoicing.downpayment is not None else None
        ),
        manufacturing_only_family=manufacturing_only_family,
        manufacturing_only_no_buy_route=manufacturing_only_no_buy_route,
        manufacturing_only_no_available_vendors=manufacturing_only_no_available_vendors,
        repair_kind=config.repair.kind if config.repair is not None else None,
        objective_kind=(
            ProcurementObjectiveKind.min_new_spend
            if config.repair is not None
            else config.objective_kind
        ),
    )


DIFFICULTY_PROFILES: dict[str, ScenarioProfile] = {
    key: ScenarioProfile(
        key=preset.key,
        label=preset.label,
        description=preset.description,
        sampler_settings=sampler_settings_from_config(preset.config),
    )
    for key, preset in DIFFICULTY_PRESETS.items()
}

DEFAULT_PROFILE_ORDER: tuple[str, ...] = ("easy", "medium", "hard")


def _apply_manufacturing_only_overlay(
    base_settings: SamplerSettings,
    *,
    difficulty: str | None,
    manufacturing_only: ManufacturingOnlyConfig | None,
) -> SamplerSettings:
    if manufacturing_only is None or not manufacturing_only.enabled:
        return base_settings
    updates: dict[str, object] = {
        "manufacturing_only_family": manufacturing_only.family,
        "manufacturing_only_no_buy_route": manufacturing_only.no_buy_route,
        "manufacturing_only_no_available_vendors": manufacturing_only.no_available_vendors,
    }
    if difficulty == "easy":
        updates.update(
            include_components=True,
            component_count_range=(1, 2),
            max_component_templates=None,
            component_vendor_category_count_range=(1, 2),
            allow_assembly=True,
            assembly_capacity_buffer_range=(1, 3),
            assembly_capacity_ratio_range=(0.12, 0.24),
            sampling_tightness=min(base_settings.sampling_tightness, 0.2),
        )
    elif difficulty == "medium":
        updates.update(
            component_count_range=(3, 5),
            stock_ratio_range=(0.45, 0.58),
            sampling_tightness=min(base_settings.sampling_tightness, 0.45),
        )
    elif difficulty == "hard":
        updates.update(
            component_count_range=(4, 6),
            stock_ratio_range=(0.38, 0.5),
            sampling_tightness=min(base_settings.sampling_tightness, 0.58),
        )
    return base_settings.model_copy(update=updates)


def _merge_background(existing: str | None, note: str) -> str:
    base = (existing or "").rstrip()
    note = note.strip()
    if not note:
        return base
    return f"{base}\n{note}" if base else note


def _apply_unsat_demand_settings(
    base_settings: SamplerSettings,
    *,
    difficulty: str | None,
    unsat_demand: bool,
) -> SamplerSettings:
    if not unsat_demand:
        return base_settings
    updates: dict[str, object] = {"unsat_demand_enabled": True}
    if difficulty == "easy":
        updates.update(
            unsat_full_seed_probability=0.6,
            unsat_seeded_ratio_range=(0.55, 0.9),
        )
    elif difficulty == "medium":
        updates.update(
            unsat_full_seed_probability=0.45,
            unsat_seeded_ratio_range=(0.35, 0.75),
        )
    elif difficulty == "hard":
        updates.update(
            unsat_full_seed_probability=0.35,
            unsat_seeded_ratio_range=(0.25, 0.6),
        )
    else:
        updates.update(
            unsat_full_seed_probability=0.5,
            unsat_seeded_ratio_range=(0.4, 0.8),
        )
    return base_settings.model_copy(update=updates)


def _compose_sampler_settings(
    base_settings: SamplerSettings,
    *,
    difficulty: str | None,
    unsat_demand: bool,
    manufacturing_only: ManufacturingOnlyConfig | None,
) -> SamplerSettings:
    settings = _apply_manufacturing_only_overlay(
        base_settings,
        difficulty=difficulty,
        manufacturing_only=manufacturing_only,
    )
    return _apply_unsat_demand_settings(
        settings,
        difficulty=difficulty,
        unsat_demand=unsat_demand,
    )


def build_profiled_blueprints(
    *,
    profiles: Sequence[str],
    per_profile: int,
    base_seed: int | None,
    start_number: int,
    unsat_demand: bool = False,
    manufacturing_only: ManufacturingOnlyConfig | None = None,
) -> list[tuple[ScenarioBlueprint, int, str]]:
    """Build blueprints from difficulty profiles with per-scenario seeds."""
    total_count = len(profiles) * per_profile
    seeds = (
        [secrets.randbits(63) for _ in range(total_count)]
        if base_seed is None
        else derive_seeds(base_seed, total_count)
    )

    results: list[tuple[ScenarioBlueprint, int, str]] = []
    scenario_number = start_number
    seed_idx = 0

    for profile_key in profiles:
        profile = DIFFICULTY_PROFILES[profile_key]
        settings = _compose_sampler_settings(
            profile.sampler_settings,
            difficulty=profile_key,
            unsat_demand=unsat_demand,
            manufacturing_only=manufacturing_only,
        )
        for _ in range(per_profile):
            seed = seeds[seed_idx]
            sampler = ProcurementSampler(seed=seed, settings=settings)
            blueprint = sampler.build_blueprint(scenario_number)
            blueprint = blueprint.model_copy(
                update={
                    "scenario_number": scenario_number,
                    "background_and_policy": _merge_background(
                        blueprint.background_and_policy, profile.background_note
                    ),
                }
            )
            results.append((blueprint, seed, profile_key))
            scenario_number += 1
            seed_idx += 1
    return results


def build_mixed_blueprints(
    *,
    difficulty_counts: dict[str, int],
    base_seed: int | None,
    start_number: int,
    unsat_demand: bool = False,
    manufacturing_only: ManufacturingOnlyConfig | None = None,
) -> list[tuple[ScenarioBlueprint, int, str]]:
    """Build blueprints with custom counts per difficulty profile.

    Args:
        difficulty_counts: Dict mapping profile key to count, e.g. {"easy": 75, "medium": 120, "hard": 105}
        base_seed: Base seed for reproducibility
        start_number: Starting scenario number

    Returns:
        List of (blueprint, seed, difficulty) tuples
    """
    total_count = sum(difficulty_counts.values())
    seeds = (
        [secrets.randbits(63) for _ in range(total_count)]
        if base_seed is None
        else derive_seeds(base_seed, total_count)
    )

    results: list[tuple[ScenarioBlueprint, int, str]] = []
    scenario_number = start_number
    seed_idx = 0

    for profile_key, count in difficulty_counts.items():
        if profile_key not in DIFFICULTY_PROFILES:
            raise ValueError(f"Unknown difficulty profile: {profile_key}")
        profile = DIFFICULTY_PROFILES[profile_key]
        settings = _compose_sampler_settings(
            profile.sampler_settings,
            difficulty=profile_key,
            unsat_demand=unsat_demand,
            manufacturing_only=manufacturing_only,
        )
        for _ in range(count):
            seed = seeds[seed_idx]
            sampler = ProcurementSampler(seed=seed, settings=settings)
            blueprint = sampler.build_blueprint(scenario_number)
            blueprint = blueprint.model_copy(
                update={
                    "scenario_number": scenario_number,
                    "background_and_policy": _merge_background(
                        blueprint.background_and_policy, profile.background_note
                    ),
                }
            )
            results.append((blueprint, seed, profile_key))
            scenario_number += 1
            seed_idx += 1
    return results


def generate_mixed_tasks(
    target_dir: Path,
    *,
    difficulty_counts: dict[str, int],
    base_seed: int | None = DEFAULT_BASE_SEED,
    start_number: int = 3000,
    force: bool = False,
    num_search_workers: int = 8,
    include_adjacent_data: bool = True,
    unsat_demand: bool = False,
    manufacturing_only: ManufacturingOnlyConfig | None = None,
) -> list[Path]:
    """Generate scenarios with custom counts per difficulty profile.

    Args:
        target_dir: Directory to create tasks in
        difficulty_counts: Dict mapping profile key to count, e.g. {"easy": 75, "medium": 120, "hard": 105}
        base_seed: Base seed for reproducibility
        start_number: Starting scenario number

    Returns:
        List of paths to created task directories
    """
    target_dir.mkdir(parents=True, exist_ok=True)
    total = sum(difficulty_counts.values())

    if base_seed is None:
        logger.info("No base seed provided; each scenario gets random seed")
    else:
        logger.info("Generating with base_seed=%s (derives per-scenario seeds)", base_seed)

    mix_str = ", ".join(f"{k}:{v}" for k, v in difficulty_counts.items())
    logger.info(
        "Generating %s mixed Harbor task(s) start=%s mix={%s} (solver_workers=%s)",
        total,
        start_number,
        mix_str,
        num_search_workers,
    )

    blueprints_with_seeds = build_mixed_blueprints(
        difficulty_counts=difficulty_counts,
        base_seed=base_seed,
        start_number=start_number,
        unsat_demand=unsat_demand,
        manufacturing_only=manufacturing_only,
    )

    exporter = HarborTaskExporter(
        output_dir=target_dir,
        force=force,
    )
    exported: list[Path] = []

    for blueprint, seed, difficulty in blueprints_with_seeds:
        logger.info(
            "Solving scenario %s - %s [%s] (seed=%s)",
            blueprint.scenario_number,
            blueprint.name,
            difficulty,
            seed,
        )
        profile = DIFFICULTY_PROFILES[difficulty]
        settings = _compose_sampler_settings(
            profile.sampler_settings,
            difficulty=difficulty,
            unsat_demand=unsat_demand,
            manufacturing_only=manufacturing_only,
        )
        solved_blueprint, build, _ = _build_and_solve(
            blueprint=blueprint,
            seed=seed,
            num_search_workers=num_search_workers,
            settings=settings,
            background_note=profile.background_note,
            include_adjacent_data=include_adjacent_data,
        )
        task_dir = exporter.export(build, solved_blueprint, difficulty=difficulty)
        if task_dir is not None:
            exported.append(task_dir)
        log_plan("optimal", build.optimal_plan, log=logger)
        log_plan_guidance(build.optimal_plan, solved_blueprint, log=logger)

    return exported


def generate_profiled_tasks(
    target_dir: Path,
    *,
    profiles: Sequence[str] = DEFAULT_PROFILE_ORDER,
    per_profile: int = 1,
    base_seed: int | None = DEFAULT_BASE_SEED,
    start_number: int = 3000,
    force: bool = False,
    num_search_workers: int = 8,
    include_adjacent_data: bool = True,
    unsat_demand: bool = False,
    manufacturing_only: ManufacturingOnlyConfig | None = None,
) -> list[Path]:
    """Generate scenarios from difficulty profiles and export as Harbor tasks."""
    target_dir.mkdir(parents=True, exist_ok=True)

    if base_seed is None:
        logger.info("No base seed provided; each scenario gets random seed")
    else:
        logger.info("Generating with base_seed=%s (derives per-scenario seeds)", base_seed)

    logger.info(
        "Generating %s profiled Harbor task(s) start=%s profiles=%s",
        len(profiles) * per_profile,
        start_number,
        ",".join(profiles),
    )

    blueprints_with_seeds = build_profiled_blueprints(
        profiles=profiles,
        per_profile=per_profile,
        base_seed=base_seed,
        start_number=start_number,
        unsat_demand=unsat_demand,
        manufacturing_only=manufacturing_only,
    )

    exporter = HarborTaskExporter(output_dir=target_dir, force=force)
    exported: list[Path] = []

    for blueprint, seed, difficulty in blueprints_with_seeds:
        profile = DIFFICULTY_PROFILES[difficulty]
        settings = _compose_sampler_settings(
            profile.sampler_settings,
            difficulty=difficulty,
            unsat_demand=unsat_demand,
            manufacturing_only=manufacturing_only,
        )
        logger.info(
            "Solving scenario %s - %s [%s] (seed=%s)",
            blueprint.scenario_number,
            blueprint.name,
            difficulty,
            seed,
        )
        solved_blueprint, build, _ = _build_and_solve(
            blueprint=blueprint,
            seed=seed,
            num_search_workers=num_search_workers,
            settings=settings,
            background_note=profile.background_note,
            include_adjacent_data=include_adjacent_data,
        )
        task_dir = exporter.export(build, solved_blueprint, difficulty=difficulty)
        if task_dir is not None:
            exported.append(task_dir)
        log_plan("optimal", build.optimal_plan, log=logger)
        log_plan_guidance(build.optimal_plan, solved_blueprint, log=logger)
    return exported


def build_routing_pattern_blueprints(
    *,
    patterns: Sequence[str] = MULTI_WORKCENTER_ROUTING_PATTERNS,
    difficulties: Sequence[str] = ROUTING_PATTERN_COVERAGE_DIFFICULTIES,
    base_seed: int | None,
    start_number: int,
    manufacturing_only: ManufacturingOnlyConfig | None = None,
) -> list[tuple[ScenarioBlueprint, int, str, str]]:
    """Build one blueprint per ``(pattern, difficulty)`` pair.

    Iterates difficulties outer, patterns inner so that scenario numbers land
    in contiguous difficulty blocks, and pins each sampler to a specific
    ``manufacturing_pattern`` via a settings overlay. Seeds are derived from
    ``base_seed`` for reproducibility.
    """
    total_count = len(difficulties) * len(patterns)
    seeds = (
        [secrets.randbits(63) for _ in range(total_count)]
        if base_seed is None
        else derive_seeds(base_seed, total_count)
    )

    results: list[tuple[ScenarioBlueprint, int, str, str]] = []
    scenario_number = start_number
    seed_idx = 0

    for difficulty in difficulties:
        if difficulty not in DIFFICULTY_PROFILES:
            raise ValueError(f"Unknown difficulty profile: {difficulty}")
        profile = DIFFICULTY_PROFILES[difficulty]
        base_settings = _apply_manufacturing_only_overlay(
            profile.sampler_settings,
            difficulty=difficulty,
            manufacturing_only=manufacturing_only,
        )
        for pattern in patterns:
            settings = base_settings.model_copy(update={"manufacturing_pattern": pattern})
            seed = seeds[seed_idx]
            sampler = ProcurementSampler(seed=seed, settings=settings)
            blueprint = sampler.build_blueprint(scenario_number)
            blueprint = blueprint.model_copy(
                update={
                    "scenario_number": scenario_number,
                    "background_and_policy": _merge_background(
                        blueprint.background_and_policy, profile.background_note
                    ),
                }
            )
            results.append((blueprint, seed, difficulty, pattern))
            scenario_number += 1
            seed_idx += 1
    return results


def generate_routing_pattern_tasks(
    target_dir: Path,
    *,
    patterns: Sequence[str] = MULTI_WORKCENTER_ROUTING_PATTERNS,
    difficulties: Sequence[str] = ROUTING_PATTERN_COVERAGE_DIFFICULTIES,
    base_seed: int | None = DEFAULT_BASE_SEED,
    start_number: int = 9110,
    force: bool = False,
    num_search_workers: int = 8,
    include_adjacent_data: bool = True,
    manufacturing_only: ManufacturingOnlyConfig | None = None,
) -> list[Path]:
    """Generate one representative Harbor task per ``(pattern, difficulty)`` pair.

    Each task uses the difficulty-specific sampler settings overlaid with the
    given ``manufacturing_pattern``, so the emitted scenario exercises that
    pattern deterministically rather than relying on random pattern sampling.
    """
    target_dir.mkdir(parents=True, exist_ok=True)

    if base_seed is None:
        logger.info("No base seed provided; each scenario gets random seed")
    else:
        logger.info("Generating with base_seed=%s (derives per-scenario seeds)", base_seed)

    logger.info(
        "Generating routing-pattern coverage: patterns=%s difficulties=%s start=%s",
        ",".join(patterns),
        ",".join(difficulties),
        start_number,
    )

    blueprints = build_routing_pattern_blueprints(
        patterns=patterns,
        difficulties=difficulties,
        base_seed=base_seed,
        start_number=start_number,
        manufacturing_only=manufacturing_only,
    )

    exporter = HarborTaskExporter(
        output_dir=target_dir,
        force=force,
    )
    exported: list[Path] = []

    for blueprint, seed, difficulty, pattern in blueprints:
        profile = DIFFICULTY_PROFILES[difficulty]
        settings = _apply_manufacturing_only_overlay(
            profile.sampler_settings,
            difficulty=difficulty,
            manufacturing_only=manufacturing_only,
        ).model_copy(update={"manufacturing_pattern": pattern})
        logger.info(
            "Solving routing-pattern scenario %s [%s/%s] (seed=%s)",
            blueprint.scenario_number,
            difficulty,
            pattern,
            seed,
        )
        solved_blueprint, build, _ = _build_and_solve(
            blueprint=blueprint,
            seed=seed,
            num_search_workers=num_search_workers,
            settings=settings,
            background_note=profile.background_note,
            include_adjacent_data=include_adjacent_data,
        )
        task_dir = exporter.export(
            build,
            solved_blueprint,
            difficulty=difficulty,
            name_suffix=pattern,
        )
        if task_dir is not None:
            exported.append(task_dir)
        log_plan("optimal", build.optimal_plan, log=logger)
        log_plan_guidance(build.optimal_plan, solved_blueprint, log=logger)

    return exported
