from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Any

from .client import Client


class BenefitPeriod(Enum):
    TWENTY_FOUR_MONTHS = 24
    THIRTY_SIX_MONTHS = 36
    FORTY_EIGHT_MONTHS = 48
    SIXTY_MONTHS = 60
    SEVENTY_TWO_MONTHS = 72
    LIFETIME = -1


class EliminationPeriod(Enum):
    ZERO_DAYS = 0
    THIRTY_DAYS = 30
    SIXTY_DAYS = 60
    NINETY_DAYS = 90
    ONE_HUNDRED_EIGHTY_DAYS = 180


class CoverageType(Enum):
    FACILITY_ONLY = "facility_only"
    HOME_CARE_ONLY = "home_care_only"
    COMPREHENSIVE = "comprehensive"


class InflationProtection(Enum):
    NONE = "none"
    SIMPLE_3 = "simple_3"
    SIMPLE_5 = "simple_5"
    COMPOUND_3 = "compound_3"
    COMPOUND_5 = "compound_5"
    CPI_U = "cpi_u"
    CPI_U_PURCHASE_OPTIONS = "cpi_u_purchase_options"


class PolicyStatus(Enum):
    ACTIVE = "active"
    ON_CLAIM = "on_claim"
    LAPSED = "lapsed"
    PAID_UP = "paid_up"
    SURRENDERED = "surrendered"


@dataclass
class LTCPolicyBase(ABC):
    client: Client
    policy_number: str
    effective_date: date
    max_monthly_benefit: float
    benefit_period: BenefitPeriod
    elimination_period: EliminationPeriod
    coverage_type: CoverageType = CoverageType.COMPREHENSIVE
    inflation_protection: InflationProtection = InflationProtection.NONE
    status: PolicyStatus = PolicyStatus.ACTIVE

    @property
    def daily_benefit_amount(self) -> float:
        return self.max_monthly_benefit / 30

    @property
    @abstractmethod
    def lifetime_maximum_benefit(self) -> float | None:
        ...

    @abstractmethod
    def benefit_available_at_year(self, policy_year: int) -> float:
        ...

    @abstractmethod
    def total_cost_to_client(self, through_year: int) -> float:
        ...

    @abstractmethod
    def policy_summary(self) -> dict[str, Any]:
        ...

    def monthly_benefit_at_year(self, policy_year: int) -> float:
        return self.max_monthly_benefit

    def benefit_period_months(self) -> int | None:
        if self.benefit_period == BenefitPeriod.LIFETIME:
            return None
        return self.benefit_period.value

    def __str__(self) -> str:
        return (
            f"{type(self).__name__} {self.policy_number} | "
            f"{self.client.full_name} | ${self.max_monthly_benefit:,.2f}/mo"
        )
