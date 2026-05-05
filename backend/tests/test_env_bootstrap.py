from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.app import env_loader
from backend.app.services.deepseek_client import DeepseekClient


class EnvBootstrapTests(unittest.TestCase):
    def test_project_dotenv_loader_reads_candidate_file(self) -> None:
        old_loaded = env_loader._LOADED
        old_loaded_paths = env_loader._LOADED_PATHS
        old_api_key = os.environ.get("DEEPSEEK_API_KEY")

        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                dotenv_path = Path(temp_dir) / ".env"
                dotenv_path.write_text("DEEPSEEK_API_KEY=test-key-from-dotenv\n", encoding="utf-8")

                os.environ.pop("DEEPSEEK_API_KEY", None)
                env_loader._LOADED = False
                env_loader._LOADED_PATHS = ()

                with patch.object(env_loader, "_candidate_env_paths", return_value=[dotenv_path]):
                    loaded_paths = env_loader.load_project_dotenv()

                self.assertIn(str(dotenv_path.resolve()), loaded_paths)
                self.assertEqual(os.environ.get("DEEPSEEK_API_KEY"), "test-key-from-dotenv")
        finally:
            env_loader._LOADED = old_loaded
            env_loader._LOADED_PATHS = old_loaded_paths
            if old_api_key is None:
                os.environ.pop("DEEPSEEK_API_KEY", None)
            else:
                os.environ["DEEPSEEK_API_KEY"] = old_api_key

    def test_deepseek_client_calls_dotenv_loader_before_reading_key(self) -> None:
        old_api_key = os.environ.get("DEEPSEEK_API_KEY")
        old_legacy_api_key = os.environ.get("DeepSeek_API_KEY")
        old_fallback_key = os.environ.get("DEEPSEEK_KEY")

        def fake_loader() -> tuple[str, ...]:
            os.environ["DEEPSEEK_API_KEY"] = "loaded-by-dotenv-loader"
            return ("fake/.env",)

        try:
            os.environ.pop("DEEPSEEK_API_KEY", None)
            os.environ.pop("DeepSeek_API_KEY", None)
            os.environ.pop("DEEPSEEK_KEY", None)

            with patch("backend.app.services.deepseek_client.load_project_dotenv", side_effect=fake_loader) as mocked:
                client = DeepseekClient()

            self.assertTrue(client.enabled)
            self.assertEqual(client.api_key, "loaded-by-dotenv-loader")
            mocked.assert_called_once()
        finally:
            if old_api_key is None:
                os.environ.pop("DEEPSEEK_API_KEY", None)
            else:
                os.environ["DEEPSEEK_API_KEY"] = old_api_key
            if old_legacy_api_key is None:
                os.environ.pop("DeepSeek_API_KEY", None)
            else:
                os.environ["DeepSeek_API_KEY"] = old_legacy_api_key
            if old_fallback_key is None:
                os.environ.pop("DEEPSEEK_KEY", None)
            else:
                os.environ["DEEPSEEK_KEY"] = old_fallback_key


if __name__ == "__main__":
    unittest.main()

