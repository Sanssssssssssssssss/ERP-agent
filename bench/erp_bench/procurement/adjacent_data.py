"""Procurement adjacent-data generation helpers."""

from __future__ import annotations

from collections.abc import Callable

import numpy as _np
from numpy.random import Generator

from schemas.models import (
    BOMComponentData,
    BOMData,
    CustomerData,
    ProductData,
    StockLevelData,
    VendorData,
    VendorInfoData,
)

from .product_domains import PRODUCT_DOMAINS, ProductDomain
from .solver import ScenarioBlueprint

_ADJACENT_VENDOR_SUFFIXES: tuple[str, ...] = (
    "Supply",
    "Distribution",
    "Materials",
    "Sourcing",
    "Trading",
    "Procurement",
    "Wholesale",
    "Resources",
    "Components",
    "Parts Co",
    "Logistics",
    "Freight",
    "Carriers",
    "Transit",
    "Networks",
)
_ADJACENT_CUSTOMER_SUFFIXES: tuple[str, ...] = (
    "Manufacturing",
    "Industries",
    "Engineering",
    "Operations",
    "Technologies",
    "Solutions",
    "Fabrication",
    "Assembly",
    "Production",
    "Services",
)
_ADJACENT_CUSTOMER_DIVISIONS: tuple[str, ...] = (
    "HQ Procurement",
    "Site Logistics",
    "Plant Maintenance",
    "Field Ops",
    "Quality Lab",
    "R&D Workshop",
    "Tooling Center",
    "Warehouse Ops",
    "Test Facility",
    "Prototype Shop",
)
_ADJACENT_PRODUCT_ADJECTIVES: tuple[str, ...] = (
    "Standard",
    "Heavy-Duty",
    "Compact",
    "Industrial",
    "Precision",
    "Commercial",
    "Reinforced",
    "Modular",
    "Premium",
    "Universal",
    "High-Performance",
    "Economy",
    "Corrosion-Resistant",
    "Stainless",
    "Hardened",
    "Lightweight",
    "Extended-Life",
    "Quick-Release",
    "Adjustable",
    "Flanged",
    "Tapered",
    "Spring-Loaded",
    "Metric",
    "Sealed",
    "Double-Row",
    "Single-Stage",
    "Dual-Stage",
    "Low-Profile",
    "Wide-Body",
    "Narrow-Body",
    "High-Torque",
    "Low-Noise",
    "Thermal-Guard",
    "Impact-Rated",
    "Cold-Rolled",
    "Heat-Treated",
    "Anodized",
    "Powder-Coated",
    "UV-Stable",
    "Weatherproof",
    "Food-Grade",
    "Medical-Grade",
    "Cleanroom",
    "Zero-Backlash",
    "Self-Lubricating",
    "Anti-Vibration",
    "High-Flow",
    "Low-Flow",
    "Pressure-Balanced",
    "Low-Friction",
    "High-Cycle",
    "Rapid-Lock",
    "Snap-Fit",
    "Threaded",
    "Non-Magnetic",
    "High-Temp",
    "Low-Temp",
)
_ADJACENT_PRODUCT_NOUNS: tuple[str, ...] = (
    "Bearing Housing",
    "Shaft Coupling",
    "Gasket Set",
    "Valve Body",
    "Hinge Assembly",
    "Bushing Kit",
    "Retainer Ring",
    "Flange Adapter",
    "Impeller Unit",
    "Gear Cartridge",
    "Bracket Mount",
    "Piston Sleeve",
    "Rotor Disc",
    "Cam Follower",
    "Seal Pack",
    "Sprocket Hub",
    "Timing Pulley",
    "Linear Slide",
    "Drive Belt",
    "Actuator Cylinder",
    "Pressure Gauge",
    "Flow Meter",
    "Relay Module",
    "Terminal Block",
    "Contactor Unit",
    "Filter Element",
    "Clutch Pack",
    "Damper Plate",
    "Anchor Bolt Set",
    "Spacer Ring",
)
_ADJACENT_PRODUCT_CATEGORIES: tuple[str, ...] = (
    "Industrial Spare Parts",
    "Mechanical Components",
    "Replacement Parts",
    "Maintenance Supplies",
    "Hardware Accessories",
    "Pneumatic Components",
    "Electrical Components",
    "Fasteners and Fixings",
    "Seals and Gaskets",
    "Power Transmission",
)
_ADJACENT_VENDOR_INFO_NAMES: tuple[str, ...] = (
    "Regional lane",
    "Standard delivery",
    "Express route",
    "Bulk shipment",
    "Local pickup",
    "Warehouse direct",
    "Cross-dock",
    "Scheduled freight",
)
_ADJACENT_EMAIL_DOMAINS: tuple[str, ...] = (
    "procurement.example",
    "ops-mail.example",
    "sourcing.example",
    "warehouse.example",
    "supply-chain.example",
    "logistics.example",
)
_ADJACENT_CUSTOMER_TAGS: tuple[tuple[str, ...], ...] = (
    ("operations",),
    ("procurement",),
    ("maintenance",),
    ("logistics",),
    ("operations", "bulk"),
    ("procurement", "preferred"),
)
_ADJACENT_CUSTOMER_COMMENTS: tuple[str, ...] = (
    "Operating division account.",
    "Recurring quarterly orders.",
    "Maintenance contract on file.",
    "Preferred payment net-30.",
    "Regional distribution hub.",
    "",
)


def _unique_combo_names(
    rng: Generator,
    prefixes: tuple[str, ...],
    suffixes: tuple[str, ...],
    count: int,
    used_prefixes: set[str],
) -> list[str]:
    """Generate *count* unique '{prefix} {suffix}' names with unique prefixes."""
    names: list[str] = []
    for _ in range(count * 16):
        if len(names) >= count:
            break
        prefix = prefixes[int(rng.integers(len(prefixes)))]
        if prefix in used_prefixes:
            continue
        suffix = suffixes[int(rng.integers(len(suffixes)))]
        used_prefixes.add(prefix)
        names.append(f"{prefix} {suffix}")
    fallback_idx = 0
    while len(names) < count:
        fallback_idx += 1
        name = f"Entity {fallback_idx}"
        if name not in set(names):
            names.append(name)
    return names


def infer_domain_from_blueprint(blueprint: ScenarioBlueprint) -> ProductDomain | None:
    product_code = blueprint.product.code
    for domain in PRODUCT_DOMAINS.values():
        if any(product_code.endswith(template.code) for template in domain.product_templates):
            return domain
    return None


def _adjacent_nouns_for_domain(domain: ProductDomain | None) -> tuple[str, ...]:
    if domain is None:
        return _ADJACENT_PRODUCT_NOUNS
    domain_nouns = tuple(noun for noun in domain.adjacent_product_nouns if noun)
    return domain_nouns or _ADJACENT_PRODUCT_NOUNS


def inject_adjacent_data(
    *,
    scenario,
    scenario_number: int,
    seed: int,
    domain: ProductDomain | None,
    name_prefixes: tuple[str, ...],
    code_namespace_prefix_for_channel: Callable[[int, int], str],
    ref_namespace_prefix_for_channel: Callable[[int, int], str],
) -> None:
    """Inject deterministic domain-adjacent records that are unrelated to task scoring."""
    rng = _np.random.default_rng(seed ^ 0xA5A5_A5A5_A5A5_A5A5)
    item_count = 50

    vendor_refs = {vendor.ref for vendor in scenario.vendors}
    customer_refs = {customer.ref for customer in scenario.customers}
    product_codes = {product.code for product in scenario.products}
    existing_product_names = {product.name for product in scenario.products}

    used_prefixes: set[str] = set()
    for v in scenario.vendors:
        used_prefixes.add(v.name.split()[0])
    for c in scenario.customers:
        used_prefixes.add(c.name.split()[0])

    adjacent_ref_prefix = ref_namespace_prefix_for_channel(scenario_number, 1)
    adjacent_code_prefix = code_namespace_prefix_for_channel(scenario_number, 1)
    vendor_ref_base = f"{adjacent_ref_prefix}q"
    customer_ref_base = f"{adjacent_ref_prefix}p"
    product_code_base = f"{adjacent_code_prefix}A"

    vendor_ref_idx = 1
    while f"{vendor_ref_base}{vendor_ref_idx:02d}" in vendor_refs:
        vendor_ref_idx += 1
    customer_ref_idx = 1
    while f"{customer_ref_base}{customer_ref_idx:02d}" in customer_refs:
        customer_ref_idx += 1
    product_code_idx = 1
    while f"{product_code_base}{product_code_idx:03d}" in product_codes:
        product_code_idx += 1

    used_product_prefixes: set[str] = set()
    for name in existing_product_names:
        used_product_prefixes.add(name.split()[0])
    vendor_names = _unique_combo_names(
        rng,
        name_prefixes,
        _ADJACENT_VENDOR_SUFFIXES,
        item_count,
        used_prefixes,
    )
    product_names = _unique_combo_names(
        rng,
        _ADJACENT_PRODUCT_ADJECTIVES,
        _adjacent_nouns_for_domain(domain),
        item_count,
        used_product_prefixes,
    )

    customer_names: list[str] = []
    customer_slugs: list[str] = []
    for _ in range(item_count * 16):
        if len(customer_names) >= item_count:
            break
        prefix = name_prefixes[int(rng.integers(len(name_prefixes)))]
        if prefix in used_prefixes:
            continue
        suffix = _ADJACENT_CUSTOMER_SUFFIXES[int(rng.integers(len(_ADJACENT_CUSTOMER_SUFFIXES)))]
        division = _ADJACENT_CUSTOMER_DIVISIONS[
            int(rng.integers(len(_ADJACENT_CUSTOMER_DIVISIONS)))
        ]
        full = f"{prefix} {suffix} {division}"
        used_prefixes.add(prefix)
        customer_names.append(full)
        customer_slugs.append(f"{prefix}{suffix}".lower().replace(" ", "").replace("&", ""))
    fallback_idx = 1
    while len(customer_names) < item_count:
        fallback_slug = f"adjacentcustomer{fallback_idx:03d}"
        fallback_name = f"Adjacent Customer {fallback_idx} Division"
        if fallback_slug not in customer_slugs:
            customer_names.append(fallback_name)
            customer_slugs.append(fallback_slug)
        fallback_idx += 1

    vendor_refs_added: list[str] = []
    for idx in range(item_count):
        vendor_ref = f"{vendor_ref_base}{vendor_ref_idx + idx:02d}"
        vendor_refs_added.append(vendor_ref)
        scenario.vendors.append(
            VendorData(
                name=vendor_names[idx],
                ref=vendor_ref,
                supplier_rank=int(rng.integers(1, 15)),
            )
        )

    for idx in range(item_count):
        customer_ref = f"{customer_ref_base}{customer_ref_idx + idx:02d}"
        email_domain = _ADJACENT_EMAIL_DOMAINS[int(rng.integers(len(_ADJACENT_EMAIL_DOMAINS)))]
        scenario.customers.append(
            CustomerData(
                name=customer_names[idx],
                ref=customer_ref,
                email=f"contact@{customer_slugs[idx]}.{email_domain}",
                is_company=True,
                comment=_ADJACENT_CUSTOMER_COMMENTS[
                    int(rng.integers(len(_ADJACENT_CUSTOMER_COMMENTS)))
                ],
                credit_limit=None,
                payment_term=None,
                tags=list(_ADJACENT_CUSTOMER_TAGS[int(rng.integers(len(_ADJACENT_CUSTOMER_TAGS)))]),
            )
        )

    for idx in range(item_count):
        product_code = f"{product_code_base}{product_code_idx + idx:03d}"
        vendor_ref = vendor_refs_added[idx % len(vendor_refs_added)]
        list_price = round(float(rng.uniform(180.0, 420.0)), 2)
        standard_price = round(float(list_price * rng.uniform(0.52, 0.72)), 2)
        vendor_price = round(float(standard_price * rng.uniform(0.95, 1.08)), 2)
        stock_qty = float(int(rng.integers(4, 16)))
        scenario.products.append(
            ProductData(
                name=product_names[idx],
                code=product_code,
                category=_ADJACENT_PRODUCT_CATEGORIES[
                    int(rng.integers(len(_ADJACENT_PRODUCT_CATEGORIES)))
                ],
                type="consu",
                list_price=list_price,
                standard_price=standard_price,
                routes=["buy"],
                vendor_info=[
                    VendorInfoData(
                        partner_ref=vendor_ref,
                        delay=int(rng.integers(3, 9)),
                        min_qty=1.0,
                        max_qty=24.0,
                        price=vendor_price,
                        name=_ADJACENT_VENDOR_INFO_NAMES[
                            int(rng.integers(len(_ADJACENT_VENDOR_INFO_NAMES)))
                        ],
                    )
                ],
            )
        )
        scenario.stock_levels.append(
            StockLevelData(
                product_code=product_code,
                warehouse_code=None,
                quantity=stock_qty,
                location_type="stock",
            )
        )

    adjacent_codes = [f"{product_code_base}{product_code_idx + i:03d}" for i in range(item_count)]
    bom_count = max(1, item_count // 5)
    bom_indices = list(range(item_count))
    rng.shuffle(bom_indices)
    for bi in bom_indices[:bom_count]:
        finished_code = adjacent_codes[bi]
        n_comps = int(rng.integers(1, 3))
        candidates = [code for code in adjacent_codes if code != finished_code]
        comp_idx = list(range(len(candidates)))
        rng.shuffle(comp_idx)
        components = [
            BOMComponentData(
                component_code=candidates[comp_idx[j]],
                quantity=float(int(rng.integers(1, 4))),
            )
            for j in range(min(n_comps, len(candidates)))
        ]
        scenario.boms.append(
            BOMData(
                product_code=finished_code,
                components=components,
                warehouse_code=None,
            )
        )
