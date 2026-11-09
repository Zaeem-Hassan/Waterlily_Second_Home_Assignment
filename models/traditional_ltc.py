from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from .base import (
    LTCPolicyBase,
    BenefitPeriod,
    EliminationPeriod,
    CoverageType,
    InflationProtection,
)
from .client import Client


@dataclass
class DividendScheduleEntry:
    policy_year: int
    age: int
    gross_premium: float
    midpoint_dividend: float
    midpoint_net_premium: float
    illustrated_dividend: float
    illustrated_net_premium: float
    cumulative_illustrated_dividend: float
    policy_lifetime_maximum: float


@dataclass
class TraditionalLTC(LTCPolicyBase):
    monthly_premium: float = 0.0
    annual_premium: float = 0.0
    lifetime_max: float = 0.0
    coinsurance_pct: float = 0.0
    dollar_deductible: float = 0.0
    inflation_rate: float = 0.0
    plan_name: str = ""
    participating: bool = True
    guaranteed_renewable: bool = True
    waiver_of_premium: bool = True
    return_of_premium_to_age: int | None = None
    international_coverage: bool = True
    care_plan_benefit: bool = True
    caregiver_relief: bool = True
    home_support_equipment: bool = True
    hospice_care: bool = True
    alternate_plan_of_care: bool = True
    shared_pools_rider: bool = False
    nonforfeiture_rider: bool = False
    dividend_schedule: list[DividendScheduleEntry] = field(default_factory=list)

    @property
    def lifetime_maximum_benefit(self) -> float | None:
        return self.lifetime_max

    @property
    def client_pays_pct(self) -> float:
        return self.coinsurance_pct

    @property
    def insurer_pays_pct(self) -> float:
        return 1.0 - self.coinsurance_pct

    def benefit_at_year(self, policy_year: int) -> dict[str, float]:
        if self.inflation_protection in (
            InflationProtection.CPI_U,
            InflationProtection.CPI_U_PURCHASE_OPTIONS,
        ):
            growth = (1 + self.inflation_rate) ** (policy_year - 1)
        elif self.inflation_protection == InflationProtection.COMPOUND_3:
            growth = 1.03 ** (policy_year - 1)
        elif self.inflation_protection == InflationProtection.COMPOUND_5:
            growth = 1.05 ** (policy_year - 1)
        else:
            growth = 1.0

        adjusted_monthly = self.max_monthly_benefit * growth
        adjusted_lifetime = self.lifetime_max * growth
        adjusted_deductible = self.dollar_deductible * growth

        return {
            "policy_year": policy_year,
            "max_monthly_benefit": round(adjusted_monthly, 2),
            "policy_lifetime_maximum": round(adjusted_lifetime, 2),
            "dollar_deductible": round(adjusted_deductible, 2),
            "coinsurance_pct": self.coinsurance_pct,
        }

    def reimbursement_for_expense(
        self,
        monthly_expense: float,
        policy_year: int,
        deductible_satisfied: bool = False,
    ) -> dict[str, float]:
        benefits = self.benefit_at_year(policy_year)
        adjusted_monthly_max = benefits["max_monthly_benefit"]

        if not deductible_satisfied:
            return {
                "expense": monthly_expense,
                "reimbursed": 0.0,
                "client_pays": monthly_expense,
                "applied_to_deductible": min(
                    monthly_expense, benefits["dollar_deductible"]
                ),
            }

        eligible = monthly_expense * self.insurer_pays_pct
        reimbursed = min(eligible, adjusted_monthly_max)
        client_pays = monthly_expense - reimbursed

        return {
            "expense": monthly_expense,
            "reimbursed": round(reimbursed, 2),
            "client_pays": round(client_pays, 2),
            "coinsurance_portion": round(monthly_expense * self.coinsurance_pct, 2),
        }

    def premium_at_year(self, policy_year: int) -> dict[str, float]:
        if self.inflation_protection in (
            InflationProtection.CPI_U,
            InflationProtection.CPI_U_PURCHASE_OPTIONS,
        ):
            growth = (1 + self.inflation_rate) ** (policy_year - 1)
        else:
            growth = 1.0

        gross = self.annual_premium * growth

        dividend_entry = next(
            (d for d in self.dividend_schedule if d.policy_year == policy_year),
            None,
        )
        illustrated_dividend = dividend_entry.illustrated_dividend if dividend_entry else 0.0
        net = gross - illustrated_dividend

        return {
            "policy_year": policy_year,
            "gross_annual_premium": round(gross, 2),
            "illustrated_dividend": round(illustrated_dividend, 2),
            "net_annual_premium": round(net, 2),
        }

    def cumulative_premiums(self, through_year: int) -> float:
        total = 0.0
        for yr in range(1, through_year + 1):
            info = self.premium_at_year(yr)
            total += info["net_annual_premium"]
        return round(total, 2)

    def benefit_available_at_year(self, policy_year: int) -> float:
        benefits = self.benefit_at_year(policy_year)
        return benefits["policy_lifetime_maximum"]

    def total_cost_to_client(self, through_year: int) -> float:
        return self.cumulative_premiums(through_year)

    def monthly_benefit_at_year(self, policy_year: int) -> float:
        benefits = self.benefit_at_year(policy_year)
        return benefits["max_monthly_benefit"]

    def yearly_illustration(self, years: int = 46) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        cumulative_premium = 0.0
        for yr in range(1, years + 1):
            benefits = self.benefit_at_year(yr)
            premium_info = self.premium_at_year(yr)
            cumulative_premium += premium_info["net_annual_premium"]

            rows.append({
                "year": yr,
                "age": self.client.issue_age + yr - 1,
                "max_monthly_benefit": benefits["max_monthly_benefit"],
                "policy_lifetime_maximum": benefits["policy_lifetime_maximum"],
                "dollar_deductible": benefits["dollar_deductible"],
                "gross_premium": premium_info["gross_annual_premium"],
                "illustrated_dividend": premium_info["illustrated_dividend"],
                "net_premium": premium_info["net_annual_premium"],
                "cumulative_premium": round(cumulative_premium, 2),
            })
        return rows

    def policy_summary(self) -> dict[str, Any]:
        return {
            "type": "traditional_ltc",
            "product": "NYL MyCare Long-Term Care Insurance",
            "issuer": "New York Life Insurance Company",
            "plan_name": self.plan_name,
            "policy_number": self.policy_number,
            "insured": self.client.full_name,
            "issue_age": self.client.issue_age,
            "underwriting_class": self.client.underwriting_class.value,
            "state": self.client.state_of_residence,
            "effective_date": self.effective_date.isoformat(),
            "monthly_premium": self.monthly_premium,
            "annual_premium": self.annual_premium,
            "max_monthly_benefit": self.max_monthly_benefit,
            "lifetime_max": self.lifetime_max,
            "coinsurance_pct": f"{self.coinsurance_pct:.0%}",
            "dollar_deductible": self.dollar_deductible,
            "inflation_protection": self.inflation_protection.value,
            "inflation_rate": f"{self.inflation_rate:.1%}",
            "participating": self.participating,
            "guaranteed_renewable": self.guaranteed_renewable,
            "included_benefits": [
                name for name, included in [
                    ("waiver_of_premium", self.waiver_of_premium),
                    ("international_coverage", self.international_coverage),
                    ("care_plan_benefit", self.care_plan_benefit),
                    ("caregiver_relief", self.caregiver_relief),
                    ("home_support_equipment", self.home_support_equipment),
                    ("hospice_care", self.hospice_care),
                    ("alternate_plan_of_care", self.alternate_plan_of_care),
                ] if included
            ],
        }

    def __str__(self) -> str:
        return (
            f"NYL MyCare {self.policy_number} | {self.client.full_name} | "
            f"${self.monthly_premium:.2f}/mo premium | "
            f"${self.max_monthly_benefit:,.0f}/mo benefit | "
            f"${self.lifetime_max:,.0f} lifetime max"
        )
