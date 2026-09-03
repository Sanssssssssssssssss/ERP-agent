from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from ..harbor import TEMPLATES_DIR
from .models import TaskRenderSpec

logger = logging.getLogger(__name__)


class HarborRenderer:
    def __init__(
        self,
        *,
        output_dir: Path | str,
        force: bool = False,
    ) -> None:
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.force = force
        self.jinja_env = Environment(
            loader=FileSystemLoader(str(TEMPLATES_DIR)),
            trim_blocks=True,
            lstrip_blocks=True,
        )

    def write(self, spec: TaskRenderSpec) -> Path | None:
        task_dir = self.output_dir / spec.task_name
        if task_dir.exists():
            if not self.force:
                logger.warning(
                    "Skipping %s (already exists, use --force to overwrite)",
                    spec.task_name,
                )
                return None
            shutil.rmtree(task_dir)

        task_dir.mkdir(parents=True)
        (task_dir / "environment").mkdir()
        (task_dir / "tests").mkdir()
        (task_dir / "solution").mkdir()

        self._write_task_toml(task_dir, spec)
        self._write_environment(task_dir, spec)
        self._write_template_files(task_dir, spec)
        self._write_json_files(task_dir, spec)

        logger.info("Exported Harbor task: %s", task_dir)
        return task_dir

    def _write_task_toml(self, task_dir: Path, spec: TaskRenderSpec) -> None:
        metadata = spec.metadata
        content = self.jinja_env.get_template("shared/task.toml.jinja2").render(
            scenario_number=metadata.scenario_number,
            scenario_name=metadata.scenario_name,
            difficulty=metadata.difficulty,
            category=metadata.category,
            objective_kind=metadata.objective_kind,
            task_pattern=metadata.task_pattern,
            tags=list(metadata.tags),
            seed=metadata.seed,
            build_timeout_sec=metadata.build_timeout_sec,
            cpus=metadata.cpus,
            memory_mb=metadata.memory_mb,
            storage_mb=metadata.storage_mb,
        )
        (task_dir / "task.toml").write_text(content)

    def _write_environment(self, task_dir: Path, spec: TaskRenderSpec) -> None:
        env_dir = task_dir / "environment"
        env_spec = spec.environment
        env_context = {
            "runtime_db_name": env_spec.runtime_db_name,
            "install_native_xlsx_support": env_spec.install_native_xlsx_support,
            "admin_user": env_spec.admin_user,
            "admin_password": env_spec.admin_password,
        }
        (env_dir / "Dockerfile").write_text(
            self.jinja_env.get_template("shared/Dockerfile.jinja2").render(**env_context)
        )
        entrypoint_path = env_dir / "entrypoint.sh"
        entrypoint_path.write_text(
            self.jinja_env.get_template("shared/entrypoint.sh.jinja2").render(**env_context)
        )
        entrypoint_path.chmod(0o755)

        setup_context = {"scenario": spec.scenario, **env_context}
        setup_context.update(dict(env_spec.template_context))
        (env_dir / "setup_scenario.py").write_text(
            self.jinja_env.get_template(env_spec.setup_template).render(**setup_context)
        )

        odoo_conf = TEMPLATES_DIR / "shared" / "odoo.conf"
        if odoo_conf.exists():
            shutil.copy(odoo_conf, env_dir / "odoo.conf")

        scenario_data = spec.scenario.model_dump(mode="json")
        (env_dir / "scenario_data.json").write_text(json.dumps(scenario_data, indent=2))

    def _write_template_files(self, task_dir: Path, spec: TaskRenderSpec) -> None:
        for file_spec in spec.files:
            path = task_dir / file_spec.path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                self.jinja_env.get_template(file_spec.template).render(**file_spec.context)
            )
            if file_spec.executable:
                path.chmod(0o755)

    def _write_json_files(self, task_dir: Path, spec: TaskRenderSpec) -> None:
        for file_spec in spec.json_files:
            path = task_dir / file_spec.path
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = file_spec.payload
            if hasattr(payload, "model_dump"):
                payload = payload.model_dump(mode="json")
            path.write_text(json.dumps(payload, indent=2))
