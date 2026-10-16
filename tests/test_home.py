import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from halbach_connecthub import store as st


class TestHomeDir(unittest.TestCase):
    def run_with(self, env, make=()):
        with tempfile.TemporaryDirectory() as t:
            base = Path(t)
            for m in make:
                (base / m).mkdir()
            full = {"LOCALAPPDATA": str(base)}
            full.update({k: (v if v != "<base>" else str(base / "custom")) for k, v in env.items()})
            with mock.patch.dict(os.environ, full, clear=False):
                for k in ("HALBACH_HOME", "CONNECTHUB_HOME"):
                    if k not in env:
                        os.environ.pop(k, None)
                return st.home_dir().relative_to(base).as_posix() if str(st.home_dir()).startswith(str(base)) else str(st.home_dir())

    def test_fresh_install_uses_new_folder(self):
        self.assertEqual(self.run_with({}), "HalbachConnectHub")

    def test_existing_old_folder_keeps_being_used(self):
        self.assertEqual(self.run_with({}, make=["ConnectHub"]), "ConnectHub")

    def test_new_folder_wins_when_both_exist(self):
        self.assertEqual(self.run_with({}, make=["ConnectHub", "HalbachConnectHub"]), "HalbachConnectHub")

    def test_env_overrides(self):
        self.assertEqual(self.run_with({"HALBACH_HOME": "<base>"}, make=["ConnectHub"]), "custom")
        self.assertEqual(self.run_with({"CONNECTHUB_HOME": "<base>"}), "custom")

    def test_halbach_home_beats_old_variable(self):
        with tempfile.TemporaryDirectory() as t:
            with mock.patch.dict(os.environ, {"HALBACH_HOME": t + "/a", "CONNECTHUB_HOME": t + "/b"}):
                self.assertEqual(st.home_dir(), Path(t + "/a"))


if __name__ == "__main__":
    unittest.main()
