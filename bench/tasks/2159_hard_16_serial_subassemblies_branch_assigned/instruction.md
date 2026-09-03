All orders below for Duplex Compressor System 30HP need confirmed supply coverage and scheduling.

- Spark Studios North: 23 units due in 8 days (pretax budget cap $341,466)
- Matrix Workshop: 19 units due in 8 days (pretax budget cap $284,235)
- Blaze Forum: 18 units due in 9 days (pretax budget cap $259,393)
- Monarch Research: 18 units due in 9 days (pretax budget cap $270,856)
- Eclipse Creative: 22 units due in 10 days (pretax budget cap $344,057)
- Aurora Refinery: 19 units due in 10 days (pretax budget cap $279,888)
- Marble Exchange: 28 units due in 10 days (pretax budget cap $418,997)
- Baltic Society: 21 units due in 10 days (pretax budget cap $319,447)
- Horizon Greenhouse: 29 units due in 11 days (pretax budget cap $447,059)
- Catalyst Technologies: 28 units due in 11 days (pretax budget cap $410,194)
- Alloy Cooperative: 21 units due in 11 days (pretax budget cap $317,700)
- Forge Agency: 20 units due in 11 days (pretax budget cap $289,175)
- Drift Hub: 27 units due in 11 days (pretax budget cap $408,546)
- Garnet Pavilion: 21 units due in 11 days (pretax budget cap $313,860)
- Nexus Bureau: 32 units due in 11 days (pretax budget cap $463,636)
- Ridgeline Works: 25 units due in 11 days (pretax budget cap $381,893)
- Anvil Clinics: 20 units due in 12 days (pretax budget cap $310,319)
- Alpine Archive: 18 units due in 12 days (pretax budget cap $262,496)
- Element Interactive: 29 units due in 12 days (pretax budget cap $421,073)
- Nimbus Foundry: 20 units due in 12 days (pretax budget cap $309,748)
- Zenith Reserve: 21 units due in 12 days (pretax budget cap $316,613)
- Bronze Media: 26 units due in 12 days (pretax budget cap $399,628)
- Chrome Boutique: 24 units due in 12 days (pretax budget cap $372,534)
- Oxide Collective: 32 units due in 12 days (pretax budget cap $478,017)
- Noble Dynamics: 31 units due in 13 days (pretax budget cap $461,306)
- Aegis Publishing: 32 units due in 13 days (pretax budget cap $498,576)

Current finished-goods stock is 250 units. Any shortfall can be handled with finished-goods buying, in-house manufacturing, or a mix that still satisfies policy.

Across all units covered through new purchasing or manufacturing, keep portfolio-level new-spend margin at or above 27.1% at selling price.

## Background & Policy

* Cover every customer order while using as little shared workcenter capacity as practical. If multiple feasible plans use the same amount of workcenter capacity, keep new purchasing and manufacturing spend as low as possible.
* The combined units covered through new purchasing or manufacturing must clear at least 27.1% portfolio-level new-spend margin at selling price.
* Available finished stock can be used where it helps keep shared workcenter capacity open.
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
