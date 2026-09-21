"""Prepare the two isolated Harbor regressions; no model calls during preparation."""
import argparse
import hashlib
import json
import re
import shutil
from pathlib import Path

from manage import ROOT, RUN, linux


def verify():
    frozen = json.loads((RUN / "live/frozen.json").read_text(encoding="utf-8"))
    for name, expected in frozen["source_hashes"].items():
        assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == expected, "Frozen input changed: " + name
    print("Frozen source, wheel, configuration and task hashes verified")


def prepare(template, base_image):
    target = RUN / "bench"
    target.mkdir(exist_ok=False)
    (target / "logs").mkdir()
    (target / "tmp").mkdir()
    (target / "configs").mkdir()
    locale = target / "configs/locale.yml"
    locale.write_text("services:\n  main:\n    environment:\n      ODOO_LOCALE: en_US\n")
    source = json.loads(template.read_text(encoding="utf-8"))
    hashes = {}
    for number in ("2003", "2156"):
        task = next((ROOT / "bench/tasks").glob(number + "_*"))
        copy = target / "tasks" / task.name
        shutil.copytree(task, copy, ignore=shutil.ignore_patterns("__pycache__"))
        config = copy / "task.toml"
        text = config.read_text()
        text = re.sub(r"(\[agent\]\s*)timeout_sec\s*=\s*[^\n]+", r"\1", text)
        text = re.sub(r"(\[verifier\]\s*)timeout_sec\s*=\s*[^\n]+", r"\1timeout_sec = inf", text)
        config.write_text(text)
        # Use the already validated local benchmark image, preserving the task's seed and verifier.
        (copy / "environment/Dockerfile").write_text(f"FROM {base_image}\nUSER root\nCOPY setup_scenario.py /setup/setup_scenario.py\nCOPY scenario_data.json /setup/scenario_data.json\nCOPY entrypoint.sh /usr/local/bin/harbor-entrypoint.sh\nRUN chmod +x /usr/local/bin/harbor-entrypoint.sh\nWORKDIR /workspace\nENTRYPOINT [\"/usr/local/bin/harbor-entrypoint.sh\"]\n")
        job = json.loads(json.dumps(source))
        job.update(job_name="enterprise-B" + number, jobs_dir=linux(target / "jobs" / number),
                   n_attempts=1, n_concurrent_trials=1, retry={"max_retries": 0})
        job["environment"]["extra_docker_compose"] = [linux(ROOT / "bench/configs/harbor-proxy-compose.yml"), linux(locale)]
        kwargs = job["agents"][0]["kwargs"]
        if number == "2156":
            kwargs.pop("task_evidence", None)
        for key in ("max_turns", "max_model_requests", "max_output_tokens", "runtime_timeout_seconds"):
            kwargs[key] = None
        job["tasks"] = [{"path": linux(copy)}]
        (target / "configs" / (number + ".json")).write_text(json.dumps(job, indent=2))
        hashes[number] = {p.relative_to(copy).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                          for p in copy.rglob("*") if p.is_file()}
    (target / "inputs.json").write_text(json.dumps({"base_image": base_image, "task_hashes": hashes}, indent=2))
    print("Prepared B2003 and B2156; paid calls: 0")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--template", type=Path)
    parser.add_argument("--base-image")
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    if args.verify:
        verify()
    else:
        assert args.template and args.base_image
        prepare(args.template, args.base_image)
