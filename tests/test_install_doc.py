"""INSTALL.md tells the operator how many tools the connector should list.
That number drifted -- it said 13/17 while the code served 15/19, because
two read-only tools were added and the doc was not -- exactly the
multi-surface-value-without-a-guard drift the CLAUDE docs warn about. This
pins the two figures to what the tiers actually serve.

The count the connector shows is the sum of implemented tools across the
deployed tiers: without the container tier, the three zip tiers; with it,
all four. UNIMPLEMENTED is subtracted because a routed-but-unbuilt tool is
not listed.
"""

import os
import re

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_SENTENCE = re.compile(
    r"tool list should show \*\*(\d+)\*\* tools, or \*\*(\d+)\*\* once the container tier"
)


def _stated_counts():
    text = open(os.path.join(REPO_ROOT, "INSTALL.md")).read()
    m = _SENTENCE.search(text)
    assert m, (
        "INSTALL.md no longer states the connector tool count in the expected "
        "shape ('should show **N** tools, or **M** once the container tier') -- "
        "update this guard's regex if the wording changed on purpose"
    )
    return int(m.group(1)), int(m.group(2))


def _served_counts():
    import sys

    if REPO_ROOT not in sys.path:
        sys.path.insert(0, REPO_ROOT)
    from mcp_server.tiers import HANDLER_TIERS, UNIMPLEMENTED, tools_for

    without = sum(
        len([t for t in tools_for(tier) if t not in UNIMPLEMENTED])
        for tier in HANDLER_TIERS
        if tier != "stack-mutation-node"
    )
    with_container = sum(
        len([t for t in tools_for(tier) if t not in UNIMPLEMENTED]) for tier in HANDLER_TIERS
    )
    return without, with_container


class TestInstallDocToolCountMatchesTheTiers:
    def test_the_stated_counts_match_what_the_tiers_serve(self):
        stated_without, stated_with = _stated_counts()
        served_without, served_with = _served_counts()
        assert (stated_without, stated_with) == (served_without, served_with), (
            f"INSTALL.md says the connector shows {stated_without}/{stated_with} tools "
            f"(without/with the container tier), but the tiers serve "
            f"{served_without}/{served_with}. Update the sentence in INSTALL.md."
        )

    def test_the_container_tier_adds_exactly_the_difference(self):
        """Sanity on the two numbers themselves: the container tier's four
        tools are the whole gap between them."""
        served_without, served_with = _served_counts()
        assert served_with - served_without == 4, (
            "the container tier is documented as adding four tools "
            "(create_cluster, apply_cluster_update, preview_cluster_config, "
            f"finalize_cluster_build); it now adds {served_with - served_without}"
        )

    def test_the_guard_can_see_a_wrong_number(self):
        """Vacuity guard: prove the comparison fails on a mismatch, not only
        when the numbers happen to line up."""
        served_without, served_with = _served_counts()
        assert (served_without + 1, served_with) != (served_without, served_with)
