import json
import shutil
import tempfile
import unittest
from pathlib import Path

from core.ai_laser_gcode.material_repository import JsonMaterialRepository


class MaterialRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="material-repository-", dir=".tmp-test")
        self.addCleanup(shutil.rmtree, self.temp_dir, ignore_errors=True)
        self.path = Path(self.temp_dir) / "materials.json"
        self.repository = JsonMaterialRepository(self.path)

    def test_save_is_atomic_and_creates_backup_on_second_write(self):
        first = {"version": 1, "materials": {"木材": {"thicknesses": {}}}}
        second = {"version": 1, "materials": {"木材": {"thicknesses": {"3": {}}}}}

        self.repository.save(first)
        self.repository.save(second)

        self.assertEqual(self.repository.load(), second)
        self.assertEqual(json.loads(self.repository.backup_path.read_text(encoding="utf-8")), first)
        self.assertFalse(self.path.with_name("materials.json.tmp").exists())

    def test_import_merge_preserves_existing_material_and_merges_aliases(self):
        self.repository.save(
            {
                "version": 1,
                "materials": {
                    "木材": {
                        "aliases": ["木板"],
                        "thicknesses": {"3": {"engrave": {"raster": {"feed_rate": 1000}}}},
                    }
                },
            }
        )

        merged = self.repository.import_data(
            {
                "version": 1,
                "materials": {
                    "木材": {
                        "aliases": ["basswood"],
                        "thicknesses": {"5": {"engrave": {"raster": {"feed_rate": 800}}}},
                    },
                    "纸板": {"aliases": [], "thicknesses": {}},
                },
            }
        )

        self.assertEqual(merged["materials"]["木材"]["aliases"], ["木板", "basswood"])
        self.assertIn("3", merged["materials"]["木材"]["thicknesses"])
        self.assertIn("5", merged["materials"]["木材"]["thicknesses"])
        self.assertIn("纸板", merged["materials"])

    def test_export_returns_copy(self):
        payload = {"version": 1, "materials": {"木材": {"aliases": []}}}
        self.repository.save(payload)

        exported = self.repository.export_data()
        exported["materials"]["木材"]["aliases"].append("changed")

        self.assertEqual(self.repository.load()["materials"]["木材"]["aliases"], [])


if __name__ == "__main__":
    unittest.main()
