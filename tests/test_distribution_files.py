import tomllib
from pathlib import Path
from unittest import TestCase


class DistributionFileTests(TestCase):
    def test_config_template_is_bom_free_valid_toml(self):
        config_path = Path(__file__).resolve().parents[1] / "config.toml"
        raw = config_path.read_bytes()

        self.assertFalse(raw.startswith(b"\xef\xbb\xbf"))
        parsed = tomllib.loads(raw.decode("utf-8"))
        self.assertEqual(parsed["api"]["api_key"], "")
        self.assertEqual(parsed["plugin"]["config_version"], "1.1.0")
