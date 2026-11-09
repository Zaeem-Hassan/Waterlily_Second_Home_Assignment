from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Any

from .base import (
    LTCPolicyBase,
    BenefitPeriod,
    EliminationPeriod,
    CoverageType,
    InflationProtection,
)
from .client import Client


class LTCCoverageTier(Enum):
    STANDARD = 2
    PREMIER = 3


@dataclass
class InterestRateSchedule:
    year_one_rate: float
    years_two_through_nine_rate: float
    year_ten_plus_rate: float

    def rate_for_year(self, policy_year: int) -> float:
        if policy_year <= 1:
            return self.year_one_rate
        if policy_year <= 9:
            return self.years_two_through_nine_rate
        return self.year_ten_plus_rate


@dataclass
class WithdrawalChargeSchedule:
    charges_by_year: list[float]

    def charge_pct(self, policy_year: int) -> float:
        if policy_year < 1:
            return self.charges_by_year[0] if self.charges_by_year else 0.0
        idx = policy_year - 1
        if idx >= len(self.charges_by_year):
            return 0.0
        return self.charges_by_year[idx]


@dataclass
class AnnuityLTC(LTCPolicyBase):
    single_premium: float = 0.0
    coverage_tier: LTCCoverageTier = LTCCoverageTier.PREMIER
    guaranteed_rates: InterestRateSchedule = field(
        default_factory=lambda: InterestRateSchedule(
            year_one_rate=0.045,
            years_two_through_nine_rate=0.01,
            year_ten_plus_rate=0.001,
        )
    )
    current_crediting_rate: float = 0.045
    rider_charge_per_dollar_per_month: float = 0.00080
    accelerated_benefit_months: int = 24
    extended_benefit_months: int = 48
    withdrawal_charges: WithdrawalChargeSchedule = field(
        default_factory=lambda: WithdrawalChargeSchedule(
            charges_by_year=[0.08, 0.08, 0.07, 0.06, 0.05, 0.04, 0.03, 0.02, 0.01]
        )
    )
    inflation_rider_included: bool = False
    nonforfeiture_rider_included: bool = False
    married_discount_included: bool = False

    @property
    def total_scheduled_benefit_months(self) -> int:
        return self.accelerated_benefit_months + self.extended_benefit_months

    @property
    def ltc_multiplier(self) -> int:
        return self.coverage_tier.value

    @property
    def lifetime_maximum_benefit(self) -> float | None:
        return None

    def _contract_value_guaranteed(self, at_year: int) -> float:
        value = self.single_premium
        for yr in range(1, at_year + 1):
            rate = self.guaranteed_rates.rate_for_year(yr)
            annual_interest = value * rate
            annual_rider_charge = value * self.rider_charge_per_dollar_per_month * 12
            value = value + annual_interest - annual_rider_charge
        return value

    def _contract_value_nonguaranteed(self, at_year: int) -> float:
        value = self.single_premium
        for yr in range(1, at_year + 1):
            if yr == 1:
                rate = self.guaranteed_rates.year_one_rate
            else:
                rate = self.current_crediting_rate
            annual_interest = value * rate
            annual_rider_charge = value * self.rider_charge_per_dollar_per_month * 12
            value = value + annual_interest - annual_rider_charge
        return value

    def contract_value(self, at_year: int, guaranteed: bool = True) -> float:
        if guaranteed:
            return self._contract_value_guaranteed(at_year)
        return self._contract_value_nonguaranteed(at_year)

    def accelerated_benefit(self, at_year: int, guaranteed: bool = True) -> float:
        return self.contract_value(at_year, guaranteed)

    def max_extended_benefit(self, at_year: int) -> float:
        return self.single_premium * (self.ltc_multiplier - 1)

    def total_ltc_benefit(self, at_year: int, guaranteed: bool = True) -> float:
        return (
            self.accelerated_benefit(at_year, guaranteed)
            + self.max_extended_benefit(at_year)
        )

    def death_benefit(self, at_year: int, guaranteed: bool = True) -> float:
        return self.contract_value(at_year, guaranteed)

    def withdrawal_value(self, at_year: int, guaranteed: bool = True) -> float:
        cv = self.contract_value(at_year, guaranteed)
        charge = self.withdrawal_charges.charge_pct(at_year)
        return cv * (1 - charge)

    def benefit_available_at_year(self, policy_year: int) -> float:
        return self.total_ltc_benefit(policy_year, guaranteed=True)

    def total_cost_to_client(self, through_year: int) -> float:
        return self.single_premium

    def yearly_illustration(
        self, years: int = 45, guaranteed: bool = True
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for yr in range(1, years + 1):
            cv = self.contract_value(yr, guaranteed)
            rows.append({
                "year": yr,
                "age": self.client.issue_age + yr,
                "interest_rate": (
                    self.guaranteed_rates.rate_for_year(yr) if guaranteed
                    else (self.guaranteed_rates.year_one_rate if yr == 1
                          else self.current_crediting_rate)
                ),
                "contract_value": round(cv, 2),
                "withdrawal_value": round(self.withdrawal_value(yr, guaranteed), 2),
                "accelerated_benefit": round(self.accelerated_benefit(yr, guaranteed), 2),
                "max_extended_benefit": round(self.max_extended_benefit(yr), 2),
                "total_ltc_benefit": round(
                    self.total_ltc_benefit(yr, guaranteed), 2
                ),
                "max_monthly_benefit": self.max_monthly_benefit,
            })
        return rows

    def policy_summary(self) -> dict[str, Any]:
        return {
            "type": "annuity_ltc",
            "product": "ForeCare Fixed Annuity",
            "issuer": "Forethought Life Insurance Company",
            "policy_number": self.policy_number,
            "insured": self.client.full_name,
            "issue_age": self.client.issue_age,
            "state": self.client.state_of_residence,
            "effective_date": self.effective_date.isoformat(),
            "single_premium": self.single_premium,
            "coverage_tier": f"{self.coverage_tier.name} ({self.ltc_multiplier}x)",
            "guaranteed_rate_yr1": self.guaranteed_rates.year_one_rate,
            "guaranteed_rate_yr2_9": self.guaranteed_rates.years_two_through_nine_rate,
            "guaranteed_rate_yr10_plus": self.guaranteed_rates.year_ten_plus_rate,
            "rider_charge": self.rider_charge_per_dollar_per_month,
            "accelerated_months": self.accelerated_benefit_months,
            "extended_months": self.extended_benefit_months,
            "total_benefit_months": self.total_scheduled_benefit_months,
            "max_monthly_benefit": self.max_monthly_benefit,
            "inflation_rider": self.inflation_rider_included,
            "nonforfeiture_rider": self.nonforfeiture_rider_included,
        }

    def __str__(self) -> str:
        return (
            f"ForeCare {self.policy_number} | {self.client.full_name} | "
            f"${self.single_premium:,.0f} premium | "
            f"{self.ltc_multiplier}x | ${self.max_monthly_benefit:,.0f}/mo max"
        )
