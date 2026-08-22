from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CHAT = (ROOT / "companion" / "src" / "screens" / "Chat.tsx").read_text(encoding="utf-8")
CSS = (ROOT / "companion" / "src" / "styles" / "tokens.css").read_text(encoding="utf-8")
OWNERSHIP = (
    ROOT / "companion" / "src" / "lib" / "conversationOwnership.ts"
).read_text(encoding="utf-8")


class ChatDeleteConfirmTests(unittest.TestCase):
    def test_overlay_is_not_mounted_while_confirming(self) -> None:
        self.assertIn("{menu && !deleteId ? (", CHAT)
        self.assertIn("if (deleteId) setMenu(null)", CHAT)
        self.assertIn("if (deleteId) return", CHAT)

    def test_confirm_delete_button_is_enabled_until_pending(self) -> None:
        self.assertIn("disabled={deleteMutation.isPending}", CHAT)
        self.assertIn("deleteMutation.mutate(deleteId)", CHAT)
        self.assertIn('type="button"', CHAT)
        self.assertIn('aria-label="Delete conversation permanently"', CHAT)

    def test_confirm_panel_is_above_menu_layer(self) -> None:
        self.assertIn(".menu-layer", CSS)
        self.assertIn("z-index: 50", CSS)
        self.assertIn(".menu-confirm", CSS)
        self.assertIn("z-index: 60", CSS)
        self.assertIn("pointer-events: auto", CSS)
        confirm_css = CSS.split(".menu-confirm {", 1)[1].split("}", 1)[0]
        self.assertIn("pointer-events: auto", confirm_css)
        self.assertNotIn("pointer-events: none", confirm_css)

    def test_next_active_after_delete_helper_exists(self) -> None:
        self.assertIn("export function nextActiveAfterDelete", OWNERSHIP)
        self.assertIn("nextActiveAfterDelete(", CHAT)


def next_active_after_delete(deleted_id, conversations, active_id):
    remaining = [item for item in conversations if item["id"] != deleted_id]
    if active_id != deleted_id:
        return active_id
    return remaining[0]["id"] if remaining else None


class ConversationOwnershipLogicTests(unittest.TestCase):
    def test_next_active_after_delete(self) -> None:
        rows = [{"id": "a"}, {"id": "b"}, {"id": "c"}]
        self.assertEqual(next_active_after_delete("b", rows, "b"), "a")
        self.assertEqual(next_active_after_delete("a", rows, "c"), "c")
        self.assertIsNone(next_active_after_delete("a", [{"id": "a"}], "a"))
