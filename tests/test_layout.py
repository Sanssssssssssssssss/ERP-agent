from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import tomllib

from integration.odoo_tools import native_tool_catalog
from integration.pi_odoo_runner import CURRENT_TIME_TOOL
from integration.reward_adapter import adapt_erp_bench_reward
from odoo_runtime.sops import build_sop_tools

ROOT = Path(__file__).resolve().parents[1]


class CleanHarnessTest(unittest.TestCase):
    def test_pinned_layout_and_mcp_only_boundary(self) -> None:
        lock = json.loads((ROOT / "sources.lock.json").read_text(encoding="utf-8"))
        tasks = [path for path in (ROOT / "bench" / "tasks").iterdir() if path.is_dir()]
        runner = (ROOT / "integration" / "pi_odoo_runner.py").read_text(
            encoding="utf-8"
        )
        patch = (ROOT / "patches" / "erp-bench-odoo19.patch").read_text(
            encoding="utf-8"
        )

        self.assertEqual(len(tasks), 300)
        self.assertFalse((ROOT / "bench" / "tasks_ui").exists())
        self.assertFalse((ROOT / "integration" / "native_reads.py").exists())
        self.assertTrue((ROOT / "odoo_runtime" / "reads.py").is_file())
        self.assertEqual(patch.count("diff --git "), 3)
        self.assertNotIn("create_coding_tools", runner)
        self.assertIn("tools=list(session_tools)", runner)
        self.assertIn("project_resources_enabled=False", runner)
        self.assertIn("skills_enabled=False", runner)
        self.assertIn("extensions_enabled=False", runner)
        self.assertIn("DynamicToolController", runner)
        self.assertIn("stage_tools_for_next_turn", runner)
        self.assertEqual(
            lock["sources"]["agent"]["commit"],
            "2ee840c3e31a62c06237b4ac781c7f5863044d07",
        )
        self.assertEqual(lock["sources"]["mcp"]["version"], "1.3.2")

    def test_reward_adapter_preserves_details_and_emits_scalar(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "reward.json"
            source.write_text(
                '{"overall_score": 97.5, "constraint": {}}', encoding="utf-8"
            )

            result = adapt_erp_bench_reward(source, root / "harbor")

            self.assertEqual(result["reward"], 97.5)
            self.assertEqual((root / "harbor" / "reward.txt").read_text(), "97.5\n")
            self.assertEqual(
                (root / "harbor" / "verifier_details.json").read_text(),
                source.read_text(),
            )

    def test_stage5_capability_inventory_is_complete_and_compiler_free(self) -> None:
        inventory = json.loads(
            (ROOT / "configs" / "capabilities.json").read_text(encoding="utf-8")
        )
        self.assertEqual(inventory["compiler"], "out_of_scope")
        self.assertEqual(len(inventory["tools"]), 41)
        self.assertEqual(len({row["name"] for row in inventory["tools"]}), 41)
        self.assertEqual(len(inventory["prompts"]), 11)
        self.assertEqual(len(inventory["resources"]), 4)
        self.assertEqual(
            sum(row["stage"] == 5 and row["status"].startswith("native") for row in inventory["tools"]),
            21,
        )

    def test_stage7_native_surface_has_no_mcp_runtime_dependency(self) -> None:
        stage6 = json.loads(
            (ROOT / "configs" / "stage6-knowledge-native-b.json").read_text(
                encoding="utf-8"
            )
        )
        stage7 = json.loads(
            (ROOT / "configs" / "stage7-odoo-native-b.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertNotIn("runtime_mode", stage6["agents"][0]["kwargs"])
        self.assertEqual(stage7["agents"][0]["kwargs"]["runtime_mode"], "native")
        self.assertEqual(stage7["tasks"], stage6["tasks"])
        self.assertEqual(stage7["extra_instructions"], stage6["extra_instructions"])
        self.assertEqual(
            stage7["agents"][0]["model_name"], stage6["agents"][0]["model_name"]
        )
        for key in (
            "thinking",
            "read_backend",
            "action_backend",
            "capability_backend",
            "sop_mode",
            "tool_mode",
            "world_mode",
            "snapshot_sha256",
        ):
            self.assertEqual(
                stage7["agents"][0]["kwargs"][key],
                stage6["agents"][0]["kwargs"][key],
            )
        inventory = json.loads(
            (ROOT / "configs" / "capabilities.json").read_text(encoding="utf-8")
        )
        catalog = json.loads(
            (ROOT / "integration" / "native_tool_catalog.json").read_text(
                encoding="utf-8"
            )
        )
        catalog_names = [
            tool["name"].removeprefix("mcp_odoo_") for tool in catalog["tools"]
        ]
        self.assertEqual(len(catalog_names), len(set(catalog_names)))
        self.assertEqual(
            set(catalog_names),
            {tool["name"] for tool in inventory["tools"]},
        )
        self.assertTrue(all(tool["status"].startswith("native") for tool in inventory["tools"]))
        runtime = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (ROOT / "odoo_runtime").rglob("*.py")
        )
        self.assertNotIn("from odoo_mcp", runtime)
        self.assertNotIn("import odoo_mcp", runtime)
        project = tomllib.loads(
            (ROOT / "agent" / "pyproject.toml").read_text(encoding="utf-8")
        )["project"]
        self.assertNotIn("mcp==2.0.0", project["dependencies"])
        self.assertEqual(project["optional-dependencies"]["mcp"], [
            "mcp==2.0.0",
            "mcp-types==2.0.0",
        ])

        with tempfile.TemporaryDirectory() as directory:
            tools = [
                *native_tool_catalog(),
                CURRENT_TIME_TOOL,
                *build_sop_tools(
                    Path(directory) / "sop.jsonl",
                    iter(range(1, 100)).__next__,
                ),
            ]
        contract = [
            {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters,
            }
            for tool in tools
        ]
        self.assertEqual(len(tools), 44)
        self.assertEqual(
            hashlib.sha256(json.dumps(contract, sort_keys=True).encode()).hexdigest(),
            "f70746bdd6afebe10ea018d6ac5521caf1e66fb375480ce4d5d4f72bad732c56",
        )


if __name__ == "__main__":
    unittest.main()
