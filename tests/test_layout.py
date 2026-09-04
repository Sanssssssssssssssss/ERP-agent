from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from integration.reward_adapter import adapt_erp_bench_reward

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
        self.assertIn("tools=list(toolset.tools)", runner)
        self.assertIn("project_resources_enabled=False", runner)
        self.assertIn("skills_enabled=False", runner)
        self.assertIn("extensions_enabled=False", runner)
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


if __name__ == "__main__":
    unittest.main()
