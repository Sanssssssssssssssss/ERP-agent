Address the current order backlog for Kitchen Hood Suppression Unit.

- Comet Enterprises: 32 units due in 8 days (pretax budget cap $109,906)
- Noble Atelier: 18 units due in 8 days (pretax budget cap $59,244)
- Cosmo Initiative: 22 units due in 8 days (pretax budget cap $78,015)
- Skyline Workspaces: 18 units due in 8 days (pretax budget cap $62,400)
- Blaze Collective: 26 units due in 8 days (pretax budget cap $92,593)
- Conduit Alliance: 20 units due in 8 days (pretax budget cap $67,591)
- Vantage Trust: 20 units due in 8 days (pretax budget cap $68,086)
- Catalyst Publishing: 18 units due in 9 days (pretax budget cap $61,158)
- Hartland Lyceum: 18 units due in 9 days (pretax budget cap $63,256)
- Pivot Cooperative: 19 units due in 9 days (pretax budget cap $68,085)
- Cipher Bureau: 22 units due in 9 days (pretax budget cap $77,501)
- Monarch Works: 18 units due in 9 days (pretax budget cap $60,217)
- Bayshore Innovations: 18 units due in 10 days (pretax budget cap $59,320)
- Onyx Ventures: 31 units due in 10 days (pretax budget cap $109,906)
- Lakewood Studios: 23 units due in 10 days (pretax budget cap $81,969)
- Flux Institute: 18 units due in 10 days (pretax budget cap $64,029)
- Forge Observatory: 32 units due in 10 days (pretax budget cap $114,905)
- Haven Hub: 28 units due in 10 days (pretax budget cap $100,377)
- Opal Manufactory: 24 units due in 10 days (pretax budget cap $81,258)
- Crestview Exchange: 19 units due in 11 days (pretax budget cap $64,466)
- Aegis Gallery: 19 units due in 11 days (pretax budget cap $62,793)
- Bronze Group: 18 units due in 11 days (pretax budget cap $62,504)
- Thornton Robotics: 25 units due in 12 days (pretax budget cap $83,226)
- Riverdale Theater: 19 units due in 12 days (pretax budget cap $68,445)
- Beacon Research: 31 units due in 12 days (pretax budget cap $103,918)
- Velocity Chambers: 18 units due in 12 days (pretax budget cap $64,281)
- Northbridge Arena: 22 units due in 13 days (pretax budget cap $78,025)
- Orbital Engineering: 32 units due in 13 days (pretax budget cap $106,549)
- Vertex Advisory: 28 units due in 13 days (pretax budget cap $92,710)
- Copper Designs: 25 units due in 13 days (pretax budget cap $89,403)
- Silverline Bazaar: 32 units due in 13 days (pretax budget cap $108,722)

We have 286 finished units on hand. Any shortfall can be handled with finished-goods buying, in-house manufacturing, or a mix that still satisfies policy.

Across all units covered through new purchasing or manufacturing, keep portfolio-level new-spend margin at or above 27.7% at selling price.

## Background & Policy

* These are the rules for customer order acceptance.
* Accepted orders must allow at least 9 days of lead time.
* Reject or cancel any order that fails those acceptance rules.
* For all accepted orders, arrange fulfillment at the lowest possible new purchase or manufacturing cost.
* The combined units covered through new purchasing or manufacturing must clear at least 27.7% portfolio-level new-spend margin at selling price.
* Existing stock is a sunk cost and should not be treated as new spend.
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
