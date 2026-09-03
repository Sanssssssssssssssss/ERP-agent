Existing confirmed sales orders and current manufacturing commitments for Ground-Mount Tracker System are already in Odoo. Assembly Line 1 is experiencing an equipment outage, and confirmed manufacturing work is already scheduled on that line. Reuse the existing sales orders, review the commitments already in place, cancel the affected manufacturing orders, keep unaffected work where it still makes sense, and adjust the plan for Flint Media, Sentinel Technologies, Arrow Advisory, Aether Interactive, Redstone Bureau, and Terra Trading Co using the best feasible alternative fulfillment route.

- Flint Media: 16 units due in 8 days (pretax budget cap $91,602)
- Spark Architects: 24 units due in 8 days (pretax budget cap $137,314)
- Sentinel Technologies: 16 units due in 9 days (pretax budget cap $85,908)
- Arrow Advisory: 16 units due in 9 days (pretax budget cap $84,552)
- Aether Interactive: 15 units due in 10 days (pretax budget cap $85,747)
- Redstone Bureau: 27 units due in 10 days (pretax budget cap $141,813)
- Haven Works: 19 units due in 11 days (pretax budget cap $103,241)
- Baltic Supply Co: 27 units due in 11 days (pretax budget cap $151,722)
- Terra Trading Co: 24 units due in 11 days (pretax budget cap $136,390)
- Ledger Academy: 20 units due in 12 days (pretax budget cap $111,829)
- Opal Dynamics: 27 units due in 12 days (pretax budget cap $146,932)

On-hand finished stock covers 64 units. If stock is not enough, you can cover the gap by buying finished goods, building in-house, or using a mix of both.

Across all units covered through new purchasing or manufacturing, keep portfolio-level new-spend margin at or above 29.7% at selling price.

## Background & Policy

* Adjust the existing supply plan with the least disruption to what is already committed. When more than one feasible option changes the plan by the same amount, keep new purchasing and manufacturing spend as low as possible.
* The combined units covered through new purchasing or manufacturing must clear at least 29.7% portfolio-level new-spend margin at selling price.
* Accounting treats the existing stock as sunk cost.
* The listed customer sales orders are already confirmed in Odoo; work from those orders and do not create duplicates.
* Review the current purchase orders, manufacturing orders, and supplier or workcenter notes before making changes.
* Keep commitments that still work; only rework the part of the plan affected by the disruption.
* Confirmed manufacturing work is already scheduled on Assembly Line 1, but that workcenter is experiencing an equipment outage.
* Cancel the affected manufacturing orders and cover the gap with the best feasible alternative fulfillment route without using that workcenter.
* Link every accepted Sales Order to the related Manufacturing Orders and Purchase Orders for traceability.
* For finished goods POs, put the SO reference(s) (e.g. S00030 or S00030, S00031) into the origin field ('Source' in the UI).
* For component POs, put the MO reference(s) (e.g. WH/MO/00010 or WH/MO/00010, WH/MO/00011) into the origin field.
* For finished goods MOs, put the Sales Order reference (e.g. S00030) into the origin field ('Source' in the UI).
* In the end, the lineage must be SO -> MO -> PO or SO -> PO for every incoming customer order you accept.
* You must sell this product at List Price.
* On every accepted Sales Order, set the commitment date.
* On manufacturing orders, you must set the start date and the due date.
* If you choose to manufacture, you must procure the components that are not in stock.
* On purchase orders, you must set the delivery date.
* Check Internal Notes/comments on stock, customers, vendors, and workcenters before you release anything.

Capacity constraints:
- Treat workcenter capacity as a hard horizon-wide limit across all products that share the center.
- Check each workcenter's Internal Notes in Odoo for the exact horizon-wide minute limit.
- Assign workcenter on each manufacturing work order.
- Assembly Line 1 is experiencing an equipment outage and must not be used.
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
