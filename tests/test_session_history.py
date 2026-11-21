import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
import session_history


class SessionHistoryTests(unittest.TestCase):
    def test_indexes_codex_and_claude_metadata_and_resolves_source_home(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            codex_home = root / "codex" / "p-codex" / "scope"
            codex_file = codex_home / "sessions/2026/11/19/rollout-test-01a1ec86-bf14-70f2-8144-874b179beb72.jsonl"
            codex_file.parent.mkdir(parents=True)
            (codex_home / "workbench-profile.json").write_text(json.dumps({
                "profile_id": "p-codex", "model": "model-a", "gateway": "https://gateway.test",
            }), encoding="utf-8")
            codex_file.write_text("\n".join([
                json.dumps({"type": "session_meta", "timestamp": "2026-11-19T10:00:00Z",
                            "payload": {"session_id": "01a1ec86-bf14-70f2-8144-874b179beb72", "cwd": "/work/project"}}),
                json.dumps({"type": "turn_context", "timestamp": "2026-11-19T10:01:00Z",
                            "payload": {"model": "model-a"}}),
                json.dumps({"type": "response_item", "payload": {"type": "message", "role": "user",
                            "content": [{"type": "input_text", "text": "Fix the startup regression"}]}}),
                json.dumps({"type": "response_item", "payload": {"message": {"role": "user",
                            "content": [{"type": "input_text", "text": "The latest Codex message"}]}}}),
            ]) + "\n", encoding="utf-8")

            claude_home = root / "claude" / "p-claude"
            claude_file = claude_home / "projects/project/8123cb5e-1984-435a-9803-976796d3075c.jsonl"
            claude_file.parent.mkdir(parents=True)
            claude_file.write_text("\n".join([
                json.dumps({"type": "user", "sessionId": "8123cb5e-1984-435a-9803-976796d3075c", "cwd": "/work/other",
                            "timestamp": "2026-11-19T11:00:00.000Z",
                            "message": {"content": [{"type": "text", "text": "Review this project"}]}}),
                json.dumps({"type": "assistant", "sessionId": "8123cb5e-1984-435a-9803-976796d3075c", "cwd": "/work/other",
                            "message": {"model": "model-b"}}),
                json.dumps({"type": "user", "sessionId": "8123cb5e-1984-435a-9803-976796d3075c", "cwd": "/work/other",
                            "timestamp": "2026-11-19T11:01:00.000Z",
                            "message": {"content": [{"type": "text", "text": "The latest Claude message"}]}}),
            ]) + "\n", encoding="utf-8")

            rows = session_history.scan_history(
                [(codex_home, "p-codex", "Codex profile")],
                [(claude_home, "p-claude", "Claude profile")],
            )
            by_id = {row["id"]: row for row in rows}
            self.assertEqual(set(by_id), {"01a1ec86-bf14-70f2-8144-874b179beb72", "8123cb5e-1984-435a-9803-976796d3075c"})
            self.assertEqual(by_id["01a1ec86-bf14-70f2-8144-874b179beb72"]["model"], "model-a")
            self.assertEqual(by_id["01a1ec86-bf14-70f2-8144-874b179beb72"]["gateway"], "https://gateway.test")
            self.assertEqual(by_id["01a1ec86-bf14-70f2-8144-874b179beb72"]["title"], "Fix the startup regression")
            self.assertEqual(by_id["01a1ec86-bf14-70f2-8144-874b179beb72"]["preview"], "The latest Codex message")
            self.assertEqual(by_id["8123cb5e-1984-435a-9803-976796d3075c"]["model"], "model-b")
            self.assertEqual(by_id["8123cb5e-1984-435a-9803-976796d3075c"]["profile_name"], "Claude profile")
            self.assertEqual(by_id["8123cb5e-1984-435a-9803-976796d3075c"]["preview"], "The latest Claude message")
            self.assertNotIn("_home", session_history.public_rows(rows)[0])

            resolved = session_history.resolve_history(
                by_id["01a1ec86-bf14-70f2-8144-874b179beb72"]["history_ref"], "01a1ec86-bf14-70f2-8144-874b179beb72", "codex",
                [(codex_home, "p-codex", "Codex profile")],
                [(claude_home, "p-claude", "Claude profile")],
            )
            self.assertEqual(Path(resolved["_home"]), codex_home)

            scoped = session_history.scan_history(
                [(codex_home, "p-codex", "Codex profile")],
                [(claude_home, "p-claude", "Claude profile")],
                workspace="/work/other",
            )
            self.assertEqual({row["client"] for row in scoped}, {"claude"})
            with self.assertRaisesRegex(ValueError, "找不到"):
                session_history.resolve_history(
                    by_id["01a1ec86-bf14-70f2-8144-874b179beb72"]["history_ref"],
                    "01a1ec86-bf14-70f2-8144-874b179beb72", "codex",
                    [(codex_home, "p-codex", "Codex profile")], [], workspace="/work/other")

    def test_all_history_marks_workbench_and_system_sources(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)

            def codex_home(name, session_id, workspace):
                home = root / name
                path = home / "sessions/2026/11/19" / f"rollout-test-{session_id}.jsonl"
                path.parent.mkdir(parents=True)
                path.write_text("\n".join(map(json.dumps, [
                    {"type": "session_meta", "payload": {"id": session_id, "cwd": workspace}},
                    {"type": "response_item", "payload": {"role": "user", "content": "A real request"}},
                ])) + "\n")
                return home

            workbench = codex_home("workbench", "01a1ec86-bf14-70f2-8144-874b179beb72", "/work/a")
            system = codex_home("system", "8123cb5e-1984-435a-9803-976796d3075c", "/work/b")
            rows = session_history.scan_all_history(
                [(workbench, "p-one", "One")], [], [(system, "global", "System")], [])
            self.assertEqual({row["scope"] for row in rows}, {"workbench", "global"})

    def test_rejects_mismatched_or_unknown_history_reference(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(ValueError):
                session_history.resolve_history("x" * 32, "wrong-id", "codex", [], [])

    def test_in_place_resume_requires_matching_workbench_profile_and_model(self):
        row = {"scope": "workbench", "profile_id": "p-one", "model": "model-a",
               "gateway": "https://gateway.test"}
        self.assertTrue(session_history.can_resume_in_place(row, "p-one", "model-a", "https://gateway.test"))
        self.assertFalse(session_history.can_resume_in_place(row, "p-two", "model-a", "https://gateway.test"))
        self.assertFalse(session_history.can_resume_in_place(row, "p-one", "model-b", "https://gateway.test"))
        self.assertFalse(session_history.can_resume_in_place({**row, "scope": "global"}, "p-one", "model-a"))
        self.assertFalse(session_history.can_resume_in_place({**row, "profile_id": ""}, "p-one", "model-a"))
        self.assertFalse(session_history.can_resume_in_place({**row, "gateway": ""}, "p-one", "model-a"))
        self.assertFalse(session_history.can_resume_in_place(
            row, "p-one", "model-a", "https://other-gateway.test"))
        fingerprint = session_history.key_fingerprint("key-one")
        self.assertFalse(session_history.can_resume_in_place(
            {**row, "_key_fingerprint": fingerprint}, "p-one", "model-a", "https://gateway.test",
            session_history.key_fingerprint("key-two")))
        self.assertTrue(session_history.can_resume_in_place(
            {**row, "_key_fingerprint": fingerprint}, "p-one", "model-a", "https://gateway.test", fingerprint))
        self.assertTrue(session_history.can_resume_in_place(
            {**row, "model": "upstream-snapshot", "_configured_model": "model-a"},
            "p-one", "model-a", "https://gateway.test"))

    def test_branch_keeps_source_and_excludes_external_history_and_import_copy(self):
        with tempfile.TemporaryDirectory() as temporary:
            user = Path(temporary)
            source_home = user / "workbench" / "codex" / "original"
            external_home = user / ".codex"
            original_id = "8123cb5e-1984-435a-9803-976796d3075c"
            branch_id = "0855c25a-adf5-4270-80eb-4c4c7e545d7d"

            def rollout(home, session, model):
                file = home / "sessions/2026/11/19" / f"rollout-test-{session}.jsonl"
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_text("\n".join(map(json.dumps, [
                    {"type": "session_meta", "payload": {"id": session, "cwd": str(user)}},
                    {"type": "response_item", "payload": {"type": "message", "role": "user", "content": "fixture"}},
                    {"type": "turn_context", "payload": {"model": model}},
                ])) + "\n")
                return file

            source_file = rollout(source_home, original_id, "original-model")
            external_file = rollout(external_home, original_id, "external-model")
            config = source_home / "config.toml"
            config.write_text('model_provider = "cchp_local"\n')
            original_files = {path: path.read_bytes() for path in (source_file, external_file, config)}
            (source_home / "sessions" / "linked").symlink_to(external_file.parent, target_is_directory=True)
            (source,) = session_history.scan_history([(source_home, "old", "Old profile")], [])
            branch, seed = session_history.stage_branch(
                source, user / "workbench" / "codex", "new", "New profile", "new-model", "https://new.example")
            self.assertEqual(seed.stat().st_mode & 0o777, 0o600)
            self.assertEqual(seed.read_bytes(), original_files[source_file])
            self.assertFalse((branch / "config.toml").exists())
            self.assertEqual(session_history.scan_history([(branch, "", "")], []), [])
            rollout(branch, branch_id, "new-model")
            (entry,) = session_history.scan_history([(branch, "", "")], [])
            self.assertEqual((entry["id"], entry["profile_id"], entry["gateway"]),
                             (branch_id, "new", "https://new.example"))
            self.assertEqual({path: path.read_bytes() for path in original_files}, original_files)

    def test_paginated_codex_branch_copies_ancestors_without_listing_them(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original = "8123cb5e-1984-435a-9803-976796d3075c"
            first = "0855c25a-adf5-4270-80eb-4c4c7e545d7d"
            second = "cd098d88-ffdf-417b-9438-0fe77471509e"

            def write_rollout(home, session_id, parent=None):
                path = home / "sessions/2026/11/19" / f"rollout-test-{session_id}.jsonl"
                path.parent.mkdir(parents=True, exist_ok=True)
                meta = {"id": session_id, "cwd": str(root)}
                if parent:
                    meta["history_base"] = {"thread_id": parent, "end_ordinal_exclusive": 1,
                                            "end_byte_offset": 100}
                    items = []
                else:
                    items = [{"type": "response_item", "payload": {"type": "message", "role": "user", "content": "original title"}}]
                rows = [{"type": "session_meta", "payload": meta}, *items]
                path.write_text("\n".join(map(json.dumps, rows)) + "\n")
                return path

            source_home = root / "source"
            write_rollout(source_home, original)
            (entry,) = session_history.scan_history([(source_home, "", "")], [])
            first_home, first_seed = session_history.stage_branch(entry, root, "p-first", "", "first", "https://one.example")
            write_rollout(first_home, first, original)
            (first_entry,) = session_history.scan_history([(first_home, "", "")], [])
            self.assertEqual(first_entry["title"], "original title")
            second_home, _ = session_history.stage_branch(first_entry, root, "p-second", "", "second", "https://two.example")
            write_rollout(second_home, second, first)
            (second_entry,) = session_history.scan_history([(second_home, "", "")], [])
            self.assertEqual(second_entry["id"], second)
            self.assertEqual(second_entry["profile_id"], "p-second")
            self.assertTrue((second_home / first_seed.relative_to(first_home)).is_file())
            first_seed.unlink()
            with self.assertRaisesRegex(ValueError, "缺少"):
                session_history.stage_branch(first_entry, root, "p-second", "", "second", "https://two.example")


if __name__ == "__main__":
    unittest.main()
