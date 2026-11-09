# LTC Insurance Policy Modeling Tool

A Python-based financial modeling tool that compares two long-term care (LTC) insurance products using month-by-month simulation engines and verified data extracted from actual policy illustration PDFs.

**Policies compared:**
- **ForeCare Fixed Annuity** (Forethought Life) — $100,000 single-premium annuity with LTC rider
- **NYL MyCare Gold** (New York Life) — Traditional LTC with CPI-U inflation protection

## Install & Run

```bash
# Install dependencies
pip install -r requirements.txt

# Run the full comparison
python main.py

# Save output to a file for easier review
python main.py > output.txt
```

**Requirements:** Python 3.10+ and `tabulate`.

## Project Structure

```
Exercise_2/
├── main.py                  # Entry point — runs everything, prints full report
├── comparison.py            # (root-level, unused — see engines/comparison.py)
├── requirements.txt
├── models/
│   ├── __init__.py          # Re-exports all model classes
│   ├── base.py              # LTCPolicyBase (ABC) with shared enums
│   ├── client.py            # Client, CareNeedsProfile, CareTrajectoryPhase
│   ├── annuity_ltc.py       # AnnuityLTC (ForeCare) with interest/withdrawal schedules
│   └── traditional_ltc.py   # TraditionalLTC (NYL MyCare) with dividend schedule
├── engines/
│   ├── __init__.py          # Re-exports all engine classes
│   ├── forecare_engine.py   # Month-by-month ForeCare simulation
│   ├── mycare_engine.py     # Month-by-month NYL MyCare simulation (41-yr dividends)
│   ├── comparison.py        # Side-by-side tables, ROI, scenarios, recommendation
│   ├── care_engine.py       # Care cost inflation and claims tracking
│   └── my_care_engine.py    # Legacy comparison engine (older version)
├── 55M ForeCare *.pdf       # Source PDF: ForeCare illustration
└── MyCare_F55_*.pdf         # Source PDF: NYL MyCare proposal
```

## Key Design Decisions

- **Dataclass-based models** with `from __future__ import annotations` for clean forward references. Abstract base class (`LTCPolicyBase`) makes the system extensible for future policy types.

- **Daily-compounded interest credited monthly:** The ForeCare PDF states "annual rates credited daily." We model this as `(1 + annual_rate/365)^30 - 1` per month, matching PDF values within 0.04%.

- **Rider charge floor protection:** ForeCare's rider charge is capped so it never reduces contract value below the prior month's ending value: `rider_charge = min(raw_charge, interest_credit)`.

- **Two-layer NYL cost sharing:** Dollar deductible ($15K, inflation-adjusted) is satisfied first across all care phases, then 80/20 coinsurance applies permanently. Deductible does not reset per phase.

- **Separate inflation rates:** Care costs inflate at 5.4% (Genworth survey data) while NYL benefits grow at 3% CPI-U. This 2.4% gap erodes real purchasing power over time — a key finding.

- **Premiums are level:** NYL premiums do not grow with CPI-U inflation (only benefits do). Premiums are guaranteed renewable at the filed rate unless NYL obtains a state-approved increase.

- **41-point dividend interpolation:** All dividend values extracted from the NYL PDF (years 1-41) are used as anchor points with linear interpolation between them.

## Summary of Findings

| Metric | ForeCare (Guaranteed) | NYL MyCare Gold |
|--------|----------------------|-----------------|
| Total Benefit Paid | $191,682 | $388,726 |
| Total Out-of-Pocket | $1,271,086 | $1,074,043 |
| Coverage Ratio | 13.1% | 26.6% |
| ROI | 91.7% | 755.8% |
| Total Cost to Client | $100,000 (single) | $45,423 (net) |

**Scenario winners:**
- **Care at 75:** NYL wins (saves $124K OOP) — inflation-adjusted benefits still dominate
- **Care lasts 6 years:** ForeCare wins — NYL exhausts its lifetime max; ForeCare's extended pool holds
- **Never needs care:** ForeCare wins — $103K+ death benefit vs $54K NYL premiums lost
- **NYL +25% premium at yr15:** NYL still wins on OOP, but ROI drops from 756% to 559%

**Recommendation:** NYL MyCare Gold for this client profile (male, 55, 3.8yr care at 82), with ForeCare as a complementary product for death benefit protection and deductible/coinsurance gap coverage.

## Limitations

- **30-day month approximation:** Using 30 days per month for daily compounding introduces ~0.04% drift vs actual calendar days over 27 years.
- **Dividends are not guaranteed:** NYL dividend projections use the current illustrated scale. Actual dividends may be higher or lower.
- **5.4% care inflation is aggressive:** Based on recent Genworth data but may not hold over 27 years. The 3% inflation scenario shows dramatically different results.
- **Elimination period simplified:** ForeCare's 90-day elimination period is acknowledged but not separately modeled; the care trajectory phases account for care ramp-up.
- **No tax modeling:** Tax treatment of annuity withdrawals, LTC benefit exclusions, and premium deductibility are not modeled.
- **Single client profile:** Results are specific to a 55-year-old male in California with Preferred underwriting. Different demographics would produce different results.
