import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "content_creator"))

google = types.ModuleType("google")
google_oauth2 = types.ModuleType("google.oauth2")
google_oauth2_credentials = types.ModuleType("google.oauth2.credentials")
google_oauth2_credentials.Credentials = object
googleapiclient = types.ModuleType("googleapiclient")
googleapiclient_discovery = types.ModuleType("googleapiclient.discovery")
googleapiclient_http = types.ModuleType("googleapiclient.http")
googleapiclient_discovery.build = lambda *args, **kwargs: None
googleapiclient_http.MediaIoBaseDownload = object
googleapiclient_http.MediaFileUpload = object
sys.modules.setdefault("google", google)
sys.modules.setdefault("google.oauth2", google_oauth2)
sys.modules.setdefault("google.oauth2.credentials", google_oauth2_credentials)
sys.modules.setdefault("googleapiclient", googleapiclient)
sys.modules.setdefault("googleapiclient.discovery", googleapiclient_discovery)
sys.modules.setdefault("googleapiclient.http", googleapiclient_http)

import carousel_builder  # noqa: E402
import carousel_reviewer  # noqa: E402


class NewsTemplateDispatchTest(unittest.TestCase):
    def test_builder_records_shared_news_template_rendered(self):
        content = {"_template_key": "illustrated"}

        with patch.object(
            carousel_builder,
            "_build_news_shared_template_html",
            return_value="/tmp/cover.html",
        ) as shared:
            result = carousel_builder.build_html(content, "brazil", "slug", "/tmp/work")

        self.assertEqual(result, "/tmp/cover.html")
        self.assertEqual(content["_template_key_rendered"], "illustrated")
        shared.assert_called_once()

    def test_builder_records_native_news_template_rendered(self):
        content = {}

        with patch.object(
            carousel_builder,
            "_build_brazil_html",
            return_value="/tmp/native.html",
        ) as native:
            result = carousel_builder.build_html(content, "usa", "slug", "/tmp/work")

        self.assertEqual(result, "/tmp/native.html")
        self.assertEqual(content["_template_key_rendered"], "native")
        native.assert_called_once()

    def test_reviewer_flags_resolved_rendered_mismatch(self):
        issues = carousel_reviewer.check_news_template_dispatch(
            {
                "_template_key_resolved": "cutout",
                "_template_key_rendered": "native",
            },
            "brazil",
        )

        self.assertTrue(any("resolved='cutout' but rendered='native'" in issue for issue in issues))

    def test_reviewer_passes_matching_shared_template(self):
        issues = carousel_reviewer.check_news_template_dispatch(
            {
                "_template_key_resolved": "cutout",
                "_template_key_rendered": "cutout",
            },
            "brazil",
        )

        self.assertEqual(issues, [])


if __name__ == "__main__":
    unittest.main()
