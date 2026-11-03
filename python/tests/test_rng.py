"""Tests for the deterministic, platform-independent random number generator."""

import math
import statistics

import pytest

from udeshi_markets.simulation import DeterministicRng, deterministic_log


def test_deterministic_log_matches_math_log_to_a_few_ulps() -> None:
    values = [
        1.0,
        2.0,
        0.5,
        math.e,
        1e-300,
        5e-324,
        1.7976931348623157e308,
        0.7071067811865476,
        1.4142135623730951,
    ]
    values += [1.0 + k * 1e-3 for k in range(-999, 1000, 7)]
    values += [10.0**k for k in range(-300, 301, 13)]
    for x in values:
        expected = math.log(x)
        actual = deterministic_log(x)
        tolerance = 4 * math.ulp(expected) if expected != 0.0 else 1e-300
        assert abs(actual - expected) <= tolerance, x


@pytest.mark.parametrize("x", [0.0, -1.0, math.inf, math.nan])
def test_deterministic_log_rejects_invalid_arguments(x: float) -> None:
    with pytest.raises(ValueError, match="positive and finite"):
        deterministic_log(x)


def test_streams_are_pinned_across_platforms_and_versions() -> None:
    # Golden values: any change here breaks reproducibility of every recorded simulation.
    rng = DeterministicRng(42)
    assert [rng.next_u64() for _ in range(3)] == [
        13679457532755275413,
        2949826092126892291,
        5139283748462763858,
    ]
    rng = DeterministicRng.derive(7, "participant", 3)
    draws = (rng.random(), rng.randint(1, 6), rng.normal(), rng.exponential(2.0))
    assert draws == (
        0.8711260141407213,
        4,
        -1.1445649836442424,
        0.02505234500430746,
    )
    rng = DeterministicRng(2024)
    assert [rng.uniform(-1.0, 1.0) for _ in range(2)] == [0.24553107329221935, -0.8055361830246146]
    assert [rng.choice("abcdefg") for _ in range(5)] == ["d", "f", "e", "a", "e"]
    assert [rng.bernoulli(0.3) for _ in range(6)] == [False, True, False, False, False, False]


def test_same_seed_same_sequence_and_streams_are_independent() -> None:
    a, b = DeterministicRng(123), DeterministicRng(123)
    assert [a.random() for _ in range(100)] == [b.random() for _ in range(100)]
    one = DeterministicRng.derive(9, "participant", 1)
    two = DeterministicRng.derive(9, "participant", 2)
    assert [one.next_u64() for _ in range(5)] != [two.next_u64() for _ in range(5)]
    assert (
        DeterministicRng.derive(9, "a", "bc").next_u64()
        != DeterministicRng.derive(9, "ab", "c").next_u64()
    )


def test_uniform_and_randint_ranges() -> None:
    rng = DeterministicRng(5)
    floats = [rng.random() for _ in range(10_000)]
    assert all(0.0 <= x < 1.0 for x in floats)
    ints = [rng.randint(-3, 3) for _ in range(14_000)]
    assert set(ints) == set(range(-3, 4))
    for value in range(-3, 4):  # Roughly uniform: each of 7 values about 2000 times.
        assert 1700 < ints.count(value) < 2300
    assert {rng.randint(0, 7) for _ in range(500)} == set(range(8))  # Power-of-two span.
    assert rng.randint(4, 4) == 4
    assert 2.0 <= rng.uniform(2.0, 3.0) < 3.0


def test_distribution_moments() -> None:
    rng = DeterministicRng(2024)
    normals = [rng.normal(10.0, 2.0) for _ in range(20_000)]
    assert statistics.fmean(normals) == pytest.approx(10.0, abs=0.06)
    assert statistics.stdev(normals) == pytest.approx(2.0, abs=0.05)
    exponentials = [rng.exponential(4.0) for _ in range(20_000)]
    assert all(x >= 0.0 for x in exponentials)
    assert statistics.fmean(exponentials) == pytest.approx(0.25, abs=0.01)
    hits = sum(rng.bernoulli(0.3) for _ in range(20_000))
    assert hits / 20_000 == pytest.approx(0.3, abs=0.015)
    assert rng.choice(["a", "b", "c"]) in {"a", "b", "c"}


def test_argument_validation() -> None:
    rng = DeterministicRng(1)
    with pytest.raises(ValueError, match="seed"):
        DeterministicRng(-1)
    with pytest.raises(ValueError, match="seed"):
        DeterministicRng(1 << 64)
    with pytest.raises(ValueError, match="low must not exceed high"):
        rng.randint(3, 2)
    with pytest.raises(ValueError, match="low must not exceed high"):
        rng.uniform(2.0, 1.0)
    with pytest.raises(ValueError, match="probability"):
        rng.bernoulli(1.5)
    with pytest.raises(ValueError, match="rate must be positive"):
        rng.exponential(0.0)
    with pytest.raises(ValueError, match="stddev"):
        rng.normal(0.0, -1.0)
    with pytest.raises(ValueError, match="empty sequence"):
        rng.choice([])
