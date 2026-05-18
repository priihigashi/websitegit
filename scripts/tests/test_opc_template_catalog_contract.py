import json
import unittest
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "content_creator"))

import carousel_builder  # noqa: E402


class OpcTemplateCatalogContractTest(unittest.TestCase):
    def setUp(self):
        self.catalog = json.loads(
            (ROOT / "scripts" / "content_creator" / "opc_template_catalog.json").read_text()
        )
        self.bundles = json.loads(
            (ROOT / "scripts" / "content_creator" / "opc_template_bundles.json").read_text()
        )

    def test_every_catalog_key_has_render_function(self):
        for template_id in self.catalog:
            self.assertTrue(
                hasattr(carousel_builder, f"render_{template_id}"),
                f"catalog has {template_id} but carousel_builder has no render_{template_id}()",
            )

    def test_every_render_function_has_catalog_entry(self):
        render_ids = {
            name.removeprefix("render_")
            for name in dir(carousel_builder)
            if name.startswith("render_opc_")
        }
        self.assertEqual(render_ids, set(self.catalog))

    def test_bundle_templates_exist_in_catalog(self):
        for bundle_id, bundle in self.bundles.items():
            for template_id in bundle["always_include"] + bundle["middle_pool"]:
                self.assertIn(template_id, self.catalog, f"{bundle_id} references unknown {template_id}")
                self.assertIn(bundle_id, self.catalog[template_id].get("bundles", []))

    def test_bundle_slide_ranges_are_valid(self):
        for bundle in self.bundles.values():
            self.assertGreaterEqual(bundle["min_slides"], 4)
            self.assertLessEqual(bundle["max_slides"], 8)
            self.assertLessEqual(bundle["min_slides"], bundle["max_slides"])


if __name__ == "__main__":
    unittest.main()
