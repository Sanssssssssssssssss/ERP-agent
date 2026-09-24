from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from erp_harness.app.storage import StateStore


class StateStoreTest(unittest.TestCase):
    def test_notification_window_keeps_business_state(self):
        with tempfile.TemporaryDirectory() as directory:
            old_window = StateStore.MAX_NOTIFICATIONS
            StateStore.MAX_NOTIFICATIONS = 2
            try:
                store = StateStore(Path(directory))
                store.data["businesses"]["b1"] = {"status": "done"}
                store.data["approvals"]["a1"] = {"status": "verified"}
                store.data["messages"]["m1"] = {"text": "kept"}
                store.data["runs"]["r1"] = {"tools": [{"name": "kept"}], "events": [{"type": "receipt"}]}
                for index in range(3):
                    store.event("notice", {"index": index})
                self.assertEqual([row["sequence"] for row in store.data["events"]], [2, 3])
                self.assertEqual(store.data["businesses"]["b1"]["status"], "done")
                self.assertEqual(store.data["approvals"]["a1"]["status"], "verified")
                self.assertEqual(store.data["messages"]["m1"]["text"], "kept")
                self.assertIsNotNone(store.last_save_ms)
                self.assertGreater(store.last_save_bytes or 0, 0)
                store.close()
                reopened = StateStore(Path(directory))
                self.assertEqual(reopened.data["runs"]["r1"]["events"][0]["type"], "receipt")
                self.assertEqual(reopened.data["runs"]["r1"]["tools"][0]["name"], "kept")
                self.assertIsNone(reopened.last_save_bytes)
                reopened.close()
            finally:
                StateStore.MAX_NOTIFICATIONS = old_window


if __name__ == "__main__":
    unittest.main()
