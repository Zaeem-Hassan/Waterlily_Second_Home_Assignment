from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Any

from models.client import Client, CareTrajectoryPhase, CareNeedsProfile
from models.base import LTCPolicyBase


class CareLevel(Enum):
    HOME_CARE = "home_care"
    ASSISTED_LIVING = "assisted_living"
    NURSING_HOME = "nursing_home"
    SKILLED_NURSING = "skilled_nursing"
    ADULT_DAY_CARE = "adult_day_care"
    HOSPICE = "hospice"


ENVIRONMENT_TO_CARE_LEVEL: dict[str, CareLevel] = {
    "home care": CareLevel.HOME_CARE,
    "home_care": CareLevel.HOME_CARE,
    "assisted living": CareLevel.ASSISTED_LIVING,
    "assisted_living": CareLevel.ASSISTED_LIVING,
    "nursing home": CareLevel.NURSING_HOME,
    "nursing_home": CareLevel.NURSING_HOME,
    "skilled nursing": CareLevel.SKILLED_NURSING,
    "skilled_nursing": CareLevel.SKILLED_NURSING,
    "adult day care": CareLevel.ADULT_DAY_CARE,
    "hospice": CareLevel.HOSPICE,
}


@dataclass
class MonthlyCareCost:
    month: int
    care_level: CareLevel
    phase_name: str
    base_monthly_cost: float
    inflated_monthly_cost: float


@dataclass
class ClaimRecord:
    claim_id: str
    policy_number: str
    care_level: CareLevel
    start_month: int
    end_month: int | None = None
    monthly_amount_paid: float = 0.0
    is_active: bool = True

    @property
    def duration_months(self) -> int:
        end = self.end_month if self.end_month is not None else 0
        return max(0, end - self.start_month)

    @property
    def total_paid(self) -> float:
        return self.monthly_amount_paid * self.duration_months


class CareEngine:
    def __init__(self, annual_inflation_rate: float = 0.054) -> None:
        self.annual_inflation_rate = annual_inflation_rate
        self._claims: list[ClaimRecord] = []
        self._next_claim_id = 1

    def inflate_cost(
        self, base_cost: float, years_forward: float
    ) -> float:
        return base_cost * ((1 + self.annual_inflation_rate) ** years_forward)

    def project_trajectory_costs(
        self, care_needs: CareNeedsProfile, client_current_age: int
    ) -> list[MonthlyCareCost]:
        years_until_care = max(0, care_needs.predicted_care_start_age - client_current_age)
        result: list[MonthlyCareCost] = []
        month_offset = 0

        for phase in care_needs.trajectory:
            care_level = ENVIRONMENT_TO_CARE_LEVEL.get(
                phase.care_environment.lower(), CareLevel.HOME_CARE
            )
            for m in range(phase.duration_months):
                total_months_from_now = (years_until_care * 12) + month_offset + m
                years_from_now = total_months_from_now / 12
                inflated = self.inflate_cost(phase.monthly_cost, years_from_now)

                result.append(MonthlyCareCost(
                    month=month_offset + m + 1,
                    care_level=care_level,
                    phase_name=phase.name,
                    base_monthly_cost=phase.monthly_cost,
                    inflated_monthly_cost=round(inflated, 2),
                ))
            month_offset += phase.duration_months

        return result

    def total_projected_care_cost(
        self, care_needs: CareNeedsProfile, client_current_age: int
    ) -> float:
        monthly_costs = self.project_trajectory_costs(care_needs, client_current_age)
        return round(sum(mc.inflated_monthly_cost for mc in monthly_costs), 2)

    def cost_by_phase(
        self, care_needs: CareNeedsProfile, client_current_age: int
    ) -> list[dict[str, Any]]:
        monthly_costs = self.project_trajectory_costs(care_needs, client_current_age)
        phases: dict[str, list[MonthlyCareCost]] = {}
        for mc in monthly_costs:
            phases.setdefault(mc.phase_name, []).append(mc)

        result: list[dict[str, Any]] = []
        for phase_name, months in phases.items():
            result.append({
                "phase": phase_name,
                "duration_months": len(months),
                "base_monthly_cost": months[0].base_monthly_cost,
                "avg_inflated_monthly_cost": round(
                    sum(m.inflated_monthly_cost for m in months) / len(months), 2
                ),
                "total_inflated_cost": round(
                    sum(m.inflated_monthly_cost for m in months), 2
                ),
            })
        return result

    def open_claim(
        self,
        policy: LTCPolicyBase,
        care_level: CareLevel,
        start_month: int,
        monthly_amount: float,
    ) -> ClaimRecord:
        claim = ClaimRecord(
            claim_id=f"CLM-{self._next_claim_id:06d}",
            policy_number=policy.policy_number,
            care_level=care_level,
            start_month=start_month,
            monthly_amount_paid=monthly_amount,
        )
        self._next_claim_id += 1
        self._claims.append(claim)
        return claim

    def close_claim(self, claim_id: str, end_month: int) -> ClaimRecord | None:
        for claim in self._claims:
            if claim.claim_id == claim_id and claim.is_active:
                claim.end_month = end_month
                claim.is_active = False
                return claim
        return None

    def total_claims_paid(self, policy_number: str) -> float:
        return sum(
            c.total_paid for c in self._claims if c.policy_number == policy_number
        )

    def annual_cost_projection(
        self,
        base_monthly_cost: float,
        years: int = 30,
    ) -> list[dict[str, float]]:
        return [
            {
                "year": y + 1,
                "monthly_cost": round(self.inflate_cost(base_monthly_cost, y), 2),
                "annual_cost": round(self.inflate_cost(base_monthly_cost, y) * 12, 2),
            }
            for y in range(years)
        ]
