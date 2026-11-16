"""Small deterministic cases for the phase's delay and pool-reduction rules."""

from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import select_bbu_pools as planner


class MinimumDelayTests(unittest.TestCase):
    def test_consecutive_k_search_in_both_directions_and_at_boundary(self):
        rrhs = [planner.RRH(str(i), (i, 0), False) for i in range(40)]

        def layouts(rrhs, candidates, k_values):
            for k in range(1, 41):
                yield k, tuple(range(k)), (), (1.0 if k >= 31 else 10.0,) * len(rrhs)

        for start, expected in (
            (25, list(range(25, 32))),
            (34, [34, 33, 32, 31, 30]),
            (31, [31, 30]),
        ):
            with self.subTest(start=start), patch.object(planner, "farthest_first", side_effect=layouts), patch.object(planner, "MAX_LINK_DISTANCE_KM", 4.5):
                initial, selected, links, final, history = planner.search_pool_counts(rrhs, set(range(40)), start)
                self.assertEqual(initial, start)
                self.assertEqual([row["k"] for row in history], expected)
                self.assertEqual(len(final), 31)
                self.assertEqual(final, set(range(1, 32)))
                self.assertEqual(history[-1]["accepted"], start < 31)
                self.assertTrue(all(abs(a["k"]-b["k"]) == 1 for a, b in zip(history, history[1:])))

    def test_round_trip_units(self):
        with patch.object(planner, "PROPAGATION_SPEED_M_PER_S", 300_000_000):
            self.assertAlmostEqual(planner.round_trip_delay_us(4.5), 30)
            self.assertAlmostEqual(planner.round_trip_delay_us(30), 200)

    def test_delay_is_strict_but_distance_limit_is_inclusive(self):
        with patch.object(planner, "MAX_LINK_DISTANCE_KM", 4.5), patch.object(planner, "MAX_ROUND_TRIP_DELAY_US", 200):
            self.assertTrue(planner.fits_link_limits(4.5, 30))
            self.assertFalse(planner.fits_link_limits(4.50001, 30))
            self.assertFalse(planner.fits_link_limits(1, 200))
            self.assertTrue(planner.fits_link_limits(1, 199.999))

    def test_pruning_preserves_coverage_and_removes_redundancy(self):
        links = [
            [planner.Link(1, 0, 0, True), planner.Link(2, 1, 1, True), planner.Link(3, 9, 9, False)],
            [planner.Link(2, 0, 0, True), planner.Link(3, 1, 1, True), planner.Link(1, 9, 9, False)],
        ]
        active = planner.prune_pools(links, 3)
        self.assertLess(len(active), 3)
        for options in links:
            self.assertTrue(any(link.feasible and link.pool_number in active for link in options))
        for pool in active:
            self.assertTrue(any(
                {link.pool_number for link in options if link.feasible} & active == {pool}
                for options in links
            ))

    def test_minimum_delay_link_order_and_infeasible_options(self):
        rrhs = [planner.RRH("a", (0, 0), False), planner.RRH("b", (0.01, 0), False), planner.RRH("c", (1, 0), False)]
        with patch.object(planner, "MAX_LINK_DISTANCE_KM", 4.5):
            links = planner.build_links(rrhs, (2, 1, 0))
        self.assertEqual([link.pool_number for link in links[0]], [3, 2, 1])
        self.assertEqual([link.feasible for link in links[0]], [True, True, False])

    def test_shared_coordinates_prefer_real_candidate(self):
        rrhs = [planner.RRH("synthetic", (0, 0), True), planner.RRH("real", (0, 0), False)]
        self.assertEqual(planner.candidate_indices(rrhs), {1})


if __name__ == "__main__":
    unittest.main()
