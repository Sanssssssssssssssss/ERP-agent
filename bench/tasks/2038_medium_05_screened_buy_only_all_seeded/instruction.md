Handle the open Fume Hood Bench Station order queue.

- Stratos Agency: 24 units due in 8 days (pretax budget cap $63,612)
- Silverline Studios South: 20 units due in 9 days (pretax budget cap $57,260)
- Blaze Theater: 14 units due in 11 days (pretax budget cap $32,817)
- Nimbus Reserve: 16 units due in 11 days (pretax budget cap $43,612)
- Cedar Forum: 15 units due in 12 days (pretax budget cap $42,237)
- Haven Trading Co: 20 units due in 13 days (pretax budget cap $54,673)
- Granite Studios North: 26 units due in 13 days (pretax budget cap $71,059)
- Pinnacle Solutions Group: 17 units due in 14 days (pretax budget cap $47,709)

We have 66 finished units on hand. The finished product is not manufactured in-house, so any shortfall has to be sourced from vendors.

Across all units covered through new purchasing or manufacturing, keep portfolio-level new-spend margin at or above 26.4% at selling price.

## Background & Policy

* The rules for which orders can be accepted are outlined below.
* Only accept orders whose budgets cover the full list-price total.
* Accepted orders must request between 15 and 23 units and allow at least 13 days of lead time.
* Reject or cancel any order that fails those acceptance rules.
* Once orders are accepted, minimize new procurement or production spend while covering them.
* Any units you cover through new buying or manufacturing count toward one combined portfolio that must still meet a minimum 26.4% new-spend margin at selling price.
* Existing stock is a sunk cost and should not be treated as new spend.
* Some sales documents may already exist in draft; review existing documents before creating new ones.
* Cancel any already-drafted sales order that fails those acceptance rules.
* Customer budgets are pre-tax amounts.
* Link every accepted Sales Order to the related Purchase Orders for traceability.
* For finished goods POs, put the SO reference(s) (e.g. S00030 or S00030, S00031) into the origin field ('Source' in the UI).
* In the end, the lineage must be SO -> PO for every incoming customer order you accept.
* You must sell this product at List Price.
* On every accepted Sales Order, set the commitment date.
* On purchase orders, you must set the delivery date.
* Check Internal Notes/comments on stock, customers, and vendors before you release anything.

Fulfillment constraints for accepted orders:
- No in-house manufacturing capacity is available.
- For each finished-goods supplier offer used to fulfill accepted orders, respect min/max quantities as horizon-wide totals.
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
