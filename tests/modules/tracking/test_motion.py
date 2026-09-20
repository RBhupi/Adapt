# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Kinematic gate: absolute speed cap and a floored acceleration cap."""

import pytest

from adapt.modules.tracking.models import TrackingError
from adapt.modules.tracking.motion import MotionValidator

pytestmark = pytest.mark.unit

DT_S = 270.0


def _check(validator: MotionValidator, speed_ms: float, previous_speed: float | None):
    return validator.check(0.0, 0.0, speed_ms * DT_S, 0.0, DT_S, previous_speed)


def test_floor_admits_a_step_above_the_relative_cap():
    decision = _check(MotionValidator(40.0, 3.0, 20.0), 13.3, previous_speed=3.8)
    assert decision.ok  # 13.3 > 3 x 3.8 = 11.4 but <= floor 20


def test_floor_does_not_admit_a_step_above_itself():
    decision = _check(MotionValidator(40.0, 3.0, 20.0), 25.0, previous_speed=3.8)
    assert decision.code is TrackingError.ACCELERATION_EXCEEDED


def test_relative_cap_still_applies_above_the_floor():
    validator = MotionValidator(40.0, 3.0, 20.0)  # previous 10 m/s -> cap 30
    assert _check(validator, 25.0, previous_speed=10.0).ok
    assert _check(validator, 35.0, previous_speed=10.0).code is TrackingError.ACCELERATION_EXCEEDED


def test_absolute_cap_is_independent_of_the_floor():
    decision = _check(MotionValidator(40.0, 3.0, 100.0), 45.0, previous_speed=None)
    assert decision.code is TrackingError.VELOCITY_EXCEEDED
