"""Persistent, isolated Odoo demo. Existing benchmark and development databases are untouched."""

import json
import secrets
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUN = ROOT / ".runtime/demo-odoo"
COMPOSE = Path(__file__).with_name("compose.yml")
DATABASE = "erp_harness_demo"


def linux(path):
    return "/mnt/" + path.drive[0].lower() + "/" + path.as_posix().split(":/", 1)[1]


def compose(*args, **kwargs):
    return subprocess.run(
        [
            "wsl",
            "-d",
            "Ubuntu",
            "--",
            "docker",
            "compose",
            "--env-file",
            linux(RUN / ".env"),
            "-f",
            linux(COMPOSE),
            *args,
        ],
        check=True,
        **kwargs,
    )


SEED = """
import json
from datetime import datetime, timedelta
assert env.cr.dbname == 'erp_harness_demo'
marker = env['ir.config_parameter'].get_param('erp_harness.demo_seed')
if not marker:
    env.company.write({'name': 'DEMO - Harbor Components'})
    customer = env['res.partner'].create({'name': 'DEMO - Northstar Retail', 'is_company': True, 'customer_rank': 1})
    vendor = env['res.partner'].create({'name': 'DEMO - Harbor Supply', 'is_company': True, 'supplier_rank': 1})
    products = env['product.product']
    for code, name, price, cost in [('DEMO-VALVE', 'DEMO - Control valve', 80, 48), ('DEMO-SENSOR', 'DEMO - Temperature sensor', 120, 70), ('DEMO-BRACKET', 'DEMO - Mounting bracket', 20, 9)]:
        product = env['product.product'].create({'name': name, 'default_code': code, 'type': 'consu', 'is_storable': True, 'list_price': price, 'standard_price': cost})
        env['product.supplierinfo'].create({'partner_id': vendor.id, 'product_tmpl_id': product.product_tmpl_id.id, 'min_qty': 1, 'price': cost, 'delay': 3})
        location = env.ref('stock.stock_location_stock')
        env['stock.quant']._update_available_quantity(product, location, 30)
        products |= product
    sale = env['sale.order'].create({'partner_id': customer.id, 'client_order_ref': 'DEMO-FIRST-SALE', 'order_line': [(0, 0, {'product_id': products[0].id, 'product_uom_qty': 5})]})
    purchase = env['purchase.order'].create({'partner_id': vendor.id, 'partner_ref': 'DEMO-FIRST-PURCHASE', 'order_line': [(0, 0, {'product_id': products[1].id, 'product_qty': 10, 'price_unit': 70})]})
    env['ir.config_parameter'].set_param('erp_harness.demo_seed', '1')
    admin = env.ref('base.user_admin')
    admin.write({'password': ADMIN_PASSWORD})
    key = env['res.users.apikeys'].with_user(admin)._generate('rpc', 'ERP Harness local demo', datetime.now() + timedelta(days=90))
    env.cr.commit()
    print('DEMO_CONNECTION=' + json.dumps({'ODOO_URL': 'http://127.0.0.1:18069', 'ODOO_DB': env.cr.dbname, 'ODOO_USERNAME': admin.login, 'ODOO_API_KEY': key, 'ODOO_PASSWORD': key, 'ODOO_TRANSPORT': 'json2'}))
print('DEMO_COUNTS=' + json.dumps({m: env[m].search_count([]) for m in ['res.partner', 'product.product', 'sale.order', 'purchase.order', 'stock.quant']}))
"""


def main():
    RUN.mkdir(parents=True, exist_ok=True)
    envfile = RUN / ".env"
    if not envfile.exists():
        envfile.write_text("DEMO_DB_PASSWORD=" + secrets.token_urlsafe(32) + "\n", encoding="utf-8")
    with (RUN / "bootstrap.log").open("a", encoding="utf-8") as log:
        compose("up", "-d", "db", "--wait", stdout=log, stderr=log)
        check = compose(
            "exec",
            "-T",
            "db",
            "psql",
            "-U",
            "odoo",
            "-d",
            "postgres",
            "-Atc",
            "SELECT 1 FROM pg_database WHERE datname='erp_harness_demo'",
            capture_output=True,
            text=True,
        )
        if not check.stdout.strip():
            compose(
                "run",
                "--rm",
                "-T",
                "odoo",
                "odoo",
                "-d",
                DATABASE,
                "--init=base,sale_management,purchase,stock,mrp,l10n_us",
                "--without-demo=all",
                "--stop-after-init",
                "--no-http",
                stdout=log,
                stderr=log,
            )
        password_file = RUN / "admin-password.txt"
        if not password_file.exists():
            password_file.write_text(secrets.token_urlsafe(24), encoding="utf-8")
        seed = "ADMIN_PASSWORD=" + repr(password_file.read_text(encoding="utf-8")) + "\n" + SEED
        result = compose(
            "run",
            "--rm",
            "-T",
            "odoo",
            "odoo",
            "shell",
            "-d",
            DATABASE,
            "--no-http",
            input=seed,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        for line in result.stdout.splitlines():
            if line.startswith("DEMO_CONNECTION="):
                (RUN / "connection.json").write_text(
                    json.dumps(json.loads(line.split("=", 1)[1]), indent=2), encoding="utf-8"
                )
            elif line.startswith("DEMO_COUNTS="):
                (RUN / "counts.json").write_text(line.split("=", 1)[1], encoding="utf-8")
                print(line)
        compose("up", "-d", "odoo", stdout=log, stderr=log)
    print("Persistent demo ready: http://127.0.0.1:18069 ; credentials: " + str(RUN))


if __name__ == "__main__":
    main()
