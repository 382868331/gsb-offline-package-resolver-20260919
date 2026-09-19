"""Tests for half-open interval unions and intersections."""

import unittest

from resolver.intervals import (
    ANY,
    contains,
    intersect,
    make_constraint,
)


class IntervalTests(unittest.TestCase):
    def test_half_open_bounds(self):
        c = make_constraint([[(1, 0, 0), (2, 0, 0)]])
        self.assertTrue(contains(c, (1, 0, 0)))
        self.assertTrue(contains(c, (1, 9, 9)))
        self.assertFalse(contains(c, (2, 0, 0)))
        self.assertFalse(contains(c, (0, 9, 9)))

    def test_triple_ordering(self):
        c = make_constraint([[(1, 2, 3), (1, 3, 0)]])
        self.assertFalse(contains(c, (1, 2, 2)))
        self.assertTrue(contains(c, (1, 2, 3)))
        self.assertTrue(contains(c, (1, 2, 99)))
        self.assertFalse(contains(c, (1, 3, 0)))

    def test_union_merges_overlaps(self):
        c = make_constraint(
            [[(1, 0, 0), (2, 0, 0)], [(1, 5, 0), (3, 0, 0)]]
        )
        self.assertEqual(c, [((1, 0, 0), (3, 0, 0))])

    def test_union_keeps_disjoint(self):
        c = make_constraint(
            [[(1, 0, 0), (2, 0, 0)], [(3, 0, 0), (4, 0, 0)]]
        )
        self.assertEqual(len(c), 2)
        self.assertFalse(contains(c, (2, 5, 0)))

    def test_intersection_basic(self):
        a = make_constraint([[(1, 0, 0), (3, 0, 0)]])
        b = make_constraint([[(2, 0, 0), (4, 0, 0)]])
        got = intersect(a, b)
        self.assertEqual(got, [((2, 0, 0), (3, 0, 0))])

    def test_intersection_multi_ranges(self):
        a = make_constraint(
            [[(1, 0, 0), (2, 0, 0)], [(3, 0, 0), (4, 0, 0)]]
        )
        b = make_constraint(
            [[(0, 0, 0), (1, 5, 0)], [(3, 5, 0), (5, 0, 0)]]
        )
        got = intersect(a, b)
        self.assertEqual(
            got,
            [((1, 0, 0), (1, 5, 0)), ((3, 5, 0), (4, 0, 0))],
        )

    def test_intersection_empty(self):
        a = make_constraint([[(1, 0, 0), (2, 0, 0)]])
        b = make_constraint([[(2, 0, 0), (3, 0, 0)]])
        self.assertEqual(intersect(a, b), [])

    def test_any_covers_everything(self):
        self.assertTrue(contains(ANY, (0, 0, 0)))
        self.assertTrue(contains(ANY, (999, 999, 999)))

    def test_invalid_ranges_rejected(self):
        with self.assertRaises(ValueError):
            make_constraint([[(2, 0, 0), (1, 0, 0)]])
        with self.assertRaises(ValueError):
            make_constraint([[(1, 0, 0), (1, 0, 0)]])


if __name__ == "__main__":
    unittest.main()
