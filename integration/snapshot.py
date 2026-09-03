"""Capture/restore a benchmark-only Odoo snapshot inside its disposable container.

No model calls. Restore refuses an existing database; raw files stay private.
The same manifest verifies public-table data, columns, sequences and filestore.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import subprocess
import tarfile
import time
from pathlib import Path

DATABASE = "bench"
CASE = "2262_easy_26_buy_only_net_30_no_adjacent_data"


def pg(*args: str, input: str | None = None, stdout=None, stdin=None):
    return subprocess.run(
        ["runuser", "-u", "postgres", "--", *args], input=input, stdin=stdin, text=True,
        stdout=stdout if stdout is not None else subprocess.PIPE,
        stderr=subprocess.PIPE, check=True,
    )


def sql(query: str, database: str = DATABASE) -> str:
    return pg("psql", "-XAt", "-v", "ON_ERROR_STOP=1", "-d", database,
              input="SET timezone='UTC';\n" + query).stdout.strip()


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def data_dir() -> Path:
    from odoo.tools import config

    config.parse_config(["--config", "/etc/odoo/odoo.conf"])
    return Path(config["data_dir"]).resolve()


def fingerprint() -> dict:
    # Sorted row digests preserve duplicates without writing record data to logs.
    tables = sql(r"""
SELECT format(
 'SELECT json_build_object(''table'',%L,''rows'',count(*),''md5'',md5(coalesce(string_agg(h,'''' ORDER BY h),''''))) FROM (SELECT md5(to_jsonb(t)::text) h FROM %I.%I t) hashed_rows;',
 schemaname || '.' || tablename, schemaname, tablename)
FROM pg_tables WHERE schemaname='public' ORDER BY tablename
\gexec
""")
    columns = sql("""SELECT row_to_json(c)::text FROM
      (SELECT table_name,column_name,ordinal_position,data_type,is_nullable,column_default
       FROM information_schema.columns WHERE table_schema='public'
       ORDER BY table_name,ordinal_position) c;""")
    sequences = sql("""SELECT row_to_json(s)::text FROM
      (SELECT schemaname,sequencename,start_value,min_value,max_value,increment_by,
              cycle,cache_size,last_value FROM pg_sequences
       WHERE schemaname='public' ORDER BY sequencename) s;""")
    database = sql("SELECT encoding,datcollate,datctype,datlocprovider FROM pg_database WHERE datname='bench';")
    return {
        "tables": [json.loads(line) for line in tables.splitlines() if line.startswith("{")],
        "columns_sha256": hashlib.sha256(columns.encode()).hexdigest(),
        "sequences_sha256": hashlib.sha256(sequences.encode()).hexdigest(),
        "database": database,
    }


def filestore_files(root: Path) -> dict[str, str]:
    if not root.is_dir():
        raise RuntimeError("Benchmark filestore is missing")
    result = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise RuntimeError("Snapshot filestore must not contain symbolic links")
        if path.is_file():
            result[path.relative_to(root).as_posix()] = digest(path)
    return result


def capture(destination: Path) -> None:
    if not Path("/tmp/saas_setup_complete").is_file():
        raise RuntimeError("Only a seeded benchmark fixture may be captured")
    destination.mkdir(parents=True, exist_ok=False, mode=0o700)
    servers = []
    for process in Path("/proc").iterdir():
        if process.name.isdigit():
            try:
                args = (process / "cmdline").read_bytes().split(b"\0")
            except (FileNotFoundError, PermissionError):
                continue
            if b"/usr/bin/odoo" in args and b"--no-http" not in args:
                servers.append(int(process.name))
    if len(servers) != 1:
        raise RuntimeError(f"Expected one threaded fixture Odoo server, found {len(servers)}")
    filestore = data_dir() / "filestore" / DATABASE
    os.kill(servers[0], signal.SIGSTOP)
    try:
        for _ in range(50):
            busy = sql("SELECT count(*) FROM pg_stat_activity WHERE datname='bench' AND pid<>pg_backend_pid() AND state IN ('active','idle in transaction');")
            if busy.splitlines()[-1] == "0":
                break
            time.sleep(0.1)
        else:
            raise RuntimeError("Fixture did not become quiescent; no snapshot accepted")
        state = fingerprint()
        files = filestore_files(filestore)
        with (destination / "database.dump").open("wb") as stream:
            pg("pg_dump", "-Fc", "-d", DATABASE, stdout=stream)
        with tarfile.open(destination / "filestore.tar", "w") as archive:
            archive.add(filestore, arcname=DATABASE)
        (destination / "api_key").write_bytes(Path("/etc/odoo/api_key").read_bytes())
        manifest = {
            "format": 1, "case": CASE, "database": DATABASE,
            "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "fingerprint": state, "filestore_files": files,
            "sha256": {name: digest(destination / name)
                       for name in ("database.dump", "filestore.tar", "api_key")},
        }
        (destination / "manifest.json").write_text(json.dumps(manifest, indent=2))
        print(json.dumps({"snapshot": "captured", "tables": len(state["tables"]), "files": len(files)}))
    finally:
        os.kill(servers[0], signal.SIGCONT)


def checked_manifest(source: Path) -> dict:
    manifest = json.loads((source / "manifest.json").read_text())
    if (manifest.get("format"), manifest.get("database"), manifest.get("case")) != (1, DATABASE, CASE):
        raise ValueError("Not an approved stage-1 benchmark snapshot")
    for name in ("database.dump", "filestore.tar", "api_key"):
        if digest(source / name) != manifest["sha256"][name]:
            raise ValueError(f"Snapshot checksum mismatch: {name}")
    return manifest


def database_creation_args(identity: str) -> list[str]:
    encoding, collate, ctype, provider = identity.splitlines()[-1].split("|")
    if (encoding, provider) != ("6", "c"):
        raise ValueError("This fixture restore supports UTF-8/libc databases only")
    return ["--template=template0", "--encoding=UTF8", "--locale-provider=libc",
            f"--lc-collate={collate}", f"--lc-ctype={ctype}"]


def restore(source: Path) -> None:
    manifest = checked_manifest(source)
    exists = sql("SELECT count(*) FROM pg_database WHERE datname='bench';", "postgres")
    if exists.splitlines()[-1] != "0":
        raise RuntimeError("Refusing to overwrite an existing bench database")
    target = data_dir() / "filestore"
    if (target / DATABASE).exists():
        raise RuntimeError("Refusing to overwrite an existing benchmark filestore")
    sql("""DO $$ BEGIN
      IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='odoo') THEN
        CREATE ROLE odoo LOGIN PASSWORD 'odoo' CREATEDB CREATEROLE SUPERUSER;
      END IF;
    END $$;""", "postgres")
    pg("createdb", *database_creation_args(manifest["fingerprint"]["database"]),
       "-O", "odoo", DATABASE)
    with (source / "database.dump").open("rb") as stream:
        pg("pg_restore", "--exit-on-error", "-d", DATABASE, stdin=stream)
    target.mkdir(parents=True, exist_ok=True)
    with tarfile.open(source / "filestore.tar") as archive:
        for member in archive.getmembers():
            parts = Path(member.name).parts
            if (not parts or parts[0] != DATABASE or ".." in parts
                    or not (member.isfile() or member.isdir())):
                raise ValueError("Unsafe snapshot archive member")
        archive.extractall(target, filter="data")
    Path("/etc/odoo/api_key").write_bytes((source / "api_key").read_bytes())
    actual = fingerprint()
    actual_files = filestore_files(target / DATABASE)
    if actual != manifest["fingerprint"] or actual_files != manifest["filestore_files"]:
        expected_tables = {row["table"]: row for row in manifest["fingerprint"]["tables"]}
        actual_tables = {row["table"]: row for row in actual["tables"]}
        difference = {
            "status": "failed", "fingerprint": actual,
            "different_tables": [name for name in expected_tables.keys() | actual_tables.keys()
                                 if expected_tables.get(name) != actual_tables.get(name)],
            "different_files": [name for name in actual_files.keys() | manifest["filestore_files"].keys()
                                if actual_files.get(name) != manifest["filestore_files"].get(name)],
        }
        Path("/logs/agent").mkdir(parents=True, exist_ok=True)
        Path("/logs/agent/snapshot-receipt.json").write_text(json.dumps(difference, indent=2))
        print(json.dumps({"snapshot": "mismatch", "different_tables": difference["different_tables"],
                          "different_files": len(difference["different_files"]),
                          "columns_match": actual["columns_sha256"] == manifest["fingerprint"]["columns_sha256"],
                          "sequences_match": actual["sequences_sha256"] == manifest["fingerprint"]["sequences_sha256"],
                          "database_match": actual["database"] == manifest["fingerprint"]["database"]}), flush=True)
        raise RuntimeError("Restored state differs from snapshot; agent must not start")
    receipt = {"status": "verified", "case": CASE, "snapshot_sha256": digest(source / "manifest.json"),
               "tables": len(actual["tables"]), "files": len(manifest["filestore_files"])}
    Path("/logs/agent").mkdir(parents=True, exist_ok=True)
    Path("/logs/agent/snapshot-receipt.json").write_text(json.dumps(receipt, indent=2))
    print(json.dumps(receipt), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("capture", "restore"))
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    if os.environ.get("PI_ODOO_LAB_SNAPSHOT") != "1":
        parser.error("Run only in a disposable lab container with PI_ODOO_LAB_SNAPSHOT=1")
    (capture if args.mode == "capture" else restore)(args.directory.resolve())
