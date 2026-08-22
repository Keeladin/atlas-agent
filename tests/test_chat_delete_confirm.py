from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CHAT = (ROOT / "companion" / "src" / "screens" / "Chat.tsx").read_text(encoding="utf-8")
CSS = (ROOT / "companion" / "src" / "styles" / "tokens.css").read_text(encoding="utf-8")
OWNERSHIP = (
    ROOT / "companion" / "src" / "lib" / "conversationOwnership.ts"
).read_text(encoding="utf-8")


def _block(css: str, selector: str) -> str:
    marker = selector + " {"
    self_pos = css.find(marker)
    if self_pos < 0:
        raise AssertionError(f"missing {selector}")
    return css[self_pos + len(marker) :].split("}", 1)[0]


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
        root = _block(CSS, ".menu-root")
        layer = _block(CSS, ".menu-layer")
        menu = _block(CSS, ".menu")
        confirm = _block(CSS, ".menu-confirm")
        self.assertIn("z-index: 50", root)
        self.assertIn("z-index: 0", layer)
        self.assertIn("z-index: 1", menu)
        self.assertIn("z-index: 60", confirm)

    def test_next_active_after_delete_helper_exists(self) -> None:
        self.assertIn("export function nextActiveAfterDelete", OWNERSHIP)
        self.assertIn("nextActiveAfterDelete(", CHAT)


class ChatRecentsMenuLayerTests(unittest.TestCase):
    def test_click_away_is_below_menu_panel(self) -> None:
        layer = _block(CSS, ".menu-layer")
        menu = _block(CSS, ".menu")
        self.assertIn("z-index: 0", layer)
        self.assertIn("z-index: 1", menu)
        self.assertIn("pointer-events: auto", layer)
        self.assertIn("pointer-events: auto", menu)
        self.assertIn("pointer-events: auto", _block(CSS, ".menu button"))
        self.assertIn("pointer-events: none", _block(CSS, ".menu-root"))

    def test_overlay_is_a_sibling_not_a_parent_of_the_menu(self) -> None:
        self.assertIn("createPortal(", CHAT)
        self.assertIn('className="menu-root"', CHAT)
        self.assertIn('className="menu-layer"', CHAT)
        self.assertIn("onPointerDown={onClose}", CHAT)
        self.assertNotIn(
            'className="menu-layer"\n        role="presentation"\n        onClick={onClose}',
            CHAT,
        )
        self.assertIn("onPointerDown={stopMenuEvent}", CHAT)
        self.assertIn("onClick={stopMenuEvent}", CHAT)

    def test_all_four_actions_are_enabled_buttons_with_handlers(self) -> None:
        for label in ("Rename", "Pin", "Archive", "Delete"):
            self.assertIn(label, CHAT)
        self.assertIn("runAction(event, onRename)", CHAT)
        self.assertIn("runAction(event, onPin)", CHAT)
        self.assertIn("runAction(event, onArchive)", CHAT)
        self.assertIn("runAction(event, onDelete)", CHAT)
        self.assertIn("event.stopPropagation()", CHAT)
        self.assertGreaterEqual(CHAT.count('type="button"'), 4)
        self.assertNotIn("disabled={true}", CHAT)
        self.assertNotIn("pointer-events: none", _block(CSS, ".menu button"))


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
