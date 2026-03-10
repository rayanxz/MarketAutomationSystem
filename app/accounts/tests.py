from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from django.test import SimpleTestCase


class SettingsSecurityTests(SimpleTestCase):
    def _run_prod_settings_import(self, *, env_overrides: dict[str, str | None]) -> subprocess.CompletedProcess[str]:
        app_dir = Path(__file__).resolve().parents[1]
        env = os.environ.copy()
        for k, v in env_overrides.items():
            if v is None:
                env.pop(k, None)
            else:
                env[k] = v
        return subprocess.run(
            [sys.executable, "-c", "import marketpos.settings_prod"],
            cwd=str(app_dir),
            env=env,
            capture_output=True,
            text=True,
        )

    def test_settings_prod_requires_secret_key_env(self):
        res = self._run_prod_settings_import(
            env_overrides={
                "DJANGO_SECRET_KEY": None,
                "DJANGO_DEBUG": None,
            }
        )
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("DJANGO_SECRET_KEY", f"{res.stdout}\n{res.stderr}")

    def test_settings_prod_rejects_debug_env_enable(self):
        res = self._run_prod_settings_import(
            env_overrides={
                "DJANGO_SECRET_KEY": "test-secret-value",
                "DJANGO_DEBUG": "1",
            }
        )
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("DJANGO_DEBUG cannot be enabled", f"{res.stdout}\n{res.stderr}")

    def test_settings_prod_imports_cleanly_with_secret_and_debug_off(self):
        res = self._run_prod_settings_import(
            env_overrides={
                "DJANGO_SECRET_KEY": "test-secret-value",
                "DJANGO_DEBUG": "0",
            }
        )
        self.assertEqual(res.returncode, 0, f"{res.stdout}\n{res.stderr}")
