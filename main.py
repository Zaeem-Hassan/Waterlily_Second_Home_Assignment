"""
LTC Insurance Policy Modeling Tool — Main Entry Point

Compares ForeCare Fixed Annuity (Forethought Life) vs NYL MyCare Gold
(New York Life) using verified data from policy illustrations.
"""
from __future__ import annotations

from datetime import date

from models import (
    Client,
    Gender,
    UnderwritingClass,
    CareNeedsProfile,
    CareTrajectoryPhase,
    AnnuityLTC,
    LTCCoverageTier,
    TraditionalLTC,
    BenefitPeriod,
    EliminationPeriod,
    InflationProtection,
)
from engines.forecare_engine import ForeCareEngine
from engines.mycare_engine import MyCareEngine
from engines.comparison import run_full_comparison


# ═══════════════════════════════════════════════════════════════════
#  ASSUMPTIONS AND NOTES
# ═══════════════════════════════════════════════════════════════════

ASSUMPTIONS_AND_NOTES = """
======================================================================
                     ASSUMPTIONS AND NOTES
======================================================================

1. BENEFIT INTERPRETATION
-------------------------
   ForeCare (Forethought Life Fixed Annuity with LTC Rider):
   - ACCELERATED BENEFIT: Pays from the annuity's contract value (CV).
     Monthly benefit = min(actual care cost, $4,167 monthly cap, remaining CV).
     Rider charge is WAIVED during the accelerated benefit phase.
   - EXTENDED BENEFIT: After CV is exhausted, the insurer pays from a
     separate pool = $100,000 x (coverage_tier - 1) = $200,000 for Premier.
     Same monthly cap ($4,167) applies.
   - TOTAL POOL: CV at time of claim + $200,000 extended = ~$303,764
     (guaranteed) or ~$340,000+ (non-guaranteed).
   - Benefits are NOT inflation-adjusted. $4,167/mo is fixed for life.

   NYL MyCare Gold (New York Life Traditional LTC):
   - REIMBURSEMENT-BASED: Pays 80% of eligible expenses after deductible,
     up to the inflation-adjusted monthly maximum.
   - Monthly benefit cap starts at $5,000, grows 3% CPI-U annually.
     At age 82 (year 27): $5,000 x 1.03^27 = $11,138.
   - Lifetime maximum starts at $175,000, grows 3% CPI-U annually.
     At age 82 (year 27): $175,000 x 1.03^27 = $388,726.

2. ELIMINATION PERIOD ASSUMPTIONS
----------------------------------
   ForeCare:
   - 90-day elimination period per PDF illustration.
   - In our model we simplify: care benefits begin immediately at the
     care start month. The 90-day wait is acknowledged but not separately
     modeled because the trajectory phases already account for care ramp-up.

   NYL MyCare Gold:
   - 0-day elimination period (Gold Bundled Plan 3 includes this).
   - Benefits begin from the first eligible expense.

3. DEDUCTIBLE / COINSURANCE INTERACTION
----------------------------------------
   NYL MyCare Gold applies a two-layer cost-sharing structure:
   - LAYER 1 -- Dollar Deductible: $15,000 (grows with 3% CPI-U inflation).
     At age 82: $15,000 x 1.03^27 = $33,319. Client pays 100% of care
     costs until this cumulative threshold is met. The deductible carries
     across all care phases (home care -> assisted living -> nursing home).
   - LAYER 2 -- Coinsurance: After the deductible is satisfied, policy
     pays 80% and client pays 20% of each month's eligible expenses,
     up to the inflation-adjusted monthly cap.
   - The deductible is a one-time cumulative amount per benefit period,
     NOT a per-phase or per-incident reset.
   - Coinsurance is a permanent feature -- it never goes away, even after
     large cumulative payments.

   ForeCare has NO deductible or coinsurance -- benefits pay dollar-for-dollar
   up to the monthly cap from the contract value pool.

4. AMBIGUITIES FOUND
---------------------
   a) ForeCare interest crediting: PDF states "annual rates credited daily."
      We model daily compounding credited monthly: (1 + annual/365)^30 - 1.
      Using 30-day months introduces ~0.04% drift vs actual calendar days,
      which we accept as reasonable for a modeling tool.

   b) ForeCare rider charge floor: PDF states "monthly rider charges will
      not cause a reduction in contract value below the prior month's
      ending value." We implement: rider_charge = min(raw_charge, interest_credit)
      and contract_value = max(new_cv, prior_cv) during pre-care.

   c) NYL dividend schedule: PDF provides annual illustrated dividends for
      years 1-41. We extracted all 41 anchor points and interpolate linearly
      between them for fractional years. Dividends are NOT guaranteed --
      they are based on the current dividend scale and may change.

   d) NYL "Bundled Plan" structure: The Gold plan bundles the dollar
      deductible and 80/20 coinsurance together. The PDF does not allow
      separate selection of deductible and coinsurance levels.

   e) Care cost inflation rate: We use 5.4% annual inflation for care
      costs, reflecting recent Genworth Cost of Care Survey trends. This
      is distinct from the 3% CPI-U used for NYL benefit growth -- the
      gap means real purchasing power of NYL benefits erodes at ~2.4%/yr.

5. EDGE CASES CONSIDERED
-------------------------
   a) ForeCare CV can't go negative: If guaranteed rate (0.10% in yr10+)
      is less than rider charge (0.96%/yr), the floor protection keeps
      CV flat. Without this, CV would decline ~$857/yr.

   b) ForeCare benefit exhaustion: In the accelerated phase, benefits
      deplete the CV. When CV = $0, the engine transitions to the
      extended phase. If extended pool also exhausts, phase = EXHAUSTED
      and client pays 100%.

   c) NYL lifetime max exhaustion: If cumulative benefits reach the
      inflation-adjusted lifetime max, policy stops paying mid-care.
      Remaining months are 100% client responsibility.

   d) NYL deductible satisfied mid-month: The model applies deductible
      at the monthly level. If remaining deductible < monthly cost,
      the excess is split 80/20 in the same month.

   e) Short care duration (1 year): ForeCare ROI is negative because
      benefits < $100K premium. NYL ROI is near zero because premiums
      nearly equal benefits.

   f) No-care scenario: ForeCare preserves the contract value as a
      death benefit. NYL premiums are a sunk cost (return of premium
      rider available to age 65 only).

6. FORECARE INTEREST RATE SCHEDULE (from PDF)
----------------------------------------------
   GUARANTEED rates (used for conservative projections):
   +------------------------------+------------+
   | Period                       | Annual Rate|
   +------------------------------+------------+
   | Year 1                       |    4.50%   |
   | Years 2-9                    |    1.00%   |
   | Year 10+                     |    0.10%   |
   +------------------------------+------------+

   NON-GUARANTEED rates (current crediting rate):
   +------------------------------+------------+
   | Period                       | Annual Rate|
   +------------------------------+------------+
   | Year 1                       |    4.50%   |
   | Year 2+                      |    4.50%   |
   +------------------------------+------------+

   Rider charge: $0.00080 per dollar of CV per month (= 0.96%/yr)

   Withdrawal charge schedule:
   Year 1: 8%, Year 2: 8%, Year 3: 7%, Year 4: 6%, Year 5: 5%,
   Year 6: 4%, Year 7: 3%, Year 8: 2%, Year 9: 1%, Year 10+: 0%

7. NYL MYCARE GOLD DIVIDEND SCHEDULE (from PDF, illustrated scale)
-------------------------------------------------------------------
   All values are annual illustrated dividends in dollars.
   Gross annual premium: $2,013.00

   +------+-----+--------------+   +------+-----+--------------+
   |  Yr  | Age | Dividend ($) |   |  Yr  | Age | Dividend ($) |
   +------+-----+--------------+   +------+-----+--------------+
   |   1  |  55 |        0.00  |   |  22  |  76 |      563.00  |
   |   2  |  56 |        0.00  |   |  23  |  77 |      725.00  |
   |   3  |  57 |        0.00  |   |  24  |  78 |      940.00  |
   |   4  |  58 |        4.00  |   |  25  |  79 |    1,224.00  |
   |   5  |  59 |        5.00  |   |  26  |  80 |    1,510.00  |
   |   6  |  60 |        6.00  |   |  27  |  81 |    1,988.00  |
   |   7  |  61 |        9.00  |   |  28  |  82 |    2,588.00  |
   |   8  |  62 |       12.00  |   |  29  |  83 |    3,369.00  |
   |   9  |  63 |       17.00  |   |  30  |  84 |    4,405.00  |
   |  10  |  64 |       22.00  |   |  31  |  85 |    5,919.00  |
   |  11  |  65 |       33.00  |   |  32  |  86 |    7,195.00  |
   |  12  |  66 |       46.00  |   |  33  |  87 |    8,497.00  |
   |  13  |  67 |       58.00  |   |  34  |  88 |   10,018.00  |
   |  14  |  68 |       75.00  |   |  35  |  89 |   11,715.00  |
   |  15  |  69 |       96.00  |   |  36  |  90 |   13,580.00  |
   |  16  |  70 |      129.00  |   |  37  |  91 |   15,247.00  |
   |  17  |  71 |      166.00  |   |  38  |  92 |   17,153.00  |
   |  18  |  72 |      217.00  |   |  39  |  93 |   19,332.00  |
   |  19  |  73 |      285.00  |   |  40  |  94 |   21,823.00  |
   |  20  |  74 |      376.00  |   |  41  |  95 |   24,672.00  |
   |  21  |  75 |      422.00  |   +------+-----+--------------+
   +------+-----+--------------+

   Note: Dividends are NOT guaranteed. They are based on the current
   dividend scale and may be higher or lower in the future. Dividends
   offset premium -- net premium = gross premium - dividend.
   By year 27, annual dividend ($1,988) nearly equals gross premium ($2,013).

======================================================================
"""


def create_client() -> Client:
    care_needs = CareNeedsProfile(
        predicted_care_start_age=82,
        predicted_care_duration_years=3.8,
        annual_cost_inflation=0.054,
        current_adl_count=0,
        trajectory=[
            CareTrajectoryPhase("Early Care", 16, "Home care", 1998.0),
            CareTrajectoryPhase("Moderate Care", 14, "Assisted living", 7451.0),
            CareTrajectoryPhase("Full Care", 16, "Nursing home", 10889.0),
        ],
    )
    return Client(
        first_name="Valued",
        last_name="Client",
        date_of_birth=date(1969, 7, 1),
        gender=Gender.MALE,
        state_of_residence="California",
        underwriting_class=UnderwritingClass.PREFERRED,
        care_needs=care_needs,
    )


def create_forecare_policy(client: Client) -> AnnuityLTC:
    return AnnuityLTC(
        client=client,
        policy_number="FC-2024-001",
        effective_date=date(2024, 7, 1),
        max_monthly_benefit=4167.0,
        benefit_period=BenefitPeriod.SEVENTY_TWO_MONTHS,
        elimination_period=EliminationPeriod.NINETY_DAYS,
        single_premium=100_000.0,
        coverage_tier=LTCCoverageTier.PREMIER,
        rider_charge_per_dollar_per_month=0.00080,
        accelerated_benefit_months=24,
        extended_benefit_months=48,
    )


def create_nyl_policy(client: Client) -> TraditionalLTC:
    return TraditionalLTC(
        client=client,
        policy_number="NYL-2024-001",
        effective_date=date(2024, 7, 1),
        max_monthly_benefit=5000.0,
        benefit_period=BenefitPeriod.LIFETIME,
        elimination_period=EliminationPeriod.ZERO_DAYS,
        inflation_protection=InflationProtection.CPI_U_PURCHASE_OPTIONS,
        monthly_premium=167.73,
        annual_premium=2013.0,
        lifetime_max=175_000.0,
        coinsurance_pct=0.20,
        dollar_deductible=15_000.0,
        inflation_rate=0.03,
        plan_name="Gold (Bundled Plan 3)",
        return_of_premium_to_age=65,
    )


def run_forecare_scenarios(policy: AnnuityLTC, care_needs: CareNeedsProfile) -> None:
    print("=" * 70)
    print("FORECARE ENGINE -- ALL SCENARIOS")
    print("=" * 70)
    engine = ForeCareEngine(policy, care_needs, issue_age=55)
    results = engine.run_both_scenarios()
    for name, result in results.items():
        print(engine.print_summary(result))
        print()


def run_nyl_scenarios(policy: TraditionalLTC, care_needs: CareNeedsProfile) -> None:
    print("=" * 70)
    print("NYL MYCARE GOLD ENGINE -- ALL SCENARIOS")
    print("=" * 70)
    engine = MyCareEngine(policy, care_needs, issue_age=55)
    results = engine.run_all_scenarios()
    for name, result in results.items():
        print(engine.print_summary(result))
        print()


def main() -> None:
    print(ASSUMPTIONS_AND_NOTES)

    client = create_client()
    fc_policy = create_forecare_policy(client)
    nyl_policy = create_nyl_policy(client)

    print("=" * 70)
    print("CLIENT PROFILE")
    print("=" * 70)
    print(f"  Name:             {client.first_name} {client.last_name}")
    print(f"  Date of Birth:    {client.date_of_birth}")
    print(f"  Gender:           {client.gender.value}")
    print(f"  State:            {client.state_of_residence}")
    print(f"  Underwriting:     {client.underwriting_class.value}")
    print(f"  Issue Age:        55")
    cn = client.care_needs
    print(f"  Care Start Age:   {cn.predicted_care_start_age}")
    print(f"  Care Duration:    {cn.predicted_care_duration_years} years ({round(cn.predicted_care_duration_years * 12)} months)")
    print(f"  Cost Inflation:   {cn.annual_cost_inflation:.1%}")
    print(f"  Trajectory:")
    for phase in cn.trajectory:
        print(f"    - {phase.name}: {phase.duration_months} months, "
              f"{phase.care_environment}, ${phase.monthly_cost:,.0f}/mo")
    print()

    run_forecare_scenarios(fc_policy, client.care_needs)
    run_nyl_scenarios(nyl_policy, client.care_needs)

    print()
    print("#" * 70)
    print("#  FULL COMPARISON REPORT")
    print("#" * 70)
    print()
    report = run_full_comparison(fc_policy, nyl_policy, client.care_needs, output_dir=".")
    print(report)


if __name__ == "__main__":
    main()
