from __future__ import annotations

import csv
import os
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from tabulate import tabulate

from models.annuity_ltc import AnnuityLTC, LTCCoverageTier
from models.traditional_ltc import TraditionalLTC
from models.base import BenefitPeriod, EliminationPeriod, InflationProtection
from models.client import (
    Client,
    Gender,
    UnderwritingClass,
    CareNeedsProfile,
    CareTrajectoryPhase,
)
from .forecare_engine import ForeCareEngine, ForeCareSimulationResult
from .mycare_engine import MyCareEngine, NYLSimulationResult


@dataclass
class ScenarioConfig:
    name: str
    care_start_age: int = 82
    care_duration_years: float = 3.8
    care_inflation: float = 0.054
    nyl_premium_increase_pct: float = 0.0
    nyl_premium_increase_year: int = 0
    trajectory: list[CareTrajectoryPhase] | None = None


DEFAULT_TRAJECTORY = [
    CareTrajectoryPhase("Early Care", 16, "Home care", 1998.0),
    CareTrajectoryPhase("Moderate Care", 14, "Assisted living", 7451.0),
    CareTrajectoryPhase("Full Care", 16, "Nursing home", 10889.0),
]


def scale_trajectory(
    base: list[CareTrajectoryPhase], target_months: int
) -> list[CareTrajectoryPhase]:
    base_total = sum(p.duration_months for p in base)
    if base_total == 0:
        return base
    ratio = target_months / base_total
    phases: list[CareTrajectoryPhase] = []
    allocated = 0
    for i, p in enumerate(base):
        if i == len(base) - 1:
            months = target_months - allocated
        else:
            months = max(1, round(p.duration_months * ratio))
        allocated += months
        phases.append(CareTrajectoryPhase(p.name, months, p.care_environment, p.monthly_cost))
    return phases


def _build_care_needs(cfg: ScenarioConfig) -> CareNeedsProfile:
    traj = cfg.trajectory or scale_trajectory(
        DEFAULT_TRAJECTORY, round(cfg.care_duration_years * 12)
    )
    return CareNeedsProfile(
        predicted_care_start_age=cfg.care_start_age,
        predicted_care_duration_years=cfg.care_duration_years,
        annual_cost_inflation=cfg.care_inflation,
        current_adl_count=0,
        trajectory=traj,
    )


def _run_forecare(
    policy: AnnuityLTC, care_needs: CareNeedsProfile
) -> ForeCareSimulationResult:
    engine = ForeCareEngine(policy, care_needs, issue_age=55)
    return engine.simulate(guaranteed=True)


def _run_nyl(
    policy: TraditionalLTC,
    care_needs: CareNeedsProfile,
    premium_increase_pct: float = 0.0,
    premium_increase_year: int = 0,
) -> NYLSimulationResult:
    if premium_increase_pct > 0 and premium_increase_year > 0:
        original_annual = policy.annual_premium
        policy.annual_premium = original_annual * (1 + premium_increase_pct)
        engine = MyCareEngine(policy, care_needs, issue_age=55)
        result = engine.simulate(with_dividends=True, with_inflation=True)
        policy.annual_premium = original_annual
        return result
    engine = MyCareEngine(policy, care_needs, issue_age=55)
    return engine.simulate(with_dividends=True, with_inflation=True)


def _investment_alternative(
    principal: float, annual_rate: float, years: int
) -> float:
    return principal * ((1 + annual_rate) ** years)


# ─── 1. Side-by-side cost table ──────────────────────────────────

def side_by_side_table(
    fc: ForeCareSimulationResult, nyl: NYLSimulationResult
) -> str:
    rows = [
        ["Total Care Cost", f"${fc.total_care_cost:,.2f}", f"${nyl.total_care_cost:,.2f}"],
        ["Total Benefit Paid", f"${fc.total_benefit_paid:,.2f}", f"${nyl.total_benefit_paid:,.2f}"],
        ["Total Out-of-Pocket", f"${fc.total_out_of_pocket:,.2f}", f"${nyl.total_out_of_pocket:,.2f}"],
        ["Deductible Cost", "$0.00", f"${nyl.total_deductible_cost:,.2f}"],
        ["Coinsurance Cost", "$0.00", f"${nyl.total_coinsurance_cost:,.2f}"],
        ["Coverage Ratio", f"{fc.coverage_ratio:.1%}", f"{nyl.coverage_ratio:.1%}"],
        ["ROI", f"{fc.roi:.1%}", f"{nyl.roi:.1%}"],
        [
            "Benefit Exhaustion Month",
            str(fc.benefit_exhaustion_month or "N/A"),
            str(nyl.benefit_exhaustion_month or "N/A"),
        ],
        ["Total Premiums/Cost", f"${100_000:,.2f} (single)", f"${nyl.total_net_premiums:,.2f} (net)"],
        ["Death Benefit/Residual", f"${fc.final_death_benefit:,.2f}", "N/A"],
    ]
    return tabulate(
        rows,
        headers=["Metric", "ForeCare (Guaranteed)", "NYL MyCare Gold"],
        tablefmt="grid",
    )


# ─── 2. ROI analysis ─────────────────────────────────────────────

def roi_analysis(
    fc: ForeCareSimulationResult, nyl: NYLSimulationResult
) -> str:
    fc_premium = 100_000.0
    fc_roi = (fc.total_benefit_paid - fc_premium) / fc_premium

    nyl_premiums = nyl.total_net_premiums
    nyl_roi = (nyl.total_benefit_paid - nyl_premiums) / nyl_premiums if nyl_premiums > 0 else 0.0

    years = 27
    rows = [
        ["ForeCare ROI", f"(${fc.total_benefit_paid:,.0f} - $100,000) / $100,000", f"{fc_roi:.1%}"],
        ["NYL MyCare ROI", f"(${nyl.total_benefit_paid:,.0f} - ${nyl_premiums:,.0f}) / ${nyl_premiums:,.0f}", f"{nyl_roi:.1%}"],
    ]

    header = "=== ROI Analysis ===\n"
    header += tabulate(rows, headers=["Policy", "Formula", "ROI"], tablefmt="grid")

    inv_rows = []
    for rate in [0.04, 0.045, 0.05, 0.055, 0.06]:
        fc_alt = _investment_alternative(fc_premium, rate, years)
        nyl_alt = _investment_alternative(nyl_premiums, rate, years)
        inv_rows.append([
            f"{rate:.1%}",
            f"${fc_alt:,.2f}",
            f"${fc.total_benefit_paid:,.2f}",
            "Yes" if fc.total_benefit_paid > fc_alt else "No",
            f"${nyl_alt:,.2f}",
            f"${nyl.total_benefit_paid:,.2f}",
            "Yes" if nyl.total_benefit_paid > nyl_alt else "No",
        ])

    header += "\n\n=== What If Invested Instead? (27 years) ===\n"
    header += tabulate(
        inv_rows,
        headers=[
            "Return Rate",
            "FC $100K Grows To", "FC Benefit", "FC Better?",
            "NYL Premiums Grow To", "NYL Benefit", "NYL Better?",
        ],
        tablefmt="grid",
    )
    return header


# ─── 3. Scenario comparisons ─────────────────────────────────────

SCENARIOS: list[ScenarioConfig] = [
    ScenarioConfig("Base Case", care_start_age=82, care_duration_years=3.8, care_inflation=0.054),
    ScenarioConfig("Care at 75", care_start_age=75, care_duration_years=3.8, care_inflation=0.054),
    ScenarioConfig("Care at 90", care_start_age=90, care_duration_years=3.8, care_inflation=0.054),
    ScenarioConfig("Duration 1yr", care_start_age=82, care_duration_years=1.0, care_inflation=0.054),
    ScenarioConfig("Duration 6yr", care_start_age=82, care_duration_years=6.0, care_inflation=0.054),
    ScenarioConfig("Inflation 3%", care_start_age=82, care_duration_years=3.8, care_inflation=0.03),
    ScenarioConfig("NYL +25% yr15", care_start_age=82, care_duration_years=3.8, care_inflation=0.054,
                   nyl_premium_increase_pct=0.25, nyl_premium_increase_year=15),
]


def run_scenarios(
    fc_policy: AnnuityLTC, nyl_policy: TraditionalLTC
) -> str:
    rows = []
    for cfg in SCENARIOS:
        care_needs = _build_care_needs(cfg)
        fc = _run_forecare(fc_policy, care_needs)
        nyl = _run_nyl(nyl_policy, care_needs, cfg.nyl_premium_increase_pct, cfg.nyl_premium_increase_year)

        fc_roi = (fc.total_benefit_paid - 100_000) / 100_000
        nyl_roi = (nyl.total_benefit_paid - nyl.total_net_premiums) / nyl.total_net_premiums if nyl.total_net_premiums > 0 else 0.0

        winner = "ForeCare" if fc.total_out_of_pocket < nyl.total_out_of_pocket else "NYL MyCare"

        rows.append([
            cfg.name,
            f"${fc.total_care_cost:,.0f}",
            f"${fc.total_benefit_paid:,.0f}",
            f"${fc.total_out_of_pocket:,.0f}",
            f"{fc.coverage_ratio:.1%}",
            f"{fc_roi:.0%}",
            str(fc.benefit_exhaustion_month or "N/A"),
            f"${nyl.total_benefit_paid:,.0f}",
            f"${nyl.total_out_of_pocket:,.0f}",
            f"{nyl.coverage_ratio:.1%}",
            f"{nyl_roi:.0%}",
            str(nyl.benefit_exhaustion_month or "N/A"),
            winner,
        ])

    return tabulate(
        rows,
        headers=[
            "Scenario", "Care Cost",
            "FC Benefit", "FC OOP", "FC Cov%", "FC ROI", "FC Exhaust",
            "NYL Benefit", "NYL OOP", "NYL Cov%", "NYL ROI", "NYL Exhaust",
            "Winner (OOP)",
        ],
        tablefmt="grid",
    )


# ─── 3b. Scenario deep-dive: which policy wins and why ───────────

def scenario_winners(
    fc_policy: AnnuityLTC, nyl_policy: TraditionalLTC
) -> str:
    lines: list[str] = [
        "=" * 70,
        "SCENARIO DEEP DIVE: WHICH POLICY WINS AND WHY",
        "=" * 70,
    ]

    # --- Scenario 1: Care at 75 ---
    cfg75 = ScenarioConfig("Care at 75", care_start_age=75, care_duration_years=3.8, care_inflation=0.054)
    cn75 = _build_care_needs(cfg75)
    fc75 = _run_forecare(fc_policy, cn75)
    nyl75 = _run_nyl(nyl_policy, cn75)
    lines += [
        "",
        "SCENARIO 1: Care starts at age 75 instead of 82",
        "-" * 50,
        f"  ForeCare:  ${fc75.total_benefit_paid:,.0f} benefit, ${fc75.total_out_of_pocket:,.0f} OOP, {fc75.coverage_ratio:.1%} coverage",
        f"  NYL MyCare: ${nyl75.total_benefit_paid:,.0f} benefit, ${nyl75.total_out_of_pocket:,.0f} OOP, {nyl75.coverage_ratio:.1%} coverage",
        "",
    ]
    if nyl75.total_out_of_pocket < fc75.total_out_of_pocket:
        lines.append(f"  WINNER: NYL MyCare Gold (saves ${fc75.total_out_of_pocket - nyl75.total_out_of_pocket:,.0f} OOP)")
        lines.append(f"  WHY: Even with only 20 years of CPI-U growth (vs 27), NYL's")
        lines.append(f"  inflation-adjusted benefits (${nyl75.total_benefit_paid:,.0f}) far exceed")
        lines.append(f"  ForeCare's fixed pool. NYL premiums are lower too (only 20yr of paying).")
    else:
        lines.append(f"  WINNER: ForeCare (saves ${nyl75.total_out_of_pocket - fc75.total_out_of_pocket:,.0f} OOP)")
        lines.append(f"  WHY: Earlier care means less CPI-U growth for NYL, narrowing its advantage.")

    # --- Scenario 2: Care lasts 6 years ---
    cfg6 = ScenarioConfig("Duration 6yr", care_start_age=82, care_duration_years=6.0, care_inflation=0.054)
    cn6 = _build_care_needs(cfg6)
    fc6 = _run_forecare(fc_policy, cn6)
    nyl6 = _run_nyl(nyl_policy, cn6)
    fc6_exhausted = fc6.benefit_exhaustion_month is not None
    nyl6_exhausted = nyl6.benefit_exhaustion_month is not None
    lines += [
        "",
        "SCENARIO 2: Care lasts 6 years instead of 3.8",
        "-" * 50,
        f"  ForeCare:  ${fc6.total_benefit_paid:,.0f} benefit, exhausted: {'YES (month ' + str(fc6.benefit_exhaustion_month) + ')' if fc6_exhausted else 'NO'}",
        f"  NYL MyCare: ${nyl6.total_benefit_paid:,.0f} benefit, exhausted: {'YES (month ' + str(nyl6.benefit_exhaustion_month) + ')' if nyl6_exhausted else 'NO'}",
        "",
    ]
    if nyl6_exhausted and not fc6_exhausted:
        lines.append(f"  WINNER: ForeCare (NYL runs out, ForeCare does not)")
    elif fc6_exhausted and not nyl6_exhausted:
        lines.append(f"  WINNER: NYL MyCare Gold (ForeCare runs out, NYL does not)")
    elif nyl6.total_out_of_pocket < fc6.total_out_of_pocket:
        lines.append(f"  WINNER: NYL MyCare Gold (saves ${fc6.total_out_of_pocket - nyl6.total_out_of_pocket:,.0f} OOP)")
    else:
        lines.append(f"  WINNER: ForeCare (saves ${nyl6.total_out_of_pocket - fc6.total_out_of_pocket:,.0f} OOP)")

    both_exhaust = fc6_exhausted and nyl6_exhausted
    if both_exhaust:
        lines.append(f"  NOTE: BOTH policies exhaust during 6 years of care.")
    if nyl6_exhausted:
        lines.append(f"  WHY: NYL's ${nyl6.total_benefit_paid:,.0f} lifetime max runs out at month {nyl6.benefit_exhaustion_month}.")
        lines.append(f"  The remaining months are 100% client responsibility.")
    if not fc6_exhausted:
        lines.append(f"  ForeCare's extended benefit pool covers the full 72 months at $4,167/mo cap.")
    else:
        lines.append(f"  ForeCare exhausts its pool at month {fc6.benefit_exhaustion_month}.")

    # --- Scenario 3: Client never needs care ---
    fc_engine_nocare = ForeCareEngine(fc_policy, _build_care_needs(
        ScenarioConfig("No Care", care_start_age=200, care_duration_years=0, care_inflation=0)
    ), issue_age=55)
    fc_nocare = fc_engine_nocare.simulate(guaranteed=True)
    fc_nocare_ng = fc_engine_nocare.simulate(guaranteed=False)

    nyl_total_premiums_27yr = nyl_policy.annual_premium * 27
    nyl_total_premiums_40yr = nyl_policy.annual_premium * 40

    lines += [
        "",
        "SCENARIO 3: Client never needs care",
        "-" * 50,
        f"  ForeCare death benefit (guaranteed, yr 27): ${fc_nocare.monthly_detail[323].contract_value:,.2f}",
        f"  ForeCare death benefit (non-guar, yr 27):   ${fc_nocare_ng.monthly_detail[323].contract_value:,.2f}",
        f"  NYL premiums lost (27 years):               ${nyl_total_premiums_27yr:,.2f}",
        f"  NYL premiums lost (40 years):               ${nyl_total_premiums_40yr:,.2f}",
        "",
        f"  WINNER: ForeCare",
        f"  WHY: ForeCare preserves the $100K as a death benefit passed to",
        f"  beneficiaries. The guaranteed CV at year 27 is ${fc_nocare.monthly_detail[323].contract_value:,.0f}",
        f"  (non-guaranteed: ${fc_nocare_ng.monthly_detail[323].contract_value:,.0f}). NYL premiums are a",
        f"  sunk cost -- ${nyl_total_premiums_27yr:,.0f} over 27 years with no return.",
        f"  NYL's return-of-premium rider only applies to age 65 (first 10 years).",
    ]

    # --- Scenario 4: NYL premium increase 25% at year 15 ---
    cfg_inc = ScenarioConfig("NYL +25% yr15", care_start_age=82, care_duration_years=3.8,
                             care_inflation=0.054, nyl_premium_increase_pct=0.25, nyl_premium_increase_year=15)
    cn_inc = _build_care_needs(cfg_inc)
    fc_inc = _run_forecare(fc_policy, cn_inc)
    nyl_inc = _run_nyl(nyl_policy, cn_inc, 0.25, 15)
    nyl_base = _run_nyl(nyl_policy, cn_inc)

    nyl_roi_base = (nyl_base.total_benefit_paid - nyl_base.total_net_premiums) / nyl_base.total_net_premiums if nyl_base.total_net_premiums > 0 else 0
    nyl_roi_inc = (nyl_inc.total_benefit_paid - nyl_inc.total_net_premiums) / nyl_inc.total_net_premiums if nyl_inc.total_net_premiums > 0 else 0

    lines += [
        "",
        "SCENARIO 4: NYL premiums increase 25% at year 15",
        "-" * 50,
        f"  NYL base ROI:        {nyl_roi_base:.1%} (net premiums: ${nyl_base.total_net_premiums:,.0f})",
        f"  NYL +25% yr15 ROI:   {nyl_roi_inc:.1%} (net premiums: ${nyl_inc.total_net_premiums:,.0f})",
        f"  ROI change:          {nyl_roi_inc - nyl_roi_base:+.1%}",
        f"  ForeCare ROI:        {fc_inc.roi:.1%} (unaffected by NYL rate changes)",
        "",
        f"  WINNER: NYL MyCare Gold still wins on OOP (${nyl_inc.total_out_of_pocket:,.0f} vs ${fc_inc.total_out_of_pocket:,.0f})"
        if nyl_inc.total_out_of_pocket < fc_inc.total_out_of_pocket
        else f"  WINNER: ForeCare wins on OOP (${fc_inc.total_out_of_pocket:,.0f} vs ${nyl_inc.total_out_of_pocket:,.0f})",
        f"  WHY: A 25% premium increase from year 15 raises total NYL cost by",
        f"  ${nyl_inc.total_net_premiums - nyl_base.total_net_premiums:,.0f}, dropping ROI from {nyl_roi_base:.0%} to {nyl_roi_inc:.0%}.",
        f"  ForeCare is immune to premium risk -- the $100K single payment is locked in.",
        f"  This scenario highlights ForeCare's advantage as a hedge against",
        f"  NYL's guaranteed-renewable (not level) premium structure.",
    ]

    return "\n".join(lines)


# ─── 4. Tradeoff summary ─────────────────────────────────────────

def tradeoff_summary() -> str:
    lines = [
        "=" * 70,
        "TRADEOFF SUMMARY",
        "=" * 70,
        "",
        "WHEN FORECARE IS BETTER:",
        "  - Short care duration (1-2 years): $4,167/mo cap is less painful",
        "    when total costs stay within the ~$300K benefit pool",
        "  - Client wants guaranteed lump-sum asset protection: $100K premium",
        "    preserved as death benefit if care is never needed",
        "  - No ongoing premium risk: single payment, no rate increases",
        "  - Higher interest rate environment: non-guaranteed scenario grows",
        "    the contract value and total pool significantly",
        "  - Client dies before needing care: full CV passes to beneficiaries",
        "",
        "WHEN NYL MYCARE IS BETTER:",
        "  - Long care duration (3+ years): inflation-adjusted benefits grow",
        "    to cover rising costs, higher monthly cap (~$11K at 82 vs $4,167)",
        "  - Lower initial outlay: $167.73/mo vs $100,000 lump sum",
        "  - Inflation protection: 3% CPI-U compounds benefits and lifetime",
        "    max, keeping pace with care cost increases",
        "  - Dividend participation: offsets premiums over time",
        "  - Premium waiver during claim: stops premium drain when on benefits",
        "  - Higher monthly benefit cap covers expensive nursing home care",
        "",
        "KEY RISK FACTORS:",
        "  - ForeCare: $4,167/mo cap creates large gap in nursing home phase",
        "    ($10,889+ costs); benefit pool is fixed (no inflation growth)",
        "  - NYL: 20% coinsurance is permanent drag; $15K+ deductible at age",
        "    82; premiums can increase (guaranteed renewable, not level);",
        "    dividends are NOT guaranteed",
        "  - Both: actual care costs may exceed projections; 5.4% inflation",
        "    assumption is aggressive; care duration is uncertain",
        "",
    ]
    return "\n".join(lines)


# ─── 5. Recommendation ───────────────────────────────────────────

def recommendation(
    fc: ForeCareSimulationResult, nyl: NYLSimulationResult
) -> str:
    lines = [
        "=" * 70,
        "RECOMMENDATION FOR THIS CLIENT",
        "=" * 70,
        "",
        "DOCUMENTED ASSUMPTIONS:",
        "  - Client: Male, age 55, California, Preferred underwriting",
        "  - Care starts at age 82, lasts 3.8 years (46 months)",
        "  - Three-phase trajectory: home -> assisted living -> nursing home",
        "  - Care cost inflation: 5.4% annually",
        "  - NYL CPI-U inflation protection: 3% annually",
        "  - ForeCare: guaranteed interest rate scenario (conservative)",
        "  - NYL: with dividends + with inflation (base case)",
        "",
        f"RESULTS:",
        f"  ForeCare: ${fc.total_benefit_paid:,.0f} benefit, "
        f"${fc.total_out_of_pocket:,.0f} OOP, {fc.coverage_ratio:.1%} coverage",
        f"  NYL MyCare: ${nyl.total_benefit_paid:,.0f} benefit, "
        f"${nyl.total_out_of_pocket:,.0f} OOP, {nyl.coverage_ratio:.1%} coverage",
        "",
    ]

    if nyl.total_out_of_pocket < fc.total_out_of_pocket:
        savings = fc.total_out_of_pocket - nyl.total_out_of_pocket
        lines += [
            f"RECOMMENDATION: NYL MyCare Gold",
            f"  NYL saves ${savings:,.0f} in out-of-pocket costs for this scenario.",
            f"  The CPI-U inflation protection more than doubles the coverage ratio",
            f"  compared to ForeCare's fixed benefit. Despite higher cumulative",
            f"  premiums (${nyl.total_net_premiums:,.0f} vs $100,000), the significantly",
            f"  larger benefit pool and higher monthly cap justify the cost.",
            "",
            f"  HOWEVER, ForeCare has value as a COMPLEMENTARY product:",
            f"  - If client can afford $100K lump sum + NYL premiums, combining both",
            f"    provides ${fc.total_benefit_paid + nyl.total_benefit_paid:,.0f} total benefits",
            f"  - ForeCare's death benefit protects the $100K if care isn't needed",
            f"  - ForeCare covers the NYL deductible period and coinsurance gap",
        ]
    else:
        lines += [
            f"RECOMMENDATION: ForeCare Fixed Annuity",
            f"  ForeCare provides lower out-of-pocket costs in this scenario.",
        ]

    return "\n".join(lines)


# ─── 6. CSV export ────────────────────────────────────────────────

def save_monthly_csv(
    fc: ForeCareSimulationResult,
    nyl: NYLSimulationResult,
    output_dir: str = ".",
) -> tuple[str, str]:
    fc_path = os.path.join(output_dir, "forecare_monthly.csv")
    with open(fc_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "month", "policy_year", "age", "phase", "interest_rate",
            "interest_credit", "rider_charge", "contract_value",
            "care_cost", "benefit_paid", "client_oop",
            "accel_remaining", "ext_remaining", "total_ltc_remaining",
            "cum_benefit", "cum_rider", "cum_care_cost", "cum_oop",
        ])
        for m in fc.monthly_detail:
            writer.writerow([
                m.month, m.policy_year, m.age, m.phase.value, m.interest_rate,
                m.interest_credit, m.rider_charge, m.contract_value,
                m.care_cost, m.benefit_paid, m.client_out_of_pocket,
                m.accelerated_benefit_remaining, m.extended_benefit_remaining,
                m.total_ltc_remaining, m.cumulative_benefit_paid,
                m.cumulative_rider_charges, m.cumulative_care_cost,
                m.cumulative_out_of_pocket,
            ])

    nyl_path = os.path.join(output_dir, "nyl_mycare_monthly.csv")
    with open(nyl_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "month", "policy_year", "age", "phase",
            "gross_premium", "dividend", "net_premium", "premium_waived",
            "cum_gross", "cum_div", "cum_net",
            "adj_monthly_max", "adj_lifetime_max", "adj_deductible",
            "care_cost", "deductible_applied", "coinsurance", "benefit_paid",
            "client_oop", "remaining_deductible", "remaining_lifetime",
            "cum_benefit", "cum_care_cost", "cum_oop",
        ])
        for m in nyl.monthly_detail:
            writer.writerow([
                m.month, m.policy_year, m.age, m.phase.value,
                m.gross_monthly_premium, m.monthly_dividend, m.net_monthly_premium,
                m.premium_waived, m.cumulative_gross_premiums, m.cumulative_dividends,
                m.cumulative_net_premiums, m.inflation_adjusted_monthly_max,
                m.inflation_adjusted_lifetime_max, m.inflation_adjusted_deductible,
                m.care_cost, m.deductible_applied, m.coinsurance_amount,
                m.benefit_paid, m.client_out_of_pocket, m.remaining_deductible,
                m.remaining_lifetime_benefit, m.cumulative_benefit_paid,
                m.cumulative_care_cost, m.cumulative_out_of_pocket,
            ])

    return fc_path, nyl_path


# ─── Main comparison runner ───────────────────────────────────────

def run_full_comparison(
    fc_policy: AnnuityLTC,
    nyl_policy: TraditionalLTC,
    care_needs: CareNeedsProfile,
    output_dir: str = ".",
) -> str:
    fc_engine = ForeCareEngine(fc_policy, care_needs, issue_age=55)
    fc = fc_engine.simulate(guaranteed=True)

    nyl_engine = MyCareEngine(nyl_policy, care_needs, issue_age=55)
    nyl = nyl_engine.simulate(with_dividends=True, with_inflation=True)

    sections: list[str] = []

    sections.append("=" * 70)
    sections.append("1. SIDE-BY-SIDE COST COMPARISON")
    sections.append("=" * 70)
    sections.append(side_by_side_table(fc, nyl))

    sections.append("")
    sections.append("=" * 70)
    sections.append("2. ROI ANALYSIS")
    sections.append("=" * 70)
    sections.append(roi_analysis(fc, nyl))

    sections.append("")
    sections.append("=" * 70)
    sections.append("3. SCENARIO COMPARISONS")
    sections.append("=" * 70)
    sections.append(run_scenarios(fc_policy, nyl_policy))

    sections.append("")
    sections.append(scenario_winners(fc_policy, nyl_policy))

    sections.append("")
    sections.append(tradeoff_summary())

    sections.append("")
    sections.append(recommendation(fc, nyl))

    fc_csv, nyl_csv = save_monthly_csv(fc, nyl, output_dir)
    sections.append("")
    sections.append(f"Month-by-month data saved to:")
    sections.append(f"  ForeCare: {fc_csv}")
    sections.append(f"  NYL MyCare: {nyl_csv}")

    return "\n".join(sections)
