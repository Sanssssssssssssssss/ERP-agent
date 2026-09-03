Handle the open CO2 Total Flooding System order queue.

- Stratos Studios: 18 units due in 8 days (pretax budget cap $189,038)
- Granite Publishing: 19 units due in 8 days (pretax budget cap $199,663)
- Globe Works: 19 units due in 8 days (pretax budget cap $202,831)
- Slate Innovations: 21 units due in 8 days (pretax budget cap $232,472)
- Pacific Collective East: 19 units due in 8 days (pretax budget cap $196,667)
- Velocity Collective: 21 units due in 8 days (pretax budget cap $226,922)
- Haven Studios North: 32 units due in 9 days (pretax budget cap $361,196)
- Cedar Foundry: 20 units due in 9 days (pretax budget cap $212,082)
- Spectra Society: 25 units due in 9 days (pretax budget cap $268,610)
- Spire Atelier: 18 units due in 9 days (pretax budget cap $188,501)
- Marble Gallery: 32 units due in 10 days (pretax budget cap $331,006)
- Vantage Guild: 21 units due in 10 days (pretax budget cap $227,442)
- Sentinel Holdings: 21 units due in 10 days (pretax budget cap $226,963)
- Comet Pictures: 25 units due in 11 days (pretax budget cap $281,560)
- Lakewood Manufactory: 20 units due in 11 days (pretax budget cap $207,465)
- Baltic Studios West: 23 units due in 11 days (pretax budget cap $255,399)
- Solace Archive: 32 units due in 11 days (pretax budget cap $359,243)
- Arbor Group: 29 units due in 11 days (pretax budget cap $300,775)
- Alloy Bazaar: 32 units due in 12 days (pretax budget cap $351,708)
- Ridge Council: 32 units due in 13 days (pretax budget cap $333,337)
- Pivot Observatory: 32 units due in 13 days (pretax budget cap $353,440)
- Lattice Trading Co: 32 units due in 13 days (pretax budget cap $340,517)
- Conduit Bureau: 32 units due in 13 days (pretax budget cap $338,577)

On-hand finished stock covers 230 units. If stock runs short, you can close the gap with finished-goods purchasing, in-house manufacturing, or a combination of the two.

Across all units covered through new purchasing or manufacturing, keep portfolio-level new-spend margin at or above 29% at selling price.

## Background & Policy

* Customer order acceptance is based on the following criteria.
* Accepted orders must allow at least 9 days of lead time.
* Reject or cancel any order that fails those acceptance rules.
* Arrange supply for eligible orders, ensuring the least new investment.
* Across all units covered through new purchasing or manufacturing, keep portfolio-level new-spend margin at or above 29% at selling price.
* Finance considers the existing stock as sunk cost.
* Some sales documents may already exist in draft; review existing documents before creating new ones.
* Cancel any already-drafted sales order that fails those acceptance rules.
* Customer budgets are pre-tax amounts.
* Link every accepted Sales Order to the related Manufacturing Orders and Purchase Orders for traceability.
* For finished goods POs, put the SO reference(s) (e.g. S00030 or S00030, S00031) into the origin field ('Source' in the UI).
* For component POs, put the MO reference(s) (e.g. WH/MO/00010 or WH/MO/00010, WH/MO/00011) into the origin field.
* For finished goods MOs, put the Sales Order reference (e.g. S00030) into the origin field ('Source' in the UI).
* For subassembly or intermediate MOs, put the immediate parent MO reference(s) that the subassembly feeds (e.g. WH/MO/00020 or WH/MO/00020, WH/MO/00021) into the origin field ('Source' in the UI).
* In the end, the lineage must be SO -> MO -> (Subassembly MO if needed) -> PO or SO -> PO for every incoming customer order you accept.
* You must sell this product at List Price.
* On every accepted Sales Order, set the commitment date.
* On manufacturing orders, you must set the start date and the due date.
* If you choose to manufacture, you must procure the components that are not in stock.
* On purchase orders, you must set the delivery date.
* Check Internal Notes/comments on stock, customers, vendors, and workcenters before you release anything.

Fulfillment constraints for accepted orders:
- After an order is accepted, treat workcenter capacity as a hard horizon-wide limit across all products that share the center.
- Check each workcenter's Internal Notes in Odoo for the exact horizon-wide minute limit.
- Assign workcenter on each manufacturing work order.
- For each finished-goods supplier offer used to fulfill accepted orders, respect min/max quantities as horizon-wide totals.
- For each component supplier offer used to fulfill accepted orders, respect min/max quantities as horizon-wide totals.
- Use one consolidated PO per supplier offer when fulfilling accepted orders (do not split a single offer across multiple POs).
- Check each vendor's Internal Notes in Odoo for fulfillment-side maximum order quantity limits.

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
