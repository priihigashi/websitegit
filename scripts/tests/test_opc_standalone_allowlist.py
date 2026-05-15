"""PR0 (2026-05-15) — OPC_STANDALONE_ALLOWLIST behavior.

Proves the SH-158 rollback in opc_template_chooser.plan_carousel_slides()
honors a per-template allowlist:

  - Empty allowlist (default)        -> standalones still get rolled back to tips.
  - Allowlist contains picked ID     -> that standalone survives unchanged.
  - Allowlist contains other IDs     -> non-listed standalones still rolled back.

Background: docs/research/opc_standalone_failure_mode.md.
"""

import os
import unittest
from contextlib import contextmanager
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "content_creator"))

from opc_template_chooser import plan_carousel_slides  # noqa: E402


# Topic verified by test_opc_comparison_parity.py to pick opc_four_card_grid in slide 3.
COMPARISON_TOPIC = "Concrete vs Pavers: Which Wins for Driveways?"


@contextmanager
def env(**overrides):
    """Temporarily override env vars; restore on exit."""
    snapshot = {k: os.environ.get(k) for k in overrides}
    try:
        for k, v in overrides.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        yield
    finally:
        for k, v in snapshot.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _slide3_template(plan):
    return plan["slides"][2]["template_id"]


def _slide3_original(plan):
    return plan["slides"][2].get("_original_template_id")


class OpcStandaloneAllowlistTest(unittest.TestCase):
    """SH-158 rollback + PR0 allowlist interaction."""

    def test_empty_allowlist_rolls_back_standalone_to_tip(self):
        """Default behavior preserved: opc_four_card_grid -> tip fallback."""
        with env(OPC_DISABLE_STANDALONES="1", OPC_STANDALONE_ALLOWLIST=""):
            plan = plan_carousel_slides(COMPARISON_TOPIC)
        self.assertEqual(plan["status"], "passed")
        # Picked standalone got rewritten to a tip family member.
        self.assertTrue(_slide3_template(plan).startswith("opc_tip_"))
        # Original ID preserved on the slide for diagnostics.
        self.assertEqual(_slide3_original(plan), "opc_four_card_grid")

    def test_allowlisted_standalone_survives_rollback(self):
        """opc_four_card_grid in allowlist -> kept as standalone."""
        with env(
            OPC_DISABLE_STANDALONES="1",
            OPC_STANDALONE_ALLOWLIST="opc_four_card_grid",
        ):
            plan = plan_carousel_slides(COMPARISON_TOPIC)
        self.assertEqual(plan["status"], "passed")
        self.assertEqual(_slide3_template(plan), "opc_four_card_grid")
        # No rewrite occurred -> _original_template_id should not be set.
        self.assertIsNone(_slide3_original(plan))

    def test_unrelated_allowlist_entry_does_not_protect_other_standalones(self):
        """Allowlist with opc_statement only -> opc_four_card_grid still rolled back."""
        with env(
            OPC_DISABLE_STANDALONES="1",
            OPC_STANDALONE_ALLOWLIST="opc_statement,opc_material_profile",
        ):
            plan = plan_carousel_slides(COMPARISON_TOPIC)
        self.assertEqual(plan["status"], "passed")
        self.assertTrue(_slide3_template(plan).startswith("opc_tip_"))
        self.assertEqual(_slide3_original(plan), "opc_four_card_grid")

    def test_allowlist_whitespace_and_blanks_are_tolerated(self):
        """' opc_four_card_grid , , ' must still allow the template."""
        with env(
            OPC_DISABLE_STANDALONES="1",
            OPC_STANDALONE_ALLOWLIST=" opc_four_card_grid , , ",
        ):
            plan = plan_carousel_slides(COMPARISON_TOPIC)
        self.assertEqual(_slide3_template(plan), "opc_four_card_grid")

    def test_disable_off_ignores_allowlist_entirely(self):
        """OPC_DISABLE_STANDALONES=0 -> no rollback regardless of allowlist."""
        with env(OPC_DISABLE_STANDALONES="0", OPC_STANDALONE_ALLOWLIST=""):
            plan = plan_carousel_slides(COMPARISON_TOPIC)
        self.assertEqual(_slide3_template(plan), "opc_four_card_grid")
        self.assertIsNone(_slide3_original(plan))


if __name__ == "__main__":
    unittest.main()
