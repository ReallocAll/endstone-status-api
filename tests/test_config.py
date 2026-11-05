from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from endstone_status_api.config import load_config, APIConfig


class ConfigTest(unittest.TestCase):
    def test_first_install_creates_private_bind_and_conservative_exposure(self):
        with tempfile.TemporaryDirectory() as path:
            config, error = load_config(Path(path))
            self.assertIsNone(error)
            self.assertEqual(config.http.host, "127.0.0.1")
            self.assertFalse(config.endpoints.chat)
            self.assertFalse(config.endpoints.players)
            self.assertTrue((Path(path) / "config.toml").is_file())

    def test_unknown_field_fails_without_overwriting(self):
        with tempfile.TemporaryDirectory() as path:
            file = Path(path) / "config.toml"
            original = "[endpoints]\nchat = true\nunknown = false\n"
            file.write_text(original)
            _, error = load_config(Path(path))
            self.assertIn("unknown", error)
            self.assertEqual(file.read_text(), original)

    def test_invalid_type_and_cors_are_rejected(self):
        for source in (
            '[endpoints]\nchat = "true"\n',
            '[http]\ncors_origins = ["*", "https://example.com"]\n',
            '[http]\ncors_origins = ["https://example.com/path"]\n',
            '[chat]\nmax_messages = -1\n',
            '[endpoints]\nchat=true\n[chat]\nmax_messages=0\n',
        ):
            with self.subTest(source=source), tempfile.TemporaryDirectory() as path:
                (Path(path) / "config.toml").write_text(source)
                _, error = load_config(Path(path))
                self.assertIsNotNone(error)
