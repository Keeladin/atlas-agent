from __future__ import annotations

import hashlib
import os
import tempfile
import unicodedata
import unittest
from pathlib import Path

from atlas_core.sources import (
    CancellationToken,
    LocalRootConfig,
    LocalRootRegistry,
    LocalSourceError,
    LocalSourceKernel,
    SourceObservation,
)


class LocalSourceKernelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root_path = Path(self.tmp.name) / "source-root"
        self.root_path.mkdir()
        self.registry = LocalRootRegistry()
        self.registry.register(LocalRootConfig(
            root_id="documents", provider_namespace="local", host_path=str(self.root_path),
            display_name="Documents", configuration_revision="config-1",
        ))
        self.kernel = LocalSourceKernel(self.registry, cursor_secret=b"x" * 32)

    def tearDown(self) -> None:
        self.registry.close()
        self.tmp.cleanup()

    def assert_error(self, code: str, callback) -> LocalSourceError:
        with self.assertRaises(LocalSourceError) as raised:
            callback()
        self.assertEqual(raised.exception.code, code)
        return raised.exception

    def test_valid_registration_and_revision_lookup(self) -> None:
        ref = self.kernel.source_ref("local", "documents", ".")
        self.assertEqual(ref.source_id, "documents:.")
        self.assertEqual(ref.display_locator, "Documents")
        self.assert_error("root_revision_unavailable", lambda: self.kernel.stat(
            "local", "documents", ".", configuration_revision="old",
        ))

    def test_symlink_root_is_rejected_without_leaking_host_path(self) -> None:
        link = Path(self.tmp.name) / "linked"
        link.symlink_to(self.root_path, target_is_directory=True)
        error = self.assert_error("symlink_rejected", lambda: self.registry.register(LocalRootConfig(
            root_id="linked", provider_namespace="local", host_path=str(link),
        )))
        self.assertNotIn(str(link), str(error.to_dict()))

    def test_root_identity_cannot_be_reused_for_another_target(self) -> None:
        other = Path(self.tmp.name) / "other"
        other.mkdir()
        self.assert_error("root_revision_unavailable", lambda: self.registry.register(LocalRootConfig(
            root_id="documents", provider_namespace="local", host_path=str(other),
        )))

    def test_path_grammar(self) -> None:
        (self.root_path / ".hidden").write_text("ok", encoding="utf-8")
        self.assertEqual(self.kernel.stat("local", "documents", ".hidden").object_type, "regular_file")
        decomposed = unicodedata.normalize("NFD", "café")
        invalid = ["", "/x", "x/", "x//y", "./x", "x/.", "..", "x/../y", "x\\y", "C:/x", "x\x00y", "x\ny", decomposed]
        for value in invalid:
            with self.subTest(value=repr(value)):
                self.assert_error("invalid_path", lambda value=value: self.kernel.source_ref("local", "documents", value))
        self.assertEqual(self.kernel.source_ref("local", "documents", "~").relative_path, "~")

    def test_escape_and_intermediate_symlink_are_rejected(self) -> None:
        outside = Path(self.tmp.name) / "outside"
        outside.mkdir()
        (outside / "secret.txt").write_text("secret", encoding="utf-8")
        (self.root_path / "jump").symlink_to(outside, target_is_directory=True)
        self.assert_error("invalid_path", lambda: self.kernel.read("local", "documents", "../outside/secret.txt"))
        self.assert_error("symlink_rejected", lambda: self.kernel.read("local", "documents", "jump/secret.txt"))

    def test_terminal_symlink_stat_succeeds_but_content_operations_reject(self) -> None:
        (self.root_path / "target.txt").write_text("target", encoding="utf-8")
        (self.root_path / "link.txt").symlink_to("target.txt")
        self.assertEqual(self.kernel.stat("local", "documents", "link.txt").object_type, "symlink")
        self.assert_error("symlink_rejected", lambda: self.kernel.read("local", "documents", "link.txt"))
        self.assert_error("symlink_rejected", lambda: self.kernel.hash("local", "documents", "link.txt"))

    def test_special_file_is_never_read_or_hashed(self) -> None:
        fifo = self.root_path / "pipe"
        os.mkfifo(fifo)
        self.assertEqual(self.kernel.stat("local", "documents", "pipe").object_type, "fifo")
        self.assert_error("special_object_rejected", lambda: self.kernel.read("local", "documents", "pipe"))
        self.assert_error("special_object_rejected", lambda: self.kernel.hash("local", "documents", "pipe"))

    def test_hard_links_have_distinct_source_refs(self) -> None:
        first = self.root_path / "first.txt"
        second = self.root_path / "second.txt"
        first.write_text("same", encoding="utf-8")
        os.link(first, second)
        one = self.kernel.stat("local", "documents", "first.txt")
        two = self.kernel.stat("local", "documents", "second.txt")
        self.assertNotEqual(one.source_ref, two.source_ref)
        self.assertEqual(one.metadata["inode"], two.metadata["inode"])

    def test_stable_stat_hash_and_read_use_exact_bytes(self) -> None:
        raw = "hello λ\n".encode()
        (self.root_path / "text.txt").write_bytes(raw)
        metadata = self.kernel.stat("local", "documents", "text.txt")
        hashed = self.kernel.hash("local", "documents", "text.txt")
        read = self.kernel.read("local", "documents", "text.txt")
        self.assertEqual(metadata.consistency, "metadata_only")
        self.assertEqual(hashed.consistency, "stable")
        self.assertEqual(read.observation.consistency, "stable")
        self.assertEqual(hashed.byte_sha256, hashlib.sha256(raw).hexdigest())
        self.assertEqual(read.observation.byte_sha256, hashlib.sha256(raw).hexdigest())
        self.assertEqual(read.text, "hello λ\n")

    def test_drift_is_durable_diagnostic_without_stable_hash(self) -> None:
        path = self.root_path / "changing.txt"
        path.write_bytes(b"a" * 150_000)
        changed = False

        def mutate(_: int) -> None:
            nonlocal changed
            if not changed:
                changed = True
                with path.open("ab") as stream:
                    stream.write(b"changed")

        kernel = LocalSourceKernel(self.registry, stream_hook=mutate)
        error = self.assert_error("drifted", lambda: kernel.hash("local", "documents", "changing.txt"))
        observation = error.details["observation"]
        self.assertEqual(observation["consistency"], "drifted")
        self.assertIsNone(observation["byte_sha256"])
        self.assertIn("diagnostic_digest", observation["metadata"])

    def test_utf8_bom_invalid_utf8_and_too_large(self) -> None:
        bom_raw = b"\xef\xbb\xbfhello"
        (self.root_path / "bom.txt").write_bytes(bom_raw)
        result = self.kernel.read("local", "documents", "bom.txt")
        self.assertTrue(result.bom)
        self.assertEqual(result.text, "hello")
        self.assertEqual(result.observation.byte_sha256, hashlib.sha256(bom_raw).hexdigest())
        (self.root_path / "bad.bin").write_bytes(b"\xff")
        self.assert_error("unsupported_encoding", lambda: self.kernel.read("local", "documents", "bad.bin"))
        with (self.root_path / "large.txt").open("wb") as stream:
            stream.truncate(4 * 1024 * 1024 + 1)
        self.assert_error("too_large", lambda: self.kernel.read("local", "documents", "large.txt"))

    def test_listing_is_deterministic_paginated_and_includes_hidden_files(self) -> None:
        for name in ["z.txt", ".hidden", "a.txt"]:
            (self.root_path / name).write_text(name, encoding="utf-8")
        first = self.kernel.list("local", "documents", ".", page_size=2)
        self.assertEqual([item.source_ref.relative_path for item in first.entries], [".hidden", "a.txt"])
        self.assertIsNotNone(first.next_cursor)
        second = self.kernel.list("local", "documents", ".", page_size=2, cursor=first.next_cursor)
        self.assertEqual([item.source_ref.relative_path for item in second.entries], ["z.txt"])
        self.assertIsNone(second.next_cursor)

    def test_cancellation_and_timeout(self) -> None:
        (self.root_path / "text.txt").write_text("hello", encoding="utf-8")
        token = CancellationToken()
        token.cancel()
        self.assert_error("cancelled", lambda: self.kernel.read("local", "documents", "text.txt", cancellation=token))
        self.assert_error("timeout", lambda: self.kernel.stat("local", "documents", "text.txt", timeout_seconds=0))

    def test_observation_payload_hash_is_stable_but_ids_are_distinct(self) -> None:
        ref = self.kernel.source_ref("local", "documents", ".")
        args = dict(
            source_ref=ref, observed_at="2026-01-01T00:00:00+00:00", observation_kind="metadata",
            object_type="directory", consistency="metadata_only", completeness="metadata_only",
        )
        one = SourceObservation.create(**args)
        two = SourceObservation.create(**args)
        self.assertNotEqual(one.observation_id, two.observation_id)
        self.assertEqual(one.observation_payload_sha256, two.observation_payload_sha256)

    def test_results_and_errors_do_not_contain_absolute_host_paths(self) -> None:
        (self.root_path / "text.txt").write_text("hello", encoding="utf-8")
        payload = str(self.kernel.read("local", "documents", "text.txt").to_dict())
        self.assertNotIn(str(self.root_path), payload)
        error = self.assert_error("missing", lambda: self.kernel.stat("local", "documents", "absent.txt"))
        self.assertNotIn(str(self.root_path), str(error.to_dict()))

    def test_cross_mount_is_rejected_when_a_distinct_mount_is_available(self) -> None:
        if os.stat("/").st_dev == os.stat("/proc").st_dev:
            self.skipTest("/proc is not a distinct mount in this environment")
        registry = LocalRootRegistry()
        try:
            registry.register(LocalRootConfig(root_id="host", provider_namespace="test", host_path="/"))
            kernel = LocalSourceKernel(registry)
            self.assert_error("outside_root", lambda: kernel.stat("test", "host", "proc"))
        finally:
            registry.close()


if __name__ == "__main__":
    unittest.main()
