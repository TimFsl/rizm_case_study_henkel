"""Real-euro investment metrics for a fixed screening capacity.

CAPEX is paid at t = 0. Operating cashflows are paid at the end of each
operating year, so the first year is discounted by one year. There is no tax,
depreciation, subsidy, financing structure, or salvage value.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping


class InvestmentError(ValueError):
    """Raised when a cashflow or carbon path cannot be evaluated."""


def interpolate_anchor_series(
    anchors: Mapping[int, float],
    years: Iterable[int],
) -> dict[int, float]:
    """Linear values between anchor years.

    Before the first anchor and after the last anchor, the nearest anchor is
    held. That avoids inventing a slope outside the stated scenario.
    """
    points = sorted((int(year), float(price)) for year, price in anchors.items())
    if not points:
        raise InvestmentError("An anchor series needs at least one anchor year")
    out: dict[int, float] = {}
    for year in years:
        y = int(year)
        if y <= points[0][0]:
            out[y] = points[0][1]
            continue
        if y >= points[-1][0]:
            out[y] = points[-1][1]
            continue
        for (y0, p0), (y1, p1) in zip(points, points[1:]):
            if y0 <= y <= y1:
                if y1 == y0:
                    out[y] = p0
                else:
                    weight = (y - y0) / (y1 - y0)
                    out[y] = p0 + weight * (p1 - p0)
                break
        else:
            raise InvestmentError(f"No anchor segment covers {y}")
    return out


def carbon_prices_for_years(
    anchors: Mapping[int, float],
    years: Iterable[int],
) -> dict[int, float]:
    """Linear carbon prices between anchor years."""
    return interpolate_anchor_series(anchors, years)


def capital_recovery_factor(discount_rate: float, lifetime_years: int) -> float:
    """CRF = r(1+r)^N / ((1+r)^N - 1). At a zero rate this is 1/N."""
    years = int(lifetime_years)
    if years <= 0:
        raise InvestmentError("Lifetime must be a positive number of years")
    rate = float(discount_rate)
    if rate < 0.0:
        raise InvestmentError("Discount rate cannot be negative")
    if rate == 0.0:
        return 1.0 / years
    growth = (1.0 + rate) ** years
    return rate * growth / (growth - 1.0)


def net_present_value(discount_rate: float, cashflows: Iterable[float]) -> float:
    """NPV of a cashflow vector whose first entry is t = 0."""
    rate = float(discount_rate)
    if rate <= -1.0:
        raise InvestmentError("Discount rate must be greater than -100%")
    total = 0.0
    for year, cashflow in enumerate(cashflows):
        total += float(cashflow) / (1.0 + rate) ** year
    return total


def equivalent_annual_value(
    npv: float,
    discount_rate: float,
    lifetime_years: int,
) -> float:
    """Spread NPV over the operating life. CAPEX is already inside NPV."""
    return float(npv) * capital_recovery_factor(discount_rate, lifetime_years)


def internal_rate_of_return(
    cashflows: Iterable[float],
    *,
    low: float = -0.9,
    high: float = 5.0,
) -> float | None:
    """IRR of a cashflow vector, or None when no sign-changing root is found.

    The search is a bisection. A zero project, or a project whose NPV does not
    change sign between the bounds, is reported as undefined rather than as a
    fabricated rate.
    """
    flows = [float(value) for value in cashflows]
    if len(flows) < 2:
        return None
    if all(abs(value) <= 1e-9 for value in flows):
        return None

    def present(rate: float) -> float:
        return net_present_value(rate, flows)

    lo = float(low)
    hi = float(high)
    npv_lo = present(lo)
    npv_hi = present(hi)
    if abs(npv_lo) <= 1e-6:
        return lo
    if abs(npv_hi) <= 1e-6:
        return hi
    if npv_lo * npv_hi > 0.0:
        return None
    for _ in range(80):
        mid = 0.5 * (lo + hi)
        npv_mid = present(mid)
        if abs(npv_mid) <= 1e-6 or abs(hi - lo) <= 1e-10:
            return mid
        if npv_lo * npv_mid <= 0.0:
            hi = mid
            npv_hi = npv_mid
        else:
            lo = mid
            npv_lo = npv_mid
    return 0.5 * (lo + hi)


def payback_years(cashflows: Iterable[float], discount_rate: float = 0.0) -> float | None:
    """First time cumulative cashflow reaches zero, with fractional years.

    `discount_rate` of zero is the undiscounted payback. A positive rate
    discounts each operating cashflow before it is accumulated. The first
    cashflow is undiscounted t = 0. None means payback is not reached.
    """
    flows = [float(value) for value in cashflows]
    if not flows:
        return None
    rate = float(discount_rate)
    cumulative = 0.0
    for year, cashflow in enumerate(flows):
        step = cashflow if year == 0 else cashflow / (1.0 + rate) ** year
        previous = cumulative
        cumulative += step
        if cumulative >= -1e-8:
            if year == 0:
                return 0.0
            if abs(step) <= 1e-12:
                return float(year)
            fraction = (0.0 - previous) / step
            fraction = min(1.0, max(0.0, fraction))
            return (year - 1) + fraction
    return None


def payback_label(years: float | None, *, capex_eur: float = 1.0) -> str | float:
    """CSV value for a payback. A zero-CAPEX case is not a payback result."""
    if abs(float(capex_eur)) <= 1e-9:
        return "not applicable"
    if years is None:
        return "not reached"
    return float(years)


def select_capacity_by_npv(npv_by_capacity: Mapping[float, float]) -> float:
    """Capacity with the highest NPV. An exact tie keeps the smaller capacity."""
    if not npv_by_capacity:
        raise InvestmentError("NPV selection needs at least one capacity")
    ranked = sorted(
        ((float(capacity), float(npv)) for capacity, npv in npv_by_capacity.items()),
        key=lambda item: (-item[1], item[0]),
    )
    return ranked[0][0]
