import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
try:
    import tomllib
except ModuleNotFoundError:
    tomllib = None

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
import tool_bridge as T


class ToolBridgeTests(unittest.TestCase):
    def test_config_translation_preserves_provider_and_disable_removes_managed_service(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, home = Path(temporary) / "tools", Path(temporary) / "codex"
            home.mkdir()
            source = 'model = "kept"\n[model_providers.private]\nbase_url = "http://127.0.0.1:8081"\n'
            (home / "config.toml").write_text(source)
            config = {"mcpServers": {"demo": {"command": "must-not-run", "args": ["--test"], "env": {"TOKEN": "local"}}}}
            T.save_settings(root, {"config": json.dumps(config)})
            for _ in range(2):
                T.apply_tools(root, home, "codex")
            text = (home / "config.toml").read_text()
            self.assertTrue(text.startswith(source))
            if tomllib is not None:
                parsed = tomllib.loads(text)
                self.assertEqual(parsed["model"], "kept")
                self.assertEqual(parsed["mcp_servers"]["workbench_demo"]["env"]["TOKEN"], "local")
            self.assertEqual((home / "config.toml").stat().st_mode & 0o777, 0o600)
            config["mcpServers"]["demo"]["enabled"] = False
            T.save_settings(root, {"config": json.dumps(config)})
            T.apply_tools(root, home, "codex")
            self.assertEqual((home / "config.toml").read_text(), source + T.BEGIN + T.END)

    def test_import_copies_skill_without_execution_then_enables_each_client(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, source = Path(temporary) / "tools", Path(temporary) / "original"
            source.mkdir()
            (source / "SKILL.md").write_text("---\nname: sample\ndescription: test\n---\nPrivate skill\n")
            (source / "script.sh").write_text("exit 99")
            value = T.import_directory(root, source, "skill")
            self.assertFalse(value["items"][0]["enabled"])
            T.save_settings(root, {"config": "{}"})
            codex = Path(temporary) / "codex"
            args = T.apply_tools(root, codex, "codex")
            self.assertIn("skills.config=", args[1])
            copies = list((codex / "skills").rglob("SKILL.md"))
            self.assertEqual(len(copies), 1)
            self.assertEqual(copies[0].read_text(), (source / "SKILL.md").read_text())
            claude = Path(temporary) / "claude"
            args = T.apply_tools(root, claude, "claude")
            plugin = Path(args[args.index("--plugin-dir") + 1])
            self.assertTrue((plugin / ".claude-plugin/plugin.json").is_file())
            self.assertEqual(len(list(plugin.rglob("SKILL.md"))), 1)
            T.save_settings(root, {"config": "{}", "disabled": [value["items"][0]["id"]]})
            self.assertNotIn("--plugin-dir", T.apply_tools(root, claude, "claude"))
            self.assertIn('"enabled" = false', T.apply_tools(root, codex, "codex")[1])
            self.assertTrue(copies[0].exists())  # Earlier sessions retain immutable files.

    def test_rejects_links_and_incompatible_transports_without_modifying_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "skill"
            source.mkdir()
            (source / "SKILL.md").write_text("sample")
            (source / "escape").symlink_to(Path(temporary))
            with self.assertRaises(ValueError):
                T.import_directory(Path(temporary) / "tools", source, "skill")
            self.assertEqual((source / "SKILL.md").read_text(), "sample")
        with self.assertRaises(ValueError): T.normalize_servers({"demo": {"url": "http://remote.test"}})
        with self.assertRaises(ValueError): T.native_servers({"demo": {"url": "https://remote.test", "type": "sse"}}, "codex")
        with patch.dict(os.environ, {"WB_DEMO_TOKEN": "test-auth"}):
            server = T.native_servers({"demo": {"url": "https://remote.test", "bearer_token_env_var": "WB_DEMO_TOKEN"}}, "claude")
        self.assertEqual(server["workbench_demo"]["headers"]["Authorization"], "Bearer test-auth")

    def test_enabled_claude_plugin_mcp_uses_private_component_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, source = Path(temporary) / "tools", Path(temporary) / "original"
            (source / ".claude-plugin").mkdir(parents=True)
            (source / ".claude-plugin/plugin.json").write_text('{"name":"demo","version":"1.0.0"}')
            (source / ".mcp.json").write_text(json.dumps({"demo": {"command": "must-not-run", "args": ["${CLAUDE_PLUGIN_ROOT}/main.py"]}}))
            T.import_directory(root, source, "plugin")
            T.save_settings(root, {"config": "{}"})
            args = T.apply_tools(root, Path(temporary) / "claude", "claude")
            config = json.loads(Path(args[args.index("--mcp-config") + 1]).read_text())
            (server,) = config["mcpServers"].values()
            self.assertIn(str(root / "components"), server["args"][0])
            self.assertNotIn(str(source), server["args"][0])

    def test_screen_mcp_stdio_handshake_and_declared_tool_without_capturing(self):
        script = Path(T.__file__).with_name("screen_mcp.py")
        requests = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05"}},
                    {"jsonrpc": "2.0", "method": "notifications/initialized"},
                    {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}]
        result = subprocess.run([sys.executable, str(script)], input="\n".join(map(json.dumps, requests)),
                                text=True, capture_output=True, check=True, timeout=10)
        responses = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(len(responses), 2)
        self.assertEqual(responses[1]["result"]["tools"][0]["name"], "capture_screen")
        self.assertEqual(responses[0]["id"], 1)
