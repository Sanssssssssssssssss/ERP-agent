All orders below for Portable Power Station 3kWh need confirmed supply coverage and scheduling.

- Marble Observatory: 19 units due in 8 days (pretax budget cap $58,130)
- Forge Dynamics: 18 units due in 8 days (pretax budget cap $57,406)
- Aurora Collective East: 18 units due in 8 days (pretax budget cap $54,431)
- Ember Arena: 18 units due in 8 days (pretax budget cap $55,690)
- Crown Works: 21 units due in 8 days (pretax budget cap $65,303)
- Hartland Theater: 18 units due in 8 days (pretax budget cap $55,782)
- Haven Productions: 19 units due in 8 days (pretax budget cap $57,965)
- Monarch Reserve: 20 units due in 8 days (pretax budget cap $60,246)
- Metro Atelier: 24 units due in 9 days (pretax budget cap $76,407)
- Meridian Publishing: 18 units due in 9 days (pretax budget cap $52,527)
- Summit Advisory: 21 units due in 9 days (pretax budget cap $63,638)
- Oxide Studios North: 18 units due in 9 days (pretax budget cap $55,107)
- Oakmont Workshop: 23 units due in 10 days (pretax budget cap $67,454)
- Flint Solutions Group: 20 units due in 10 days (pretax budget cap $64,080)
- Helix Studios West: 27 units due in 10 days (pretax budget cap $79,359)
- Sterling Pavilion: 19 units due in 11 days (pretax budget cap $56,080)
- Quantum Architects: 18 units due in 11 days (pretax budget cap $54,496)
- Cardinal Trading Co: 18 units due in 11 days (pretax budget cap $53,629)
- Aegis Society: 22 units due in 11 days (pretax budget cap $68,840)
- Ironside Alliance: 20 units due in 12 days (pretax budget cap $63,101)
- Northbridge Manufactory: 21 units due in 12 days (pretax budget cap $64,478)
- Crest Sciences: 18 units due in 13 days (pretax budget cap $54,838)
- Baltic Consortium: 23 units due in 13 days (pretax budget cap $67,443)
- Trident Technologies: 21 units due in 13 days (pretax budget cap $62,643)
- Axis Partners: 19 units due in 13 days (pretax budget cap $57,175)
- Scion Museum: 19 units due in 13 days (pretax budget cap $56,782)

On-hand finished stock covers 208 units. If stock runs short, you can close the gap with finished-goods purchasing, in-house manufacturing, or a combination of the two.

Across all units covered through new purchasing or manufacturing, keep portfolio-level new-spend margin at or above 26.9% at selling price.

## Background & Policy

* Fulfill all customer orders while preserving as much shared workcenter capacity as possible for other scheduled work. If more than one feasible plan uses the same amount of workcenter capacity, keep new purchasing and manufacturing spend as low as possible.
* The combined units covered through new purchasing or manufacturing must clear at least 26.9% portfolio-level new-spend margin at selling price.
* Use available finished stock where it helps preserve shared workcenter capacity.
* You must create and confirm the necessary sales orders, purchase orders, and/or manufacturing orders.
* Customer budgets are pre-tax amounts.
* Link Sales Orders to the related Manufacturing Orders and Purchase Orders for traceability.
* For finished goods POs, put the SO reference(s) (e.g. S00030 or S00030, S00031) into the origin field ('Source' in the UI).
* For component POs, put the MO reference(s) (e.g. WH/MO/00010 or WH/MO/00010, WH/MO/00011) into the origin field.
* For finished goods MOs, put the Sales Order reference (e.g. S00030) into the origin field ('Source' in the UI).
* For subassembly or intermediate MOs, put the immediate parent MO reference(s) that the subassembly feeds (e.g. WH/MO/00020 or WH/MO/00020, WH/MO/00021) into the origin field ('Source' in the UI).
* In the end, the lineage must be SO -> MO -> (Subassembly MO if needed) -> PO or SO -> PO.
* You must sell this product at List Price.
* On sales orders, set the commitment date.
* On manufacturing orders, you must set the start date and the due date.
* If you choose to manufacture, you must procure the components that are not in stock.
* On purchase orders, you must set the delivery date.
* Check Internal Notes/comments on stock, customers, vendors, and workcenters before you release anything.

Capacity constraints:
- Treat workcenter capacity as a hard horizon-wide limit across all products that share the center.
- Check each workcenter's Internal Notes in Odoo for the exact horizon-wide minute limit.
- Assign workcenter on each manufacturing work order.
- For each finished-goods supplier offer, respect min/max quantities as horizon-wide totals.
- For each component supplier offer, respect min/max quantities as horizon-wide totals.
- Use one consolidated PO per supplier offer (do not split a single offer across multiple POs).
- Check each vendor's Internal Notes in Odoo for maximum order quantity limits.

## Execution Autonomy

Work with full autonomy. Do not ask the user for confirmation, approval, or preferences before acting. Make the best valid plan from the available Odoo data and execute it completely in Odoo. If multiple feasible plans satisfy the constraints, choose the one that best optimizes the stated objective.

---

# Odoo Environment

You have access to an Odoo ERP instance with the following credentials:

- **Host:** 127.0.0.1
- **Port:** 8069
- **Database:** bench
- **API Key:** stored in `/etc/odoo/api_key`

## How to Access Odoo

You can write and execute Python scripts using the `odoo-client-lib` library to interact with Odoo via the JSON-2 API:

```python
import odoolib

# Connect to Odoo via JSON-2 API
connection = odoolib.get_connection(
    hostname='127.0.0.1', protocol='json2', port=8069,
    database='bench', password=open('/etc/odoo/api_key').read().strip()
)

# Get model proxies
Product = connection.get_model('product.product')
Partner = connection.get_model('res.partner')
SaleOrder = connection.get_model('sale.order')
SaleOrderLine = connection.get_model('sale.order.line')
PaymentTerm = connection.get_model('account.payment.term')
InvoiceWizard = connection.get_model('sale.advance.payment.inv')

# Example: Search for products (use named kwargs)
product_ids = Product.search(domain=[('default_code', '=', 'SPP-001')])
if product_ids:
    data = Product.read(ids=product_ids, fields=['name', 'qty_available', 'list_price'])
    for p in (data if isinstance(data, list) else [data]):
        print(f"Product: {p['name']}, Stock: {p['qty_available']}, Price: ${p['list_price']}")

# Example: Create a sale order
customer_ids = Partner.search(domain=[('ref', '=', 'customer_001')])
so_ids = SaleOrder.create(vals_list=[{
    'partner_id': customer_ids[0],
    'note': 'Urgent order'
}])
so_id = so_ids[0]

SaleOrderLine.create(vals_list=[{
    'order_id': so_id,
    'product_id': product_ids[0],
    'product_uom_qty': 25,
    'price_unit': 450.00
}])

# Example: Confirm sale order
SaleOrder.action_confirm(ids=[so_id])
```

## Common Odoo Models

- `res.partner` - Customers, vendors, contacts
- `product.product` - Products
- `product.supplierinfo` - Vendor pricing and lead times (`min_qty`, `price`, `delay`); **no `max_qty` field exists** — read vendor capacity limits from `res.partner.comment` (Internal Notes) instead
- `sale.order` / `sale.order.line` - Sales orders
- `account.payment.term` - Payment terms
- `sale.advance.payment.inv` - Sales invoice wizard (regular invoices and down payments)
- `purchase.order` / `purchase.order.line` - Purchase orders
- `account.move` - Invoices and bills
- `stock.move` - Inventory movements
- `stock.picking` - Deliveries and receipts
- `mrp.production` - Manufacturing orders
- `mrp.workorder` - Manufacturing work orders and workcenter assignments
