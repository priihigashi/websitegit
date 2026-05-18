"""SH-049 correction (2026-05-17) — OPC AI cascade policy.

Per Priscila 2026-05-17: SH-049 was over-broad. For OPC niche:
  ALLOWED:  NB2, Seedream 4.5, Gemini (last resort)
  BLOCKED:  DALL-E 3 (cartoonish), Seedream 5.0, SDXL

This test verifies:
  - The skip list used by carousel_builder is exactly the 3 blocked providers.
  - image_providers.generate_ai_image() honors the skip list (already-tested mechanism,
    re-asserted here as a guardrail).
  - The OPC cascade after skips matches Priscila's approved order.

Real fetch calls are mocked — this is a policy/wiring test, not an integration test.
"""

import os
import unittest
from pathlib import Path
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "content_creator"))

import image_providers  # noqa: E402


# Source of truth: the skip list embedded in carousel_builder.py at both cascades.
OPC_AI_SKIP = ["dall-e-3", "seedream-5.0", "sdxl"]
OPC_AI_ALLOWED = ["nb2", "seedream-4.5", "gemini"]


class OpcAiCascadeCorrectedTest(unittest.TestCase):
    """SH-049 correction policy + image_providers skip-list mechanism."""

    def test_default_cascade_contains_all_six_providers(self):
        """Sanity: DEFAULT_AI_CASCADE has every named provider."""
        cascade_lower = {p.lower() for p in image_providers.DEFAULT_AI_CASCADE}
        self.assertSetEqual(
            cascade_lower,
            {"nb2", "seedream-4.5", "seedream-5.0", "gemini", "sdxl", "dall-e-3"},
        )

    def test_opc_skip_list_blocks_exactly_the_three_disallowed(self):
        """OPC skip list = exactly {dall-e-3, seedream-5.0, sdxl}."""
        self.assertSetEqual(set(OPC_AI_SKIP), {"dall-e-3", "seedream-5.0", "sdxl"})

    def test_opc_allowed_list_is_exactly_three_providers(self):
        """OPC allowed = exactly {nb2, seedream-4.5, gemini}."""
        self.assertSetEqual(set(OPC_AI_ALLOWED), {"nb2", "seedream-4.5", "gemini"})

    def test_opc_skip_and_allowed_partition_the_default_cascade(self):
        """Every provider in DEFAULT_AI_CASCADE is either skipped or allowed for OPC."""
        cascade_lower = {p.lower() for p in image_providers.DEFAULT_AI_CASCADE}
        partition = set(OPC_AI_SKIP) | set(OPC_AI_ALLOWED)
        self.assertSetEqual(cascade_lower, partition)

    def test_generate_ai_image_honors_opc_skip_list(self):
        """image_providers.generate_ai_image() must skip exactly the OPC-blocked providers
        and call providers in DEFAULT_AI_CASCADE order until one returns truthy."""
        calls = []

        def fake_fn_factory(slug):
            def fn(prompt, work_dir, filename):
                calls.append(slug)
                return ""  # always fail so cascade walks to end
            return fn

        fake_provider_fns = {
            slug: fake_fn_factory(slug) for slug in image_providers.DEFAULT_AI_CASCADE
        }

        with patch.object(image_providers, "_AI_PROVIDER_FN", fake_provider_fns):
            rel, used = image_providers.generate_ai_image(
                "test prompt", "/tmp", "test.png", skip_providers=OPC_AI_SKIP
            )

        self.assertEqual(rel, "")
        self.assertEqual(used, "")
        for blocked in OPC_AI_SKIP:
            self.assertNotIn(blocked, calls, f"OPC must skip {blocked} but it was called")
        for allowed in OPC_AI_ALLOWED:
            self.assertIn(allowed, calls, f"OPC must try {allowed} but it was not called")

    def test_generate_ai_image_returns_first_truthy_provider(self):
        """When NB2 returns a path, the cascade stops there — no Seedream 4.5 / Gemini calls."""
        calls = []

        def succeed_nb2(prompt, work_dir, filename):
            calls.append("nb2")
            return "resources/images/test.png"

        def must_not_run(prompt, work_dir, filename):
            calls.append("UNREACHED")
            return ""

        fake_provider_fns = {
            "nb2": succeed_nb2,
            "seedream-4.5": must_not_run,
            "seedream-5.0": must_not_run,
            "gemini": must_not_run,
            "sdxl": must_not_run,
            "dall-e-3": must_not_run,
        }

        with patch.object(image_providers, "_AI_PROVIDER_FN", fake_provider_fns):
            rel, used = image_providers.generate_ai_image(
                "test prompt", "/tmp", "test.png", skip_providers=OPC_AI_SKIP
            )

        self.assertEqual(rel, "resources/images/test.png")
        self.assertEqual(used, "nb2")
        self.assertEqual(calls, ["nb2"])  # zero further providers called

    def test_non_opc_niche_skip_none_runs_full_cascade(self):
        """News (Brazil/USA) passes skip_providers=None and tries every provider."""
        calls = []

        def fake_fn(slug):
            def fn(prompt, work_dir, filename):
                calls.append(slug)
                return ""
            return fn

        fake_provider_fns = {slug: fake_fn(slug) for slug in image_providers.DEFAULT_AI_CASCADE}

        with patch.object(image_providers, "_AI_PROVIDER_FN", fake_provider_fns):
            image_providers.generate_ai_image(
                "test prompt", "/tmp", "test.png", skip_providers=None
            )

        # Order matches DEFAULT_AI_CASCADE, no skips
        self.assertEqual(calls, list(image_providers.DEFAULT_AI_CASCADE))


if __name__ == "__main__":
    unittest.main()
