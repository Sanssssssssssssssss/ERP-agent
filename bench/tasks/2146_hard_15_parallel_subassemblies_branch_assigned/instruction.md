All orders below for Modular Rack Battery System 20kWh need confirmed supply coverage and scheduling.

- Beacon Arena: 20 units due in 8 days (pretax budget cap $352,056)
- Dune Enterprises: 19 units due in 8 days (pretax budget cap $327,706)
- Meridian Advisory: 18 units due in 8 days (pretax budget cap $303,628)
- Aether Consortium: 20 units due in 8 days (pretax budget cap $359,031)
- Sierra Clinics: 19 units due in 9 days (pretax budget cap $342,730)
- Axis Robotics: 20 units due in 9 days (pretax budget cap $359,410)
- Trident Research: 29 units due in 9 days (pretax budget cap $505,931)
- Westfield Archive: 18 units due in 9 days (pretax budget cap $314,483)
- Bridgeway Trading Co: 22 units due in 9 days (pretax budget cap $364,608)
- Quantum Initiative: 19 units due in 10 days (pretax budget cap $322,719)
- Catalyst Council: 20 units due in 10 days (pretax budget cap $352,781)
- Cipher Lyceum: 23 units due in 10 days (pretax budget cap $389,731)
- Blaze Greenhouse: 19 units due in 10 days (pretax budget cap $328,371)
- Peak Workspaces: 22 units due in 10 days (pretax budget cap $363,871)
- Ivory Studios North: 25 units due in 10 days (pretax budget cap $423,873)
- Hartland Theater: 18 units due in 11 days (pretax budget cap $311,063)
- Oakmont Society: 28 units due in 11 days (pretax budget cap $482,909)
- Citadel Studios: 32 units due in 11 days (pretax budget cap $556,306)
- Grove Bureau: 24 units due in 11 days (pretax budget cap $412,467)
- Gateway Institute: 31 units due in 11 days (pretax budget cap $519,842)
- Flux Chambers: 21 units due in 11 days (pretax budget cap $356,602)
- Arrow Agency: 21 units due in 12 days (pretax budget cap $356,370)
- Nexus Group: 25 units due in 12 days (pretax budget cap $416,876)
- Spark Museum: 32 units due in 12 days (pretax budget cap $543,868)
- Raven Studios West: 25 units due in 12 days (pretax budget cap $424,692)
- Quartz Pictures: 25 units due in 13 days (pretax budget cap $429,558)
- Metro Studios South: 20 units due in 13 days (pretax budget cap $334,544)
- Ridge Workshop: 18 units due in 13 days (pretax budget cap $300,811)
- Aegis Conservatory: 25 units due in 13 days (pretax budget cap $430,898)
- Cobalt Pavilion: 32 units due in 13 days (pretax budget cap $529,235)

We have 276 finished units on hand. If stock is not enough, you can cover the gap by buying finished goods, building in-house, or using a mix of both.

Across all units covered through new purchasing or manufacturing, keep portfolio-level new-spend margin at or above 25% at selling price.

## Background & Policy

* Cover every customer order while using as little shared workcenter capacity as practical. If multiple feasible plans use the same amount of workcenter capacity, keep new purchasing and manufacturing spend as low as possible.
* Any units you cover through new buying or manufacturing count toward one combined portfolio that must still meet a minimum 25% new-spend margin at selling price.
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
