import random
from itertools import product

import numpy
from django.test import SimpleTestCase
from frontend.tests.utils import round_floats
from matrixstore.matrix_ops import finalise_matrix, sparse_matrix
from matrixstore.row_grouper import RowGrouper


class TestGrouper(SimpleTestCase):
    # Show large diffs on assertEqual failures
    maxDiff = None

    def __init__(self, *args, **kwargs):
        super(TestGrouper, self).__init__(*args, **kwargs)
        self.random = random.Random()
        self.random.seed(507)
        self.rows = 16
        self.columns = 16
        self.shape = (self.rows, self.columns)

    def test_basic_sum_by_group(self):
        """
        Test grouping and summing on a matrix which is small enough to verify
        the results by hand
        """
        # fmt: off
        group_definition = [(0, "even"), (1, "odd"), (2, "even"), (3, "odd")]
        rows = [
            [1, 2, 3, 4],
            [2, 3, 4, 5],
            [3, 4, 5, 6],
            [4, 5, 6, 7]
        ]
        expected_value = [
            [4, 6, 8, 10],
            [6, 8, 10, 12]
        ]
        # fmt: on
        matrix = numpy.array(rows)
        row_grouper = RowGrouper(group_definition)
        grouped_matrix = row_grouper.sum(matrix)
        value = to_list_of_lists(grouped_matrix)
        self.assertEqual(value, expected_value)
        self.assertEqual(row_grouper.ids, ["even", "odd"])
        self.assertEqual(row_grouper.offsets, {"even": 0, "odd": 1})

    def test_basic_sum_one_group(self):
        """
        Test summing for a single group on a matrix which is small enough to
        verify the results by hand
        """
        # fmt: off
        group_definition = [(0, "even"), (1, "odd"), (2, "even"), (3, "odd")]
        rows = [
            [1, 2, 3, 4],
            [2, 3, 4, 5],
            [3, 4, 5, 6],
            [4, 5, 6, 7]
        ]
        expected_value = [4, 6, 8, 10]
        # fmt: on
        matrix = numpy.array(rows)
        row_grouper = RowGrouper(group_definition)
        group_sum = row_grouper.sum_one_group(matrix, "even")
        self.assertEqual(group_sum.tolist(), expected_value)

    def test_basic_get_group(self):
        """
        Test fetching members for a single group on a matrix which is small
        enough to verify the results by hand
        """
        # fmt: off
        group_definition = [(0, "even"), (1, "odd"), (2, "even"), (3, "odd")]
        rows = [
            [1, 2, 3, 4],
            [2, 3, 4, 5],
            [3, 4, 5, 6],
            [4, 5, 6, 7]
        ]
        expected_value = [
            [1, 2, 3, 4],
            [3, 4, 5, 6]
        ]
        # fmt: on
        matrix = numpy.array(rows)
        row_grouper = RowGrouper(group_definition)
        group = row_grouper.get_group(matrix, "even")
        self.assertEqual(to_list_of_lists(group), expected_value)

    def test_empty_group_produces_empty_matrix(self):
        """
        Test the empty group edge case
        """
        group_definition = []
        rows = [[1, 2, 3, 4], [2, 3, 4, 5], [3, 4, 5, 6], [4, 5, 6, 7]]
        matrix = numpy.array(rows)
        row_grouper = RowGrouper(group_definition)
        grouped_matrix = row_grouper.sum(matrix)
        value = to_list_of_lists(grouped_matrix)
        self.assertEqual(value, [])

    def test_sum_with_all_group_and_matrix_type_combinations(self):
        """
        Tests the `sum` method with every combination of group type and matrix
        type
        """
        test_cases = product(self.get_group_definitions(), self.get_matrices())
        for (group_name, group_definition), (matrix_name, matrix) in test_cases:
            with self.subTest(matrix=matrix_name, group=group_name):
                row_grouper = RowGrouper(group_definition)

                # Test summing all groups (the default if no groups specified)
                with self.subTest(group_ids=None):
                    grouped_matrix = row_grouper.sum(matrix)
                    values = to_list_of_lists(grouped_matrix)
                    # Calculate the same dict the boring way using pure Python
                    expected_values = self.sum_rows_by_group(group_definition, matrix)
                    # We need to round floats to account for differences
                    # between numpy and Python float rounding
                    self.assertEqual(
                        round_floats(values), round_floats(expected_values)
                    )

                # Test summing just specific groups by getting the last two
                # group ids in reverse order
                group_ids = row_grouper.ids[-1:-3:-1]
                with self.subTest(group_ids=group_ids):
                    grouped_matrix = row_grouper.sum(matrix, group_ids)
                    values = to_list_of_lists(grouped_matrix)
                    # Calculate the same dict the boring way using pure Python
                    expected_values = self.sum_rows_by_group(
                        group_definition, matrix, group_ids
                    )
                    # We need to round floats to account for differences
                    # between numpy and Python float rounding
                    self.assertEqual(
                        round_floats(values), round_floats(expected_values)
                    )

    def test_sum_one_group_with_all_group_and_matrix_type_combinations(self):
        """
        Tests the `sum_one_group` method with every combination of group type
        and matrix type
        """
        test_cases = product(self.get_group_definitions(), self.get_matrices())
        for (group_name, group_definition), (matrix_name, matrix) in test_cases:
            with self.subTest(matrix=matrix_name, group=group_name):
                row_grouper = RowGrouper(group_definition)
                # Calculate sums for all groups the boring way using pure
                # Python
                expected_values = self.sum_rows_by_group(group_definition, matrix)
                # Check the `sum_one_group` gives the expected answer for all
                # groups
                for offset, group_id in enumerate(row_grouper.ids):
                    expected_value = expected_values[offset]
                    value = row_grouper.sum_one_group(matrix, group_id)
                    # We need to round floats to account for differences
                    # between numpy and Python float rounding
                    self.assertEqual(
                        round_floats(value.tolist()), round_floats(expected_value)
                    )

    def test_get_group_with_all_group_and_matrix_type_combinations(self):
        """
        Tests the `get_group` method with every combination of group type
        and matrix type
        """
        test_cases = product(self.get_group_definitions(), self.get_matrices())
        for (group_name, group_definition), (matrix_name, matrix) in test_cases:
            with self.subTest(matrix=matrix_name, group=group_name):
                row_grouper = RowGrouper(group_definition)
                for group_id in row_grouper.ids:
                    with self.subTest(group_id=group_id):
                        # Get the group members the boring way using pure
                        # Python
                        expected_value = self.get_group(
                            group_definition, matrix, group_id
                        )
                        value = row_grouper.get_group(matrix, group_id)
                        self.assertEqual(to_list_of_lists(value), expected_value)

    def sum_rows_by_group(self, group_definition, matrix, group_ids=None):
        """
        Given a group definition and a matrix, calculate the column-wise totals
        for each group (just like `row_grouper.sum` would do)
        """
        group_totals = {}
        for row_offset, group_id in group_definition:
            # Initialise a new zero-valued row for this group, if we don't have
            # one already
            if group_id not in group_totals:
                group_totals[group_id] = [0.0] * self.columns
            for column_offset in range(self.columns):
                value = matrix[row_offset, column_offset]
                group_totals[group_id][column_offset] += value
        # If group IDs aren't supplied then we want all groups in lexical order
        if group_ids is None:
            group_ids = sorted(group_totals.keys())
        # Return the group totals as a list of lists
        return [group_totals[group_id] for group_id in group_ids]

    def get_group(self, group_definition, matrix, group_id):
        """
        Given a group definition, a matrix and a group id return all rows in
        the matrix which below to the specified group in their original order
        """
        selected_row_offsets = []
        for row_offset, this_group_id in group_definition:
            if group_id == this_group_id:
                selected_row_offsets.append(row_offset)
        selected_rows = []
        for row_offset in sorted(selected_row_offsets):
            row = [matrix[row_offset, j] for j in range(self.columns)]
            selected_rows.append(row)
        return selected_rows

    def get_matrices(self):
        for sparse, integer in product([True, False], repeat=2):
            name = "{structure}.{type}".format(
                structure="sparse" if sparse else "dense",
                type="integer" if integer else "float",
            )
            yield name, self.make_matrix(sparse, integer)

    def make_matrix(self, sparse, integer):
        matrix = sparse_matrix(self.shape, integer=integer)
        sample_density = 0.1 if sparse else 1
        for coords in self._random_coords(self.shape, sample_density):
            value = self.random.randrange(1024) if integer else random.random()
            matrix[coords] = value
        matrix = finalise_matrix(matrix)
        # Make sure we get back the type of matrix we're expecting
        assert hasattr(matrix, "todense") == sparse
        return matrix

    def _random_coords(self, shape, sample_density):
        rows, cols = shape
        size = rows * cols
        samples = max(1, int(size * sample_density))
        for n in self.random.sample(range(size), samples):
            i = int(n / cols)
            j = n % cols
            yield i, j

    def get_group_definitions(self):
        return [
            (
                "basic_partition",
                [(row, "odd" if row % 2 else "even") for row in range(self.rows)],
            ),
            (
                "one_group_with_all_rows",
                [(row, "everything") for row in range(self.rows)],
            ),
            (
                "some_rows_not_in_any_groups",
                [(row, "odd" if row % 2 else "even") for row in range(self.rows // 2)],
            ),
            (
                "some_rows_in_multiple_groups",
                [(row, "small") for row in range(self.rows // 2)]
                + [(row, "odd" if row % 2 else "even") for row in range(self.rows)],
            ),
            (
                "each_row_in_exactly_one_group",
                [(row, "row_%s" % row) for row in range(self.rows)],
            ),
            (
                "some_rows_in_exactly_one_group",
                [(row, "row_%s" % row) for row in range(self.rows) if row % 2],
            ),
        ]


def to_list_of_lists(matrix):
    """
    Convert a 2D matrix into a list of lists
    """
    return [
        [matrix[i, j] for j in range(matrix.shape[1])] for i in range(matrix.shape[0])
    ]
