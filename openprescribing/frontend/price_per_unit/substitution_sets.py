"""
Defines a list of "substitution sets" which are groups of presentations which
can, in our opinion, be substituted for one another.

We build these lists based on the contents of dm+d, using a single large SQL query to
fetch pairs of substitutable presentations. Sometimes these substitutions involve a
change in formulation (e.g. tablets to capsules) which we want to highlight so we also
fetch a string giving the formulation of each presentation.
"""

import functools
import hashlib
from pathlib import Path

from django.db import connection
from django.db.models import Max
from dmd.models import PriceInfo
from matrixstore.db import get_db


# This would be a good candidate for a dataclass when we move to Python 3.7
class SubstitutionSet:
    """
    Represents a set of presentations which we believe can be reasonably
    substituted for one another
    """

    def __init__(self, id, presentations, name=None, formulation_swaps=None):
        # The code which identifies this set of substitutions. As it happens we
        # define this as the lexically smallest generic BNF code in the set but the
        # rest of the codebase just treats this as an opaque string identifier.
        self.id = id
        # The BNF codes for all presentations contained within the set
        self.presentations = presentations
        # Human readable name to represent this set (usually the name of the generic)
        self.name = name
        # Where a substitution set involves multiple formulations (e.g tablets and
        # capsules) this is a string representating a short, human-readable
        # description of the formulation swaps involved e.g 'Tab / Cap'. If no
        # formulation changes are involved then this is None.
        self.formulation_swaps = formulation_swaps
        # `cache_key` is used to identify the state of this SubstitutionSet for
        # caching purposes i.e.  SubstitutionSet instances should have the same
        # cache_key if and only if they have same list of presentations
        hashobj = hashlib.md5(str(self.presentations).encode("utf8"))
        self.cache_key = hashobj.digest()


class DictWithCacheID(dict):
    """
    Dict subclass which adds a `cache_key` attribute which is just the hash of
    the cache_keys of its values
    """

    cache_key = None

    def __new__(cls, items):
        instance = dict.__new__(cls, items)
        hashobj = hashlib.md5()
        for key, value in items:
            hashobj.update(value.cache_key)
        instance.cache_key = hashobj.digest()
        return instance


def cache_until_dmd_import(fn):
    """
    Cache the results of `fn` until the next dm+d import
    """

    cache = {"key": None}

    @functools.wraps(fn)
    def wrapper():
        # We're using the maximum ID of the PriceInfo table as the key here because this
        # is fast to check and, as far as I can tell, pretty much guaranteed to change
        # when we import new data. I'd rather check this than be forced to rely on the
        # ImportLog table.
        new_key = PriceInfo.objects.aggregate(Max("id", default=0))
        if cache["key"] == new_key:
            return cache["value"]
        else:
            cache["value"] = fn()
            cache["key"] = new_key
            return cache["value"]

    # Provide the same API as the old `@memoize` decorator so tests can use it
    wrapper.cache_clear = lambda: cache.update(key=None, value=None)

    return wrapper


def get_all_prescribed_bnf_codes():
    return {row[0] for row in get_db().query("SELECT bnf_code FROM presentation")}


@cache_until_dmd_import
def get_substitution_sets():
    prescribed_codes = get_all_prescribed_bnf_codes()
    equivalent_codes = []
    formulations = {}
    names = {}

    for code_1, name_1, formulation_1, code_2, name_2, formulation_2 in get_swaps():
        equivalent_codes.append((code_1, code_2))
        formulations[code_1] = formulation_1
        formulations[code_2] = formulation_2
        names[code_1] = name_1
        names[code_2] = name_2

    substitution_sets = []

    for code_group in groups_from_pairs(equivalent_codes):
        # Ignore any codes for which we don't have prescribing data
        code_group = prescribed_codes.intersection(code_group)
        if not code_group:
            continue

        # Pick a "representative" code whose name we will use as the name for the
        # substitution set
        primary_code = get_representative_code(code_group)

        # Get the unique formulations involved in this substitution set
        formulations_for_group = {
            formulations[code] for code in code_group if formulations[code]
        }
        if len(formulations_for_group) > 1:
            swap_description = " / ".join(sorted(formulations_for_group))
        else:
            swap_description = None

        substitution_sets.append(
            SubstitutionSet(
                id=primary_code,
                name=names[primary_code],
                presentations=sorted(code_group),
                formulation_swaps=swap_description,
            )
        )

    return DictWithCacheID([(s.id, s) for s in substitution_sets])


def get_representative_code(bnf_codes):
    return sorted(bnf_codes, key=prefer_generic_codes)[0]


def prefer_generic_codes(bnf_code):
    # Sorts non-generic codes _after_ generic ones
    return (bnf_code[9:11] != "AA", bnf_code)


@cache_until_dmd_import
def get_substitution_sets_by_presentation():
    """
    Build a mapping of all substitutable presentations to the substitution set
    which contains them
    """
    index = {}
    for substitution_set in get_substitution_sets().values():
        for presentation in substitution_set.presentations:
            index[presentation] = substitution_set
    return index


def groups_from_pairs(pairs):
    """
    Accepts a list of pairs and combines any overlapping pairs into groups

    >>> list(groups_from_pairs([
    ...     (1, 2),
    ...     (3, 4),
    ...     (5, 6),
    ...     (1, 3),
    ... ]))
    [[1, 2, 3, 4], [5, 6]]

    In set theoretic terms, `pairs` is an equivalence relation and `groups` are
    the equivalence classes it induces.
    """
    groups = {}
    for element, other_element in pairs:
        group = groups.setdefault(element, [element])
        other_group = groups.get(other_element, [other_element])
        if other_group is not group:
            group.extend(other_group)
            for member in other_group:
                groups[member] = group
    for element, group in groups.items():
        # Each group will occur multiple times in this loop, once for each of
        # its elements. But we want to return just the unique groups, so we
        # pick (arbitrarily) the first element of each group to identify it
        # with.
        if element == group[0]:
            yield group


def get_swaps():
    sql = Path(__file__).parent.joinpath("swaps_postgres.sql").read_text()
    with connection.cursor() as cursor:
        cursor.execute(sql)
        column_indices = get_indices(
            cursor.description,
            [
                "code",
                "name",
                "formulation",
                "alternative_code",
                "alternative_name",
                "alternative_formulation",
            ],
        )
        return [tuple(row[i] for i in column_indices) for row in cursor.fetchall()]


def get_indices(cursor_description, columns):
    # Find the target column names (case-insensitively) in the cursor description object
    # and return the column indices
    all_indices = {}
    for i, c in enumerate(cursor_description):
        all_indices.setdefault(c.name.lower(), []).append(i)

    indices = []
    for name in columns:
        matches = all_indices.get(name.lower(), ())
        assert len(matches) == 1
        indices.append(matches[0])

    return indices
