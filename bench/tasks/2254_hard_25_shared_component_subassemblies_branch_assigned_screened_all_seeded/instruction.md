Work through the current order queue for Dry Pipe Sprinkler System.

- Baseline Studios West: 32 units due in 8 days (pretax budget cap $191,875)
- Sierra Ventures: 19 units due in 8 days (pretax budget cap $105,195)
- Raven Solutions Group: 18 units due in 8 days (pretax budget cap $103,720)
- Matrix Technologies: 19 units due in 8 days (pretax budget cap $111,892)
- Onyx Architects: 18 units due in 8 days (pretax budget cap $101,914)
- Lakewood Office: 18 units due in 9 days (pretax budget cap $106,772)
- Indigo Workspaces: 27 units due in 9 days (pretax budget cap $160,846)
- Citadel Theater: 20 units due in 9 days (pretax budget cap $120,526)
- Crown Labs: 19 units due in 9 days (pretax budget cap $111,744)
- Spectra Advisory: 23 units due in 9 days (pretax budget cap $126,953)
- Bronze Partners: 25 units due in 9 days (pretax budget cap $141,497)
- Echo Reserve: 26 units due in 9 days (pretax budget cap $143,923)
- Meridian Initiative: 18 units due in 9 days (pretax budget cap $102,706)
- Anvil Studios South: 18 units due in 10 days (pretax budget cap $101,936)
- Evergreen Designs: 23 units due in 10 days (pretax budget cap $132,794)
- Quantum Trading Co: 25 units due in 10 days (pretax budget cap $150,320)
- Nexus Refinery: 28 units due in 10 days (pretax budget cap $161,372)
- Coral Collective East: 18 units due in 10 days (pretax budget cap $102,121)
- Summit Innovations: 20 units due in 10 days (pretax budget cap $117,740)
- Cobalt Pictures: 24 units due in 10 days (pretax budget cap $142,000)
- Arrow Exchange: 22 units due in 10 days (pretax budget cap $126,100)
- Spire Studios East: 19 units due in 11 days (pretax budget cap $105,490)
- Eclipse Forum: 19 units due in 11 days (pretax budget cap $109,021)
- Vantage Collective: 22 units due in 11 days (pretax budget cap $126,350)
- Sapphire Studios North: 19 units due in 12 days (pretax budget cap $106,682)
- Element Bureau: 18 units due in 12 days (pretax budget cap $105,409)
- Clearwater Sciences: 31 units due in 12 days (pretax budget cap $179,054)
- Monarch Museum: 27 units due in 13 days (pretax budget cap $152,870)
- Ridgeline Cooperative: 22 units due in 13 days (pretax budget cap $122,514)
- Ironwood Consortium: 23 units due in 13 days (pretax budget cap $133,267)

We have 264 finished units on hand. Any shortfall can be handled with finished-goods buying, in-house manufacturing, or a mix that still satisfies policy.

Across all units covered through new purchasing or manufacturing, keep portfolio-level new-spend margin at or above 24% at selling price.

## Background & Policy

* The rules for which orders can be accepted are outlined below.
* Accepted orders must allow at least 13 days of lead time.
* Reject or cancel any order that fails those acceptance rules.
* Approved orders should be satisfied with the minimum necessary new spend.
* Across all units covered through new purchasing or manufacturing, keep portfolio-level new-spend margin at or above 24% at selling price.
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
* Review Internal Notes/comments on stock, customers, vendors, and workcenters before you confirm entries in the ERP.

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
