from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from models.client import Client, CareNeedsProfile
from models.base import LTCPolicyBase, InflationProtection
from models.annuity_ltc import AnnuityLTC
from models.traditional_ltc import TraditionalLTC

from .care_engine import CareEngine, MonthlyCareCost


@dataclass
class MonthlyProjectionRow:
    month: int
    phase_name: str
    care_cost: float
    benefit_paid: float
    client_out_of_pocket: float
    remaining_pool: float | None
    notes: str = ""


@dataclass
class PolicyProjectionResult:
    policy_number: str
    policy_type: str
    product_name: str
    total_care_cost: float
    total_benefit_paid: float
    total_client_out_of_pocket: float
    deductible_cost: float
    coinsurance_cost: float
    coverage_gap_cost: float
    benefit_exhaustion_month: int | None
    coverage_ratio: float
    monthly_detail: list[MonthlyProjectionRow] = field(default_factory=list)

    @property
    def shortfall(self) -> float:
        return max(0.0, self.total_care_cost - self.total_benefit_paid)

    @property
    def is_fully_covered(self) -> bool:
        return self.shortfall <= 0.01


@dataclass
class ComparisonResult:
    projections: list[PolicyProjectionResult]
    care_cost_total: float
    best_coverage_policy: str
    lowest_out_of_pocket_policy: str

    def summary_table(self) -> list[dict[str, Any]]:
        return [
            {
                "policy": p.product_name,
                "policy_number": p.policy_number,
                "total_care_cost": p.total_care_cost,
                "total_benefit_paid": p.total_benefit_paid,
                "total_out_of_pocket": p.total_client_out_of_pocket,
                "coverage_ratio": f"{p.coverage_ratio:.1%}",
                "exhaustion_month": p.benefit_exhaustion_month,
                "deductible": p.deductible_cost,
                "coinsurance": p.coinsurance_cost,
                "gap": p.coverage_gap_cost,
            }
            for p in self.projections
        ]


class MyCareEngine:
    def __init__(self, care_engine: CareEngine | None = None) -> None:
        self.care_engine = care_engine or CareEngine()

    def project_forecare(
        self,
        policy: AnnuityLTC,
        care_needs: CareNeedsProfile,
        client_age: int,
    ) -> PolicyProjectionResult:
        monthly_costs = self.care_engine.project_trajectory_costs(care_needs, client_age)
        years_until_care = max(0, care_needs.predicted_care_start_age - client_age)
        policy_year_at_care_start = years_until_care + 1

        total_ltc_pool = policy.total_ltc_benefit(
            policy_year_at_care_start, guaranteed=True
        )
        remaining_pool = total_ltc_pool
        max_monthly = policy.max_monthly_benefit

        total_care_cost = 0.0
        total_benefit_paid = 0.0
        total_oop = 0.0
        exhaustion_month: int | None = None
        detail: list[MonthlyProjectionRow] = []

        for mc in monthly_costs:
            care_cost = mc.inflated_monthly_cost
            total_care_cost += care_cost

            if remaining_pool <= 0:
                benefit = 0.0
                notes = "benefit exhausted"
            else:
                benefit = min(care_cost, max_monthly, remaining_pool)
                remaining_pool -= benefit
                if remaining_pool <= 0 and exhaustion_month is None:
                    exhaustion_month = mc.month

                notes = ""
                if care_cost > max_monthly:
                    notes = f"capped at ${max_monthly:,.0f}/mo"

            oop = care_cost - benefit
            total_benefit_paid += benefit
            total_oop += oop

            detail.append(MonthlyProjectionRow(
                month=mc.month,
                phase_name=mc.phase_name,
                care_cost=round(care_cost, 2),
                benefit_paid=round(benefit, 2),
                client_out_of_pocket=round(oop, 2),
                remaining_pool=round(remaining_pool, 2),
                notes=notes,
            ))

        coverage_ratio = total_benefit_paid / total_care_cost if total_care_cost > 0 else 0.0

        return PolicyProjectionResult(
            policy_number=policy.policy_number,
            policy_type="AnnuityLTC",
            product_name="ForeCare Fixed Annuity",
            total_care_cost=round(total_care_cost, 2),
            total_benefit_paid=round(total_benefit_paid, 2),
            total_client_out_of_pocket=round(total_oop, 2),
            deductible_cost=0.0,
            coinsurance_cost=0.0,
            coverage_gap_cost=round(total_oop, 2),
            benefit_exhaustion_month=exhaustion_month,
            coverage_ratio=round(coverage_ratio, 4),
            monthly_detail=detail,
        )

    def project_nyl(
        self,
        policy: TraditionalLTC,
        care_needs: CareNeedsProfile,
        client_age: int,
    ) -> PolicyProjectionResult:
        monthly_costs = self.care_engine.project_trajectory_costs(care_needs, client_age)
        years_until_care = max(0, care_needs.predicted_care_start_age - client_age)
        policy_year_at_care_start = years_until_care + 1

        benefits_at_start = policy.benefit_at_year(policy_year_at_care_start)
        adjusted_deductible = benefits_at_start["dollar_deductible"]
        adjusted_lifetime_max = benefits_at_start["policy_lifetime_maximum"]

        remaining_deductible = adjusted_deductible
        remaining_lifetime = adjusted_lifetime_max

        total_care_cost = 0.0
        total_benefit_paid = 0.0
        total_oop = 0.0
        total_deductible_cost = 0.0
        total_coinsurance_cost = 0.0
        total_gap_cost = 0.0
        exhaustion_month: int | None = None
        detail: list[MonthlyProjectionRow] = []

        for mc in monthly_costs:
            care_cost = mc.inflated_monthly_cost
            total_care_cost += care_cost
            care_year = policy_year_at_care_start + (mc.month - 1) // 12
            year_benefits = policy.benefit_at_year(care_year)
            monthly_max = year_benefits["max_monthly_benefit"]

            if remaining_deductible > 0:
                deductible_applied = min(care_cost, remaining_deductible)
                remaining_deductible -= deductible_applied
                total_deductible_cost += deductible_applied

                benefit = 0.0
                oop = care_cost
                notes = f"deductible: ${remaining_deductible:,.0f} remaining"
            elif remaining_lifetime <= 0:
                benefit = 0.0
                oop = care_cost
                total_gap_cost += care_cost
                notes = "lifetime max exhausted"
                if exhaustion_month is None:
                    exhaustion_month = mc.month
            else:
                coinsurance = care_cost * policy.coinsurance_pct
                eligible = care_cost - coinsurance
                benefit = min(eligible, monthly_max, remaining_lifetime)
                remaining_lifetime -= benefit
                total_coinsurance_cost += coinsurance
                oop = care_cost - benefit

                if remaining_lifetime <= 0 and exhaustion_month is None:
                    exhaustion_month = mc.month

                notes = ""
                if eligible > monthly_max:
                    excess = eligible - monthly_max
                    total_gap_cost += excess
                    notes = f"over monthly cap by ${excess:,.0f}"

            total_benefit_paid += benefit
            total_oop += oop

            detail.append(MonthlyProjectionRow(
                month=mc.month,
                phase_name=mc.phase_name,
                care_cost=round(care_cost, 2),
                benefit_paid=round(benefit, 2),
                client_out_of_pocket=round(oop, 2),
                remaining_pool=round(remaining_lifetime, 2),
                notes=notes,
            ))

        coverage_ratio = total_benefit_paid / total_care_cost if total_care_cost > 0 else 0.0

        return PolicyProjectionResult(
            policy_number=policy.policy_number,
            policy_type="TraditionalLTC",
            product_name="NYL MyCare Gold",
            total_care_cost=round(total_care_cost, 2),
            total_benefit_paid=round(total_benefit_paid, 2),
            total_client_out_of_pocket=round(total_oop, 2),
            deductible_cost=round(total_deductible_cost, 2),
            coinsurance_cost=round(total_coinsurance_cost, 2),
            coverage_gap_cost=round(total_gap_cost, 2),
            benefit_exhaustion_month=exhaustion_month,
            coverage_ratio=round(coverage_ratio, 4),
            monthly_detail=detail,
        )

    def project_policy(
        self,
        policy: LTCPolicyBase,
        care_needs: CareNeedsProfile,
        client_age: int,
    ) -> PolicyProjectionResult:
        if isinstance(policy, AnnuityLTC):
            return self.project_forecare(policy, care_needs, client_age)
        if isinstance(policy, TraditionalLTC):
            return self.project_nyl(policy, care_needs, client_age)
        raise TypeError(f"Unsupported policy type: {type(policy).__name__}")

    def compare_policies(
        self,
        policies: list[LTCPolicyBase],
        care_needs: CareNeedsProfile,
        client_age: int,
    ) -> ComparisonResult:
        projections = [
            self.project_policy(p, care_needs, client_age) for p in policies
        ]

        care_total = projections[0].total_care_cost if projections else 0.0

        best_coverage = max(projections, key=lambda p: p.coverage_ratio)
        lowest_oop = min(projections, key=lambda p: p.total_client_out_of_pocket)

        return ComparisonResult(
            projections=projections,
            care_cost_total=care_total,
            best_coverage_policy=best_coverage.product_name,
            lowest_out_of_pocket_policy=lowest_oop.product_name,
        )

    def sensitivity_analysis(
        self,
        policy: LTCPolicyBase,
        care_needs: CareNeedsProfile,
        client_age: int,
        inflation_rates: list[float] | None = None,
    ) -> list[dict[str, Any]]:
        if inflation_rates is None:
            inflation_rates = [0.03, 0.04, 0.054, 0.06, 0.07]

        results: list[dict[str, Any]] = []
        original_rate = self.care_engine.annual_inflation_rate

        for rate in inflation_rates:
            self.care_engine.annual_inflation_rate = rate
            projection = self.project_policy(policy, care_needs, client_age)
            results.append({
                "inflation_rate": f"{rate:.1%}",
                "total_care_cost": projection.total_care_cost,
                "total_benefit_paid": projection.total_benefit_paid,
                "out_of_pocket": projection.total_client_out_of_pocket,
                "coverage_ratio": f"{projection.coverage_ratio:.1%}",
                "exhaustion_month": projection.benefit_exhaustion_month,
            })

        self.care_engine.annual_inflation_rate = original_rate
        return results
