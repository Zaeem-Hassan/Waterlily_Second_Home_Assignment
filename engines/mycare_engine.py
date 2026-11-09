from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from models.traditional_ltc import TraditionalLTC
from models.client import CareNeedsProfile, CareTrajectoryPhase


class NYLBenefitPhase(Enum):
    PRE_CARE = "pre_care"
    DEDUCTIBLE = "deductible"
    COVERED = "covered"
    EXHAUSTED = "exhausted"


DIVIDEND_ANCHOR_POINTS: list[tuple[int, float]] = [
    (1, 0.0),
    (2, 0.0),
    (3, 0.0),
    (4, 4.0),
    (5, 5.0),
    (6, 6.0),
    (7, 9.0),
    (8, 12.0),
    (9, 17.0),
    (10, 22.0),
    (11, 33.0),
    (12, 46.0),
    (13, 58.0),
    (14, 75.0),
    (15, 96.0),
    (16, 129.0),
    (17, 166.0),
    (18, 217.0),
    (19, 285.0),
    (20, 376.0),
    (21, 422.0),
    (22, 563.0),
    (23, 725.0),
    (24, 940.0),
    (25, 1224.0),
    (26, 1510.0),
    (27, 1988.0),
    (28, 2588.0),
    (29, 3369.0),
    (30, 4405.0),
    (31, 5919.0),
    (32, 7195.0),
    (33, 8497.0),
    (34, 10018.0),
    (35, 11715.0),
    (36, 13580.0),
    (37, 15247.0),
    (38, 17153.0),
    (39, 19332.0),
    (40, 21823.0),
    (41, 24672.0),
]


def interpolate_dividend(year: int, anchors: list[tuple[int, float]]) -> float:
    if year <= anchors[0][0]:
        return anchors[0][1]
    if year >= anchors[-1][0]:
        return anchors[-1][1]
    for i in range(len(anchors) - 1):
        y0, d0 = anchors[i]
        y1, d1 = anchors[i + 1]
        if y0 <= year <= y1:
            frac = (year - y0) / (y1 - y0)
            return d0 + frac * (d1 - d0)
    return 0.0


@dataclass
class NYLMonthlySummary:
    month: int
    policy_year: int
    age: float
    phase: NYLBenefitPhase
    gross_monthly_premium: float
    monthly_dividend: float
    net_monthly_premium: float
    premium_waived: bool
    cumulative_gross_premiums: float
    cumulative_dividends: float
    cumulative_net_premiums: float
    inflation_adjusted_monthly_max: float
    inflation_adjusted_lifetime_max: float
    inflation_adjusted_deductible: float
    care_cost: float
    deductible_applied: float
    coinsurance_amount: float
    benefit_paid: float
    client_out_of_pocket: float
    remaining_deductible: float
    remaining_lifetime_benefit: float
    cumulative_benefit_paid: float
    cumulative_care_cost: float
    cumulative_out_of_pocket: float


@dataclass
class NYLSimulationResult:
    scenario_name: str
    with_dividends: bool
    with_inflation: bool
    monthly_detail: list[NYLMonthlySummary]
    total_care_cost: float
    total_benefit_paid: float
    total_out_of_pocket: float
    total_deductible_cost: float
    total_coinsurance_cost: float
    total_gross_premiums: float
    total_dividends: float
    total_net_premiums: float
    premiums_waived_months: int
    benefit_exhaustion_month: int | None
    coverage_ratio: float
    roi: float
    pre_care_months: int
    care_months: int
    inflation_adjusted_monthly_max_at_care: float
    inflation_adjusted_lifetime_max_at_care: float
    inflation_adjusted_deductible_at_care: float

    def summary(self) -> dict[str, Any]:
        return {
            "scenario": self.scenario_name,
            "with_dividends": self.with_dividends,
            "with_inflation": self.with_inflation,
            "total_care_cost": self.total_care_cost,
            "total_benefit_paid": self.total_benefit_paid,
            "total_out_of_pocket": self.total_out_of_pocket,
            "total_deductible_cost": self.total_deductible_cost,
            "total_coinsurance_cost": self.total_coinsurance_cost,
            "total_gross_premiums": self.total_gross_premiums,
            "total_dividends": self.total_dividends,
            "total_net_premiums": self.total_net_premiums,
            "premiums_waived_months": self.premiums_waived_months,
            "benefit_exhaustion_month": self.benefit_exhaustion_month,
            "coverage_ratio": f"{self.coverage_ratio:.1%}",
            "roi": f"{self.roi:.1%}",
            "pre_care_months": self.pre_care_months,
            "care_months": self.care_months,
            "adj_monthly_max_at_care": self.inflation_adjusted_monthly_max_at_care,
            "adj_lifetime_max_at_care": self.inflation_adjusted_lifetime_max_at_care,
            "adj_deductible_at_care": self.inflation_adjusted_deductible_at_care,
        }


class MyCareEngine:
    def __init__(
        self,
        policy: TraditionalLTC,
        care_needs: CareNeedsProfile,
        issue_age: int = 55,
        dividend_anchors: list[tuple[int, float]] | None = None,
    ) -> None:
        self.policy = policy
        self.care_needs = care_needs
        self.issue_age = issue_age
        self.care_inflation = care_needs.annual_cost_inflation
        self.dividend_anchors = dividend_anchors or DIVIDEND_ANCHOR_POINTS

    def _inflate_care_cost(self, base_monthly: float, years_forward: float) -> float:
        return base_monthly * ((1 + self.care_inflation) ** years_forward)

    def _benefit_growth(self, policy_year: int, with_inflation: bool) -> float:
        if not with_inflation:
            return 1.0
        return (1 + self.policy.inflation_rate) ** (policy_year - 1)

    def _gross_annual_premium(self, policy_year: int, with_inflation: bool) -> float:
        return self.policy.annual_premium

    def _annual_dividend(self, policy_year: int, with_dividends: bool) -> float:
        if not with_dividends:
            return 0.0
        return interpolate_dividend(policy_year, self.dividend_anchors)

    def _build_care_schedule(self) -> list[tuple[int, float, str]]:
        years_until_care = max(
            0, self.care_needs.predicted_care_start_age - self.issue_age
        )
        schedule: list[tuple[int, float, str]] = []
        care_month_offset = 0

        for phase in self.care_needs.trajectory:
            for m in range(phase.duration_months):
                abs_month = (years_until_care * 12) + care_month_offset + m
                years_from_issue = abs_month / 12
                inflated = self._inflate_care_cost(phase.monthly_cost, years_from_issue)
                schedule.append((abs_month + 1, round(inflated, 2), phase.name))
            care_month_offset += phase.duration_months

        return schedule

    def simulate(
        self,
        with_dividends: bool = True,
        with_inflation: bool = True,
    ) -> NYLSimulationResult:
        care_schedule_raw = self._build_care_schedule()
        care_by_month: dict[int, tuple[float, str]] = {
            m: (c, name) for m, c, name in care_schedule_raw
        }

        pre_care_months = max(
            0, self.care_needs.predicted_care_start_age - self.issue_age
        ) * 12
        total_months = pre_care_months + self.care_needs.total_trajectory_months

        initial_deductible = self.policy.dollar_deductible
        initial_lifetime_max = self.policy.lifetime_max
        initial_monthly_max = self.policy.max_monthly_benefit
        coinsurance_pct = self.policy.coinsurance_pct

        cumulative_gross = 0.0
        cumulative_div = 0.0
        cumulative_net = 0.0
        cumulative_benefit = 0.0
        cumulative_care = 0.0
        cumulative_oop = 0.0
        total_deductible_cost = 0.0
        total_coinsurance_cost = 0.0
        waived_months = 0
        exhaustion_month: int | None = None

        care_start_year: int | None = None
        remaining_deductible = 0.0
        remaining_lifetime = 0.0
        adj_monthly_max_at_care = 0.0
        adj_lifetime_at_care = 0.0
        adj_deductible_at_care = 0.0

        detail: list[NYLMonthlySummary] = []

        for month in range(1, total_months + 1):
            policy_year = ((month - 1) // 12) + 1
            age = self.issue_age + (month / 12)
            care_entry = care_by_month.get(month)
            care_cost = care_entry[0] if care_entry else 0.0
            phase_name = care_entry[1] if care_entry else ""
            is_on_claim = care_cost > 0

            if is_on_claim and care_start_year is None:
                care_start_year = policy_year
                growth = self._benefit_growth(policy_year, with_inflation)
                remaining_deductible = initial_deductible * growth
                remaining_lifetime = initial_lifetime_max * growth
                adj_monthly_max_at_care = initial_monthly_max * growth
                adj_lifetime_at_care = remaining_lifetime
                adj_deductible_at_care = remaining_deductible

            growth = self._benefit_growth(policy_year, with_inflation)
            adj_monthly_max = initial_monthly_max * growth
            adj_lifetime_max = initial_lifetime_max * growth
            adj_deductible = initial_deductible * growth

            gross_annual = self._gross_annual_premium(policy_year, with_inflation)
            annual_dividend = self._annual_dividend(policy_year, with_dividends)
            net_annual = max(0.0, gross_annual - annual_dividend)

            gross_monthly = gross_annual / 12
            div_monthly = annual_dividend / 12
            net_monthly = net_annual / 12

            premium_waived = is_on_claim and self.policy.waiver_of_premium
            if premium_waived:
                actual_premium = 0.0
                waived_months += 1
            else:
                actual_premium = net_monthly

            cumulative_gross += gross_monthly if not premium_waived else 0.0
            cumulative_div += div_monthly if not premium_waived else 0.0
            cumulative_net += actual_premium

            benefit_paid = 0.0
            deductible_applied = 0.0
            coinsurance_amount = 0.0
            client_oop = 0.0

            if not is_on_claim:
                phase = NYLBenefitPhase.PRE_CARE
            elif remaining_lifetime <= 0 and care_start_year is not None:
                phase = NYLBenefitPhase.EXHAUSTED
                client_oop = care_cost
                if exhaustion_month is None:
                    exhaustion_month = month
            elif remaining_deductible > 0:
                phase = NYLBenefitPhase.DEDUCTIBLE
                deductible_applied = min(care_cost, remaining_deductible)
                remaining_deductible -= deductible_applied
                total_deductible_cost += deductible_applied
                client_oop = care_cost
            else:
                phase = NYLBenefitPhase.COVERED
                coinsurance_amount = care_cost * coinsurance_pct
                eligible = care_cost - coinsurance_amount
                benefit_paid = min(eligible, adj_monthly_max, remaining_lifetime)
                remaining_lifetime -= benefit_paid
                total_coinsurance_cost += coinsurance_amount
                client_oop = care_cost - benefit_paid

                if remaining_lifetime <= 0 and exhaustion_month is None:
                    exhaustion_month = month

            cumulative_benefit += benefit_paid
            cumulative_care += care_cost
            cumulative_oop += client_oop

            detail.append(NYLMonthlySummary(
                month=month,
                policy_year=policy_year,
                age=round(age, 2),
                phase=phase,
                gross_monthly_premium=round(gross_monthly if not premium_waived else 0.0, 2),
                monthly_dividend=round(div_monthly if not premium_waived else 0.0, 2),
                net_monthly_premium=round(actual_premium, 2),
                premium_waived=premium_waived,
                cumulative_gross_premiums=round(cumulative_gross, 2),
                cumulative_dividends=round(cumulative_div, 2),
                cumulative_net_premiums=round(cumulative_net, 2),
                inflation_adjusted_monthly_max=round(adj_monthly_max, 2),
                inflation_adjusted_lifetime_max=round(adj_lifetime_max, 2),
                inflation_adjusted_deductible=round(adj_deductible, 2),
                care_cost=round(care_cost, 2),
                deductible_applied=round(deductible_applied, 2),
                coinsurance_amount=round(coinsurance_amount, 2),
                benefit_paid=round(benefit_paid, 2),
                client_out_of_pocket=round(client_oop, 2),
                remaining_deductible=round(max(remaining_deductible, 0.0), 2),
                remaining_lifetime_benefit=round(max(remaining_lifetime, 0.0), 2),
                cumulative_benefit_paid=round(cumulative_benefit, 2),
                cumulative_care_cost=round(cumulative_care, 2),
                cumulative_out_of_pocket=round(cumulative_oop, 2),
            ))

        coverage_ratio = (
            cumulative_benefit / cumulative_care if cumulative_care > 0 else 0.0
        )
        roi = (
            (cumulative_benefit - cumulative_net) / cumulative_net
            if cumulative_net > 0 else 0.0
        )

        labels: list[str] = []
        if with_dividends:
            labels.append("With Dividends")
        else:
            labels.append("Without Dividends")
        if with_inflation:
            labels.append("With CPI-U 3% Inflation")
        else:
            labels.append("Without Inflation Protection")
        scenario_name = " + ".join(labels)

        return NYLSimulationResult(
            scenario_name=scenario_name,
            with_dividends=with_dividends,
            with_inflation=with_inflation,
            monthly_detail=detail,
            total_care_cost=round(cumulative_care, 2),
            total_benefit_paid=round(cumulative_benefit, 2),
            total_out_of_pocket=round(cumulative_oop, 2),
            total_deductible_cost=round(total_deductible_cost, 2),
            total_coinsurance_cost=round(total_coinsurance_cost, 2),
            total_gross_premiums=round(cumulative_gross, 2),
            total_dividends=round(cumulative_div, 2),
            total_net_premiums=round(cumulative_net, 2),
            premiums_waived_months=waived_months,
            benefit_exhaustion_month=exhaustion_month,
            coverage_ratio=round(coverage_ratio, 4),
            roi=round(roi, 4),
            pre_care_months=pre_care_months,
            care_months=self.care_needs.total_trajectory_months,
            inflation_adjusted_monthly_max_at_care=round(adj_monthly_max_at_care, 2),
            inflation_adjusted_lifetime_max_at_care=round(adj_lifetime_at_care, 2),
            inflation_adjusted_deductible_at_care=round(adj_deductible_at_care, 2),
        )

    def run_all_scenarios(self) -> dict[str, NYLSimulationResult]:
        return {
            "with_div_with_infl": self.simulate(
                with_dividends=True, with_inflation=True
            ),
            "with_div_no_infl": self.simulate(
                with_dividends=True, with_inflation=False
            ),
            "no_div_with_infl": self.simulate(
                with_dividends=False, with_inflation=True
            ),
            "no_div_no_infl": self.simulate(
                with_dividends=False, with_inflation=False
            ),
        }

    def print_summary(self, result: NYLSimulationResult) -> str:
        s = result.summary()
        lines = [
            f"=== NYL MyCare Simulation: {s['scenario']} ===",
            f"  Pre-care months:             {s['pre_care_months']}",
            f"  Care months:                 {s['care_months']}",
            f"  Adj monthly max at care:     ${s['adj_monthly_max_at_care']:>12,.2f}",
            f"  Adj lifetime max at care:    ${s['adj_lifetime_max_at_care']:>12,.2f}",
            f"  Adj deductible at care:      ${s['adj_deductible_at_care']:>12,.2f}",
            f"  ---",
            f"  Total care cost:             ${s['total_care_cost']:>12,.2f}",
            f"  Total benefit paid:          ${s['total_benefit_paid']:>12,.2f}",
            f"  Total out-of-pocket:         ${s['total_out_of_pocket']:>12,.2f}",
            f"    Deductible portion:         ${s['total_deductible_cost']:>12,.2f}",
            f"    Coinsurance portion:        ${s['total_coinsurance_cost']:>12,.2f}",
            f"  ---",
            f"  Total gross premiums:        ${s['total_gross_premiums']:>12,.2f}",
            f"  Total dividends:             ${s['total_dividends']:>12,.2f}",
            f"  Total net premiums:          ${s['total_net_premiums']:>12,.2f}",
            f"  Premiums waived (months):    {s['premiums_waived_months']}",
            f"  ---",
            f"  Benefit exhaustion:          month {s['benefit_exhaustion_month'] or 'N/A'}",
            f"  Coverage ratio:              {s['coverage_ratio']}",
            f"  ROI:                         {s['roi']}",
        ]
        return "\n".join(lines)
