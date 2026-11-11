# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Contract for the detection seed-carry state."""

import pytest

from adapt.contracts import ContractViolation, check_seed_carry

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("ages", [(), (0,), (0, 1, 2)])
def test_seed_carry_accepts_tuples_of_non_negative_ints(ages):
    check_seed_carry(ages)


@pytest.mark.parametrize("bad", [[0, 1], (0.5,), (-1,), (True,), None])
def test_seed_carry_rejects_non_tuple_or_non_int_or_negative(bad):
    with pytest.raises(ContractViolation):
        check_seed_carry(bad)
