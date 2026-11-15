"""Keep the GPT and DSH bundles byte-identical where they share files."""
import unittest
from pathlib import Path

CANONICAL = Path(__file__).resolve().parents[1]
DSH = CANONICAL.parent / "delegate-coding-dsh"
SHARED = (
    "scripts/delegate.py",
    "scripts/api_worker.py",
    "scripts/check_executor.py",
    "assets/assistants.json",
    "assets/task.example.json",
    "references/task-protocol.md",
    "references/cli-route.md",
    "references/desktop-route.md",
    "references/api-route.md",
    "references/runtime-paths.md",
)


@unittest.skipUnless(DSH.is_dir(), "DSH bundle is not installed beside this skill")
class BundleParityTests(unittest.TestCase):
    def test_shared_files_are_identical(self):
        for name in SHARED:
            with self.subTest(file=name):
                self.assertEqual((DSH / name).read_bytes(), (CANONICAL / name).read_bytes(),
                                 f"{name} drifted between the GPT and DSH bundles; copy the canonical file")

    def test_dsh_bundle_has_its_own_entry_point(self):
        entry = (DSH / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("name: delegate-coding", entry)
        self.assertIn("dsh", entry.lower())


if __name__ == "__main__":
    unittest.main()
