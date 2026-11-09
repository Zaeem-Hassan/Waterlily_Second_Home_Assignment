from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from models.annuity_ltc import AnnuityLTC, InterestRateSchedule
from models.client import CareNeedsProfile, CareTrajectoryPhase


class BenefitPhase(Enum):
    PRE_CARE = "pre_care"
    ACCELERATED = "accelerated"
    EXTENDED = "extended"
    EXHAUSTED = "exhausted"


@dataclass
class ForeCareMonthlySummary:
    month: int
    policy_year: int
    age: float
    phase: BenefitPhase
    interest_rate: float
    interest_credit: float
    rider_charge: float
    contract_value: float
    care_cost: float
    benefit_paid: float
    client_out_of_pocket: float
    accelerated_benefit_remaining: float
    extended_benefit_remaining: float
    total_ltc_remaining: float
    cumulative_benefit_paid: float
    cumulative_rider_charges: float
    cumulative_care_cost: float
    cumulative_out_of_pocket: float


@dataclass
class ForeCareSimulationResult:
    scenario_name: str
    guaranteed: bool
    monthly_detail: list[ForeCareMonthlySummary]
    total_care_cost: float
    total_benefit_paid: float
    total_out_of_pocket: float
    total_rider_charges: float
    total_interest_earned: float
    final_contract_value: float
    final_death_benefit: float
    max_extended_benefit: float
    benefit_exhaustion_month: int | None
    coverage_ratio: float
    roi: float
    pre_care_months: int
    care_months: int

    def summary(self) -> dict[str, Any]:
        return {
            "scenario": self.scenario_name,
            "guaranteed": self.guaranteed,
            "single_premium": self.total_rider_charges + self.final_contract_value + self.total_benefit_paid - self.total_interest_earned,
            "total_care_cost": self.total_care_cost,
            "total_benefit_paid": self.total_benefit_paid,
            "total_out_of_pocket": self.total_out_of_pocket,
            "total_rider_charges": self.total_rider_charges,
            "total_interest_earned": self.total_interest_earned,
            "final_contract_value": self.final_contract_value,
            "final_death_benefit": self.final_death_benefit,
            "benefit_exhaustion_month": self.benefit_exhaustion_month,
            "coverage_ratio": f"{self.coverage_ratio:.1%}",
            "roi": f"{self.roi:.1%}",
            "pre_care_months": self.pre_care_months,
            "care_months": self.care_months,
        }


class ForeCareEngine:
    def __init__(
        self,
        policy: AnnuityLTC,
        care_needs: CareNeedsProfile,
        issue_age: int = 55,
    ) -> None:
        self.policy = policy
        self.care_needs = care_needs
        self.issue_age = issue_age
        self.care_inflation = care_needs.annual_cost_inflation

    def _annual_rate(self, policy_year: int, guaranteed: bool) -> float:
        if guaranteed:
            return self.policy.guaranteed_rates.rate_for_year(policy_year)
        if policy_year == 1:
            return self.policy.guaranteed_rates.year_one_rate
        return self.policy.current_crediting_rate

    def _monthly_interest_factor(self, policy_year: int, guaranteed: bool) -> float:
        annual = self._annual_rate(policy_year, guaranteed)
        daily_rate = annual / 365
        return (1 + daily_rate) ** 30 - 1

    def _inflate_care_cost(self, base_monthly: float, years_forward: float) -> float:
        return base_monthly * ((1 + self.care_inflation) ** years_forward)

    def _build_care_schedule(self) -> list[tuple[int, float]]:
        years_until_care = max(
            0, self.care_needs.predicted_care_start_age - self.issue_age
        )
        schedule: list[tuple[int, float]] = []
        care_month_offset = 0

        for phase in self.care_needs.trajectory:
            for m in range(phase.duration_months):
                months_from_issue = (years_until_care * 12) + care_month_offset + m
                years_from_issue = months_from_issue / 12
                inflated = self._inflate_care_cost(phase.monthly_cost, years_from_issue)
                schedule.append((months_from_issue + 1, round(inflated, 2)))
            care_month_offset += phase.duration_months

        return schedule

    def simulate(self, guaranteed: bool = True) -> ForeCareSimulationResult:
        care_schedule_raw = self._build_care_schedule()
        care_by_month: dict[int, float] = {m: c for m, c in care_schedule_raw}

        pre_care_months = max(
            0, self.care_needs.predicted_care_start_age - self.issue_age
        ) * 12

        total_months = pre_care_months + self.care_needs.total_trajectory_months
        max_extended = self.policy.single_premium * (self.policy.ltc_multiplier - 1)
        rider_charge_rate = self.policy.rider_charge_per_dollar_per_month
        max_monthly_benefit = self.policy.max_monthly_benefit

        contract_value = self.policy.single_premium
        extended_remaining = max_extended
        cumulative_rider = 0.0
        cumulative_interest = 0.0
        cumulative_benefit = 0.0
        cumulative_care_cost = 0.0
        cumulative_oop = 0.0
        exhaustion_month: int | None = None

        detail: list[ForeCareMonthlySummary] = []

        for month in range(1, total_months + 1):
            policy_year = ((month - 1) // 12) + 1
            age = self.issue_age + (month / 12)
            care_cost = care_by_month.get(month, 0.0)
            is_on_claim = care_cost > 0

            monthly_interest = self._monthly_interest_factor(policy_year, guaranteed)
            annual_rate = self._annual_rate(policy_year, guaranteed)

            if is_on_claim and contract_value <= 0:
                phase = BenefitPhase.EXTENDED
            elif is_on_claim:
                phase = BenefitPhase.ACCELERATED
            elif exhaustion_month is not None:
                phase = BenefitPhase.EXHAUSTED
            else:
                phase = BenefitPhase.PRE_CARE

            prior_cv = contract_value
            interest_credit = contract_value * monthly_interest if contract_value > 0 else 0.0

            if phase == BenefitPhase.ACCELERATED:
                rider_charge = 0.0
            else:
                raw_rider = contract_value * rider_charge_rate if contract_value > 0 else 0.0
                rider_charge = min(raw_rider, interest_credit)

            contract_value += interest_credit - rider_charge
            contract_value = max(contract_value, prior_cv if not is_on_claim else 0.0)

            benefit_paid = 0.0
            client_oop = 0.0

            if phase == BenefitPhase.ACCELERATED:
                benefit_paid = min(care_cost, max_monthly_benefit, contract_value)
                contract_value -= benefit_paid
                contract_value = max(contract_value, 0.0)
                client_oop = care_cost - benefit_paid

                if contract_value <= 0 and care_cost > benefit_paid:
                    shortfall = min(
                        care_cost - benefit_paid, max_monthly_benefit - benefit_paid, extended_remaining
                    )
                    if shortfall > 0:
                        benefit_paid += shortfall
                        extended_remaining -= shortfall
                        client_oop = care_cost - benefit_paid

            elif phase == BenefitPhase.EXTENDED:
                payable = min(care_cost, max_monthly_benefit, extended_remaining)
                benefit_paid = payable
                extended_remaining -= payable
                client_oop = care_cost - benefit_paid

                if extended_remaining <= 0 and exhaustion_month is None:
                    exhaustion_month = month

            elif phase == BenefitPhase.EXHAUSTED:
                client_oop = care_cost

            cumulative_interest += interest_credit
            cumulative_rider += rider_charge
            cumulative_benefit += benefit_paid
            cumulative_care_cost += care_cost
            cumulative_oop += client_oop

            accel_remaining = max(contract_value, 0.0)
            total_ltc_remaining = accel_remaining + max(extended_remaining, 0.0)

            detail.append(ForeCareMonthlySummary(
                month=month,
                policy_year=policy_year,
                age=round(age, 2),
                phase=phase,
                interest_rate=round(annual_rate, 4),
                interest_credit=round(interest_credit, 2),
                rider_charge=round(rider_charge, 2),
                contract_value=round(contract_value, 2),
                care_cost=round(care_cost, 2),
                benefit_paid=round(benefit_paid, 2),
                client_out_of_pocket=round(client_oop, 2),
                accelerated_benefit_remaining=round(accel_remaining, 2),
                extended_benefit_remaining=round(max(extended_remaining, 0.0), 2),
                total_ltc_remaining=round(total_ltc_remaining, 2),
                cumulative_benefit_paid=round(cumulative_benefit, 2),
                cumulative_rider_charges=round(cumulative_rider, 2),
                cumulative_care_cost=round(cumulative_care_cost, 2),
                cumulative_out_of_pocket=round(cumulative_oop, 2),
            ))

        coverage_ratio = (
            cumulative_benefit / cumulative_care_cost if cumulative_care_cost > 0 else 0.0
        )
        roi = (
            (cumulative_benefit - self.policy.single_premium) / self.policy.single_premium
            if self.policy.single_premium > 0 else 0.0
        )

        scenario_name = "Guaranteed" if guaranteed else "Non-Guaranteed (Current Rate)"

        return ForeCareSimulationResult(
            scenario_name=scenario_name,
            guaranteed=guaranteed,
            monthly_detail=detail,
            total_care_cost=round(cumulative_care_cost, 2),
            total_benefit_paid=round(cumulative_benefit, 2),
            total_out_of_pocket=round(cumulative_oop, 2),
            total_rider_charges=round(cumulative_rider, 2),
            total_interest_earned=round(cumulative_interest, 2),
            final_contract_value=round(contract_value, 2),
            final_death_benefit=round(contract_value, 2),
            max_extended_benefit=round(max_extended, 2),
            benefit_exhaustion_month=exhaustion_month,
            coverage_ratio=round(coverage_ratio, 4),
            roi=round(roi, 4),
            pre_care_months=pre_care_months,
            care_months=self.care_needs.total_trajectory_months,
        )

    def run_both_scenarios(self) -> dict[str, ForeCareSimulationResult]:
        return {
            "guaranteed": self.simulate(guaranteed=True),
            "non_guaranteed": self.simulate(guaranteed=False),
        }

    def print_summary(self, result: ForeCareSimulationResult) -> str:
        s = result.summary()
        lines = [
            f"=== ForeCare Simulation: {s['scenario']} ===",
            f"  Pre-care months:         {s['pre_care_months']}",
            f"  Care months:             {s['care_months']}",
            f"  Total care cost:         ${s['total_care_cost']:>12,.2f}",
            f"  Total benefit paid:      ${s['total_benefit_paid']:>12,.2f}",
            f"  Total out-of-pocket:     ${s['total_out_of_pocket']:>12,.2f}",
            f"  Total rider charges:     ${s['total_rider_charges']:>12,.2f}",
            f"  Total interest earned:   ${s['total_interest_earned']:>12,.2f}",
            f"  Final contract value:    ${s['final_contract_value']:>12,.2f}",
            f"  Final death benefit:     ${s['final_death_benefit']:>12,.2f}",
            f"  Benefit exhaustion:      month {s['benefit_exhaustion_month'] or 'N/A'}",
            f"  Coverage ratio:          {s['coverage_ratio']}",
            f"  ROI:                     {s['roi']}",
        ]
        return "\n".join(lines)
