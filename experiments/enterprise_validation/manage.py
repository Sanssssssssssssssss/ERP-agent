"""Isolated enterprise fixture lifecycle. Credentials never enter tracked files."""

import argparse
import hashlib
import json
import re
import secrets
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
RUN = ROOT / ".runtime/enterprise-validation-20260922"
DATABASE = "erp_harness_enterprise_v1"
FILESTORE_RESET = """
from pathlib import Path
import shutil
target = Path('/var/lib/odoo/filestore/erp_harness_enterprise_v1')
if any(path.is_symlink() for path in (target, *target.parents)):
    raise RuntimeError('Refusing symlink in dedicated filestore path')
if target.resolve() != Path('/var/lib/odoo/filestore/erp_harness_enterprise_v1'):
    raise RuntimeError('Unexpected dedicated filestore path')
if target.exists():
    shutil.rmtree(target)
"""


def linux(path):
    return "/mnt/" + path.drive[0].lower() + "/" + path.as_posix().split(":/", 1)[1]


def compose(*args, **kwargs):
    return subprocess.run(
        ["wsl", "-d", "Ubuntu", "--", "docker", "compose", "--env-file", linux(RUN / ".env"),
         "-f", linux(HERE / "compose.yml"), *args], check=True, **kwargs,
    )


def database_exists():
    r = compose("exec", "-T", "db", "psql", "-U", "odoo", "-d", "postgres", "-Atc",
                f"SELECT 1 FROM pg_database WHERE datname='{DATABASE}'", capture_output=True, text=True)
    return bool(r.stdout.strip())


def shell(source):
    try:
        r = compose("run", "--rm", "-T", "odoo", "odoo", "shell", "-d", DATABASE, "--no-http",
                    input=source, capture_output=True, text=True, encoding="utf-8")
    except subprocess.CalledProcessError as error:
        public_output = "\n".join(line for line in (error.stdout or "").splitlines() if not line.startswith("ENTERPRISE_RESULT="))
        (RUN / "last-shell-error.log").write_text((error.stderr or "") + "\n" + public_output, encoding="utf-8")
        raise RuntimeError("Seed failed; inspect private last-shell-error.log") from None
    # Only structured receipts are public; startup logs can contain source locations.
    for line in r.stdout.splitlines():
        if line.startswith("ENTERPRISE_RESULT="):
            return json.loads(line.split("=", 1)[1])
    (RUN / "last-shell-error.log").write_text(r.stderr + "\n" + r.stdout, encoding="utf-8")
    raise RuntimeError("No seed receipt; inspect private last-shell-error.log")


def bootstrap(log):
    compose("up", "-d", "db", "--wait", stdout=log, stderr=log)
    if not database_exists():
        compose("run", "--rm", "-T", "odoo", "odoo", "-d", DATABASE,
                "--init=base,sale_management,purchase,stock,mrp,l10n_cn", "--without-demo=all",
                "--stop-after-init", "--no-http", "--load-language=zh_CN", stdout=log, stderr=log)


def seed(count, log):
    private = RUN / "accounts.json"
    accounts = json.loads(private.read_text(encoding="utf-8")) if private.exists() else {}
    for role in ["sales", "purchase", "warehouse", "production", "finance", "manager", "admin",
                 "trade_sales", "trade_purchase", "trade_warehouse", "trade_production", "trade_finance"]:
        accounts.setdefault(role, {"login": f"{role}@chengchuan.example", "password": secrets.token_urlsafe(24)})
    private.write_text(json.dumps(accounts, ensure_ascii=False, indent=2), encoding="utf-8")
    source = f"TARGET={count!r}\nACCOUNTS={accounts!r}\n" + (HERE / "seed.py").read_text(encoding="utf-8")
    result = shell(source)
    if "accounts" in result:
        private.write_text(json.dumps(result.pop("accounts"), ensure_ascii=False, indent=2), encoding="utf-8")
    if "connection" in result:
        (RUN / "connection.json").write_text(json.dumps(result.pop("connection"), indent=2), encoding="utf-8")
    (RUN / "fixtures.json").write_text(json.dumps(result.pop("fixtures"), ensure_ascii=False, indent=2), encoding="utf-8")
    (RUN / "seed-report.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    compose("up", "-d", "odoo", stdout=log, stderr=log)
    print(json.dumps(result, ensure_ascii=False))


def snapshot(name, restore, log):
    if not re.fullmatch(r"[a-zA-Z0-9_-]+", name):
        raise ValueError("Snapshot name must contain only letters, digits, underscore, hyphen")
    folder = (RUN / "snapshots" / name).resolve()
    assert folder.is_relative_to(RUN.resolve())
    dump = folder / "database.dump"
    archive = folder / "filestore.tar"
    if restore and (not dump.is_file() or not archive.is_file()):
        raise ValueError("Complete snapshot required")
    if not restore and folder.exists():
        raise ValueError("Snapshot already exists; use a new name")
    snapshot_files = ["database.dump", "filestore.tar", "fixtures.json", "seed-report.json", "accounts.json", "connection.json"]
    if restore:
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["database"] == DATABASE
        for filename in snapshot_files:
            with (folder / filename).open("rb") as f:
                assert hashlib.file_digest(f, "sha256").hexdigest() == manifest["sha256"][filename], "Snapshot integrity failure: " + filename
    compose("stop", "odoo", stdout=log, stderr=log)
    try:
        if restore:
            compose("exec", "-T", "db", "dropdb", "--force", "--if-exists", "-U", "odoo", DATABASE, stdout=log, stderr=log)
            compose("exec", "-T", "db", "createdb", "-U", "odoo", DATABASE, stdout=log, stderr=log)
            with dump.open("rb") as f:
                compose("exec", "-T", "db", "pg_restore", "--exit-on-error", "-U", "odoo", "-d", DATABASE, stdin=f, stdout=log, stderr=log)
            compose("run", "--rm", "-T", "--entrypoint", "python3", "odoo", "-c", FILESTORE_RESET, stdout=log, stderr=log)
            with archive.open("rb") as f:
                compose("run", "--rm", "-T", "--entrypoint", "tar", "odoo", "-xf", "-", "-C", "/var/lib/odoo", stdin=f, stdout=log, stderr=log)
            for filename in ["fixtures.json", "seed-report.json", "accounts.json", "connection.json"]:
                (RUN / filename).write_bytes((folder / filename).read_bytes())
        else:
            folder.mkdir(parents=True)
            with dump.open("wb") as f:
                compose("exec", "-T", "db", "pg_dump", "-Fc", "-U", "odoo", DATABASE, stdout=f, stderr=log)
            with archive.open("wb") as f:
                compose("run", "--rm", "-T", "--entrypoint", "tar", "odoo", "-cf", "-", "-C", "/var/lib/odoo", f"filestore/{DATABASE}", stdout=f, stderr=log)
            for filename in ["fixtures.json", "seed-report.json", "accounts.json", "connection.json"]:
                (folder / filename).write_bytes((RUN / filename).read_bytes())
            hashes = {}
            for filename in snapshot_files:
                with (folder / filename).open("rb") as f:
                    hashes[filename] = hashlib.file_digest(f, "sha256").hexdigest()
            (folder / "manifest.json").write_text(json.dumps({"database": DATABASE, "sha256": hashes}, indent=2), encoding="utf-8")
    finally:
        compose("up", "-d", "odoo", stdout=log, stderr=log)
    print(("Restored " if restore else "Saved ") + str(folder))


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["seed", "status", "snapshot", "restore"])
    parser.add_argument("--source-count", type=int, choices=[100, 10000], default=100)
    parser.add_argument("--name", default="baseline-100")
    args = parser.parse_args()
    RUN.mkdir(parents=True, exist_ok=True)
    if not (RUN / ".env").exists():
        (RUN / ".env").write_text("ENTERPRISE_DB_PASSWORD=cc_" + secrets.token_urlsafe(32) + "\n", encoding="utf-8")
    with (RUN / "manage.log").open("a", encoding="utf-8") as log:
        if args.command == "seed":
            bootstrap(log)
            seed(args.source_count, log)
        elif args.command == "status":
            compose("ps")
            if (RUN / "seed-report.json").exists():
                print((RUN / "seed-report.json").read_text(encoding="utf-8"))
        else:
            snapshot(args.name, args.command == "restore", log)


if __name__ == "__main__":
    main()
