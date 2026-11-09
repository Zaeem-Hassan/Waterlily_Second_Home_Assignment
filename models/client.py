from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum


class Gender(Enum):
    MALE = "male"
    FEMALE = "female"


class HealthStatus(Enum):
    PREFERRED = "preferred"
    STANDARD = "standard"
    SUBSTANDARD = "substandard"


class UnderwritingClass(Enum):
    PREFERRED_PLUS = "preferred_plus"
    PREFERRED = "preferred"
    STANDARD_PLUS = "standard_plus"
    STANDARD = "standard"
    SUBSTANDARD = "substandard"


@dataclass
class CareTrajectoryPhase:
    name: str
    duration_months: int
    care_environment: str
    monthly_cost: float

    @property
    def total_cost(self) -> float:
        return self.monthly_cost * self.duration_months


@dataclass
class CareNeedsProfile:
    predicted_care_start_age: int
    predicted_care_duration_years: float
    annual_cost_inflation: float
    current_adl_count: int
    trajectory: list[CareTrajectoryPhase] = field(default_factory=list)

    @property
    def total_trajectory_months(self) -> int:
        return sum(p.duration_months for p in self.trajectory)

    @property
    def total_trajectory_cost_today(self) -> float:
        return sum(p.total_cost for p in self.trajectory)

    def phase_at_month(self, month: int) -> CareTrajectoryPhase | None:
        elapsed = 0
        for phase in self.trajectory:
            if elapsed + phase.duration_months > month:
                return phase
            elapsed += phase.duration_months
        return None


@dataclass
class Client:
    first_name: str
    last_name: str
    date_of_birth: date
    gender: Gender
    state_of_residence: str
    underwriting_class: UnderwritingClass = UnderwritingClass.STANDARD
    health_status: HealthStatus = HealthStatus.STANDARD
    smoker: bool = False
    marital_status: str = "single"
    annual_income: float = 0.0
    net_worth: float = 0.0
    existing_ltc_coverage: bool = False
    care_needs: CareNeedsProfile | None = None

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}"

    @property
    def age(self) -> int:
        today = date.today()
        return today.year - self.date_of_birth.year - (
            (today.month, today.day) < (self.date_of_birth.month, self.date_of_birth.day)
        )

    @property
    def issue_age(self) -> int:
        return self.age

    @property
    def years_until_care(self) -> int | None:
        if self.care_needs is None:
            return None
        return max(0, self.care_needs.predicted_care_start_age - self.age)

    def is_eligible(self, min_age: int = 18, max_age: int = 84) -> bool:
        return min_age <= self.age <= max_age

    def __str__(self) -> str:
        return (
            f"{self.full_name}, age {self.age}, "
            f"{self.underwriting_class.value}, {self.state_of_residence}"
        )
