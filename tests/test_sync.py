"""Run the interface regression tests with Node, without a browser or network."""
import shutil
import subprocess
import unittest
from pathlib import Path


@unittest.skipUnless(shutil.which("node"), "Node is needed for interface tests")
class InterfaceTests(unittest.TestCase):
    def test_interface(self):
        result = subprocess.run(["node", "--test", str(Path(__file__).with_name("test_sync.js"))],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
