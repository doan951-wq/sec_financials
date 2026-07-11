"""Parses companyfacts JSON: tag-priority fallback lists for Revenue /
Cost of Sales, per-accession-number grouping and duration filtering to pick
one row per filing/period, and Gross Profit computation.

Implements (Milestone 3):
- Fetch companyfacts/CIK##########.json for a resolved CIK.
- Tag-priority fallback lists per concept:
    Revenue: Revenues -> RevenueFromContractWithCustomerExcludingAssessedTax
             -> SalesRevenueNet
    Cost of Sales: CostOfRevenue -> CostOfGoodsAndServicesSold
             -> CostOfGoodsSold -> CostOfServices
  Fallback is resolved **per accession/filing**, not once globally per
  company: companies commonly switch XBRL tags over time as SEC taxonomies
  evolve (e.g. Apple's `Revenues` tag has no data after fiscal 2018 even
  though the tag is present in the company's facts; its filings from
  fiscal 2019 onward use `RevenueFromContractWithCustomerExcludingAssessedTax`
  instead). Picking a single "winning" tag for the whole company would
  silently blank out Revenue for every filing not covered by whichever
  tag happens to be first in the priority list and present at all -- this
  was caught by a live smoke test against Apple's real companyfacts data
  and is why the per-accession merge below exists. For a given accession,
  the highest-priority tag that has data for that specific accession
  wins; lower-priority tags fill in accessions the higher-priority tag
  doesn't cover.
- Group same-concept data points by accession number (accn) -- one
  accession number is one filing.
- Within an accession number, filter to the duration band matching the
  form type (annual ~350-380 days for 10-K, quarterly ~80-100 days for
  10-Q), then take the entry with the MAXIMUM `end` date among survivors.
  This is the verified-correct disambiguation rule (see PLAN.md): a
  single accession routinely contains multiple entries in the same
  duration band (current year plus one or two comparative prior years
  for a 10-K; a ~90-day quarter entry alongside a ~181-day YTD entry for
  a 10-Q). There is no top-level per-filing `end`/`fy`/`fp` to match
  against -- fy/fp are identical across every data point in a given
  accession. The filing's own current period is always the most recent
  (max `end`) within its accession.
- Compute Gross Profit = Revenue - Cost of Sales when both present for
  the same filing/period; leave blank if either is missing.
- Emit one normalized record per (accession number, form type) with:
  company, ticker, CIK, form, fiscal year, fiscal period, period end
  date, filed date, Revenue, Cost of Sales, Gross Profit. Filers with no
  matching tag at all still get a row per filing with Revenue/Cost of
  Sales/Gross Profit left blank (resolved decision #5 in PLAN.md).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from html import unescape
import re

from sec_financials.edgar_client import EdgarClient

COMPANYFACTS_URL_TEMPLATE = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
SUBMISSIONS_URL_TEMPLATE = "https://data.sec.gov/submissions/CIK{cik}.json"

REVENUE_TAG_PRIORITY = [
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "Revenues",
    "SalesRevenueNet",
]

COST_OF_SALES_TAG_PRIORITY = [
    "CostOfRevenue",
    "CostOfGoodsAndServicesSold",
    "CostOfGoodsSold",
    "CostOfServices",
]

# Duration bands, in days, keyed by form type.
ANNUAL_DURATION_DAYS = (350, 380)
QUARTERLY_DURATION_DAYS = (80, 100)

DURATION_BANDS_BY_FORM = {
    "10-K": ANNUAL_DURATION_DAYS,
    "10-Q": QUARTERLY_DURATION_DAYS,
}

# -- Milestone 14.1: YTD-duration fallback bands, keyed by fiscal period --
#
# Some filers (confirmed live: GOOGL, AAPL) tag certain DURATION-kind
# concepts -- overwhelmingly Cash Flow Statement concepts, but the
# mechanism itself is concept-agnostic -- with ONLY a cumulative
# year-to-date duration fact for Q2/Q3 10-Qs, no discrete ~90-day fact at
# all for that (concept, accession). `QUARTERLY_DURATION_DAYS` correctly
# filters these out (they are correctly NOT ~90 days), so nothing survives
# for that concept/quarter under the existing discrete-only logic -- not a
# bug in the duration-band filter, a real data-coverage gap. This table
# gives the fallback path (`_best_value_by_accession_for_tag_with_ytd_fallback`)
# the duration band to accept a YTD-shaped fact instead, keyed by the
# datapoint's OWN fiscal period (`fp`), not the accession's form alone --
# a Q2 10-Q accession's comparative prior-period entries share the same
# form but a different fp/duration, so fp is what actually disambiguates
# which YTD band applies.
#
# Q1 needs no entry: a fiscal year's Q1 YTD figure and Q1-alone are
# numerically identical (the fiscal year has only just started), so Q1
# never needs this fallback -- confirmed live against GOOGL's own Q1 2024
# accession (discrete 90-day fact already present).
YTD_DURATION_BANDS_BY_FISCAL_PERIOD = {
    "Q2": (170, 190),
    "Q3": (260, 280),
}

# -- Milestone 7.1: concept classification table -----------------------------
#
# Single source of truth for every direct-tag-lookup concept: its
# tag-priority fallback list, its "kind" (duration vs. instant -- see
# Milestone 7.2 for why these need different grouping code), its XBRL
# namespace ("us-gaap" or "dei"), and its unit key ("USD", "USD/shares", or
# "shares"). Concepts not listed here (Cost of Revenue/Gross Profit/Free
# Cash Flow) are derived, not direct-tag lookups -- see Milestone 7.3.
#
# This is a starting fallback set, not closed -- per PLAN.md, expect to
# discover and add more fallback tags per concept as real companies are
# tested against it (document additions in EXECUTION_LOG.md when found).

DURATION = "duration"
INSTANT = "instant"
# Milestone 13.2 (Capability 1): a new ConceptSpec.kind for concepts whose
# value is the SUM of multiple tags (each present/absent independently per
# filing), rather than a fallback pick between them. See ConceptSpec's
# docstring and `_sum_value_by_accession` below for the exact combination
# rule.
SUM = "sum"

UNIT_USD = "USD"
UNIT_USD_PER_SHARE = "USD/shares"
UNIT_SHARES = "shares"


@dataclass(frozen=True)
class ConceptSpec:
    """Classification metadata for one direct-tag-lookup (or multi-tag-sum)
    concept.

    `tag_priority` means "tags to fall back across, first-present-wins"
    for every DURATION/INSTANT concept, exactly as before. For a SUM-kind
    concept, `tag_priority`'s *meaning changes*: it holds the fallback
    tag(s) to use only when every summand in `sum_tags` is absent for a
    given filing (see Milestone 13.2) -- do NOT read `tag_priority` as
    "tags to sum" for a SUM-kind concept; the tags to actually sum live in
    `sum_tags`.

    `sum_tags` is `None` for every existing/non-SUM concept and is only
    populated for `kind == SUM` concepts, where it lists the tags that get
    independently extracted (via the existing DURATION duration-merge
    machinery, unchanged) and then summed per (accn, form), treating a
    missing individual summand as 0 unless *every* summand is missing for
    that filing, in which case the concept falls back to `tag_priority`'s
    tag(s) (and is blank only if that also has no data) -- see
    `_sum_value_by_accession`.
    """

    key: str  # FilingRecord field name / internal key
    tag_priority: list[str]
    kind: str  # DURATION, INSTANT, or SUM
    namespace: str  # "us-gaap" or "dei"
    unit: str  # UNIT_USD, UNIT_USD_PER_SHARE, or UNIT_SHARES
    sum_tags: list[str] | None = None  # only for kind == SUM; None otherwise


CONCEPT_TABLE: list[ConceptSpec] = [
    ConceptSpec("revenue", REVENUE_TAG_PRIORITY, DURATION, "us-gaap", UNIT_USD),
    ConceptSpec("cost_of_sales", COST_OF_SALES_TAG_PRIORITY, DURATION, "us-gaap", UNIT_USD),
    ConceptSpec("gross_profit", ["GrossProfit"], DURATION, "us-gaap", UNIT_USD),
    ConceptSpec("operating_income", ["OperatingIncomeLoss"], DURATION, "us-gaap", UNIT_USD),
    ConceptSpec("net_income", ["NetIncomeLoss"], DURATION, "us-gaap", UNIT_USD),
    ConceptSpec("cash", ["CashAndCashEquivalentsAtCarryingValue"], INSTANT, "us-gaap", UNIT_USD),
    ConceptSpec("total_assets", ["Assets"], INSTANT, "us-gaap", UNIT_USD),
    ConceptSpec("total_liabilities", ["Liabilities"], INSTANT, "us-gaap", UNIT_USD),
    ConceptSpec("stockholders_equity", ["StockholdersEquity"], INSTANT, "us-gaap", UNIT_USD),
    ConceptSpec(
        "operating_cash_flow",
        ["NetCashProvidedByUsedInOperatingActivities"],
        DURATION,
        "us-gaap",
        UNIT_USD,
    ),
    ConceptSpec(
        "capital_expenditures",
        ["PaymentsToAcquireProductiveAssets", "PaymentsToAcquirePropertyPlantAndEquipment"],
        DURATION,
        "us-gaap",
        UNIT_USD,
    ),
    # Milestone (Long Term Debt fallback fix): `LongTermDebt` alone leaves
    # many GOOGL quarters blank -- confirmed live, GOOGL has ZERO
    # `us-gaap:LongTermDebt` facts across its entire filing history.
    # `LongTermDebtAndCapitalLeaseObligations` is the tag Alphabet actually
    # uses instead (a legitimate alternate total that bundles long-term
    # debt with finance/capital lease obligations); confirmed live it
    # covers 39 of GOOGL's 44 historical 10-Q instant dates. Apple is
    # unaffected: it has `LongTermDebt` present for every quarter (direct
    # tag always wins, first-present-wins) and zero
    # `LongTermDebtAndCapitalLeaseObligations` facts at all, so this
    # fallback is a no-op for companies that already report cleanly. The
    # remaining gap (GOOGL 2025-Q2 onward, which drops both tags and only
    # reports the current/noncurrent split) is covered by the separate
    # current+noncurrent derived fallback below -- see Milestone 7.3-style
    # circularity-safety comment near the emit loop.
    ConceptSpec(
        "long_term_debt",
        ["LongTermDebt", "LongTermDebtAndCapitalLeaseObligations"],
        INSTANT,
        "us-gaap",
        UNIT_USD,
    ),
    ConceptSpec(
        "research_and_development",
        ["ResearchAndDevelopmentExpense"],
        DURATION,
        "us-gaap",
        UNIT_USD,
    ),
    ConceptSpec(
        "shares_outstanding",
        ["CommonStockSharesOutstanding"],
        INSTANT,
        "us-gaap",
        UNIT_SHARES,
    ),
    ConceptSpec("eps_basic", ["EarningsPerShareBasic"], DURATION, "us-gaap", UNIT_USD_PER_SHARE),
    ConceptSpec(
        "eps_diluted", ["EarningsPerShareDiluted"], DURATION, "us-gaap", UNIT_USD_PER_SHARE
    ),
    ConceptSpec("income_tax_expense", ["IncomeTaxExpenseBenefit"], DURATION, "us-gaap", UNIT_USD),
    ConceptSpec(
        "pre_tax_income",
        [
            "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",
            "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest"
        ],
        DURATION,
        "us-gaap",
        UNIT_USD,
    ),
    ConceptSpec(
        "dei_common_stock_shares_outstanding",
        ["EntityCommonStockSharesOutstanding"],
        INSTANT,
        "dei",
        UNIT_SHARES,
    ),
    ConceptSpec(
        "marketable_securities_current",
        ["MarketableSecuritiesCurrent"],
        INSTANT,
        "us-gaap",
        UNIT_USD,
    ),
    ConceptSpec(
        "operating_lease_liabilities", ["OperatingLeaseLiability"], INSTANT, "us-gaap", UNIT_USD
    ),
    ConceptSpec(
        "long_term_debt_noncurrent", ["LongTermDebtNoncurrent"], INSTANT, "us-gaap", UNIT_USD
    ),
    ConceptSpec(
        "long_term_debt_current", ["LongTermDebtCurrent"], INSTANT, "us-gaap", UNIT_USD
    ),
    ConceptSpec(
        "short_term_debt", ["ShortTermBorrowings"], INSTANT, "us-gaap", UNIT_USD
    ),
    ConceptSpec(
        "finance_lease_liabilities", ["FinanceLeaseLiability"], INSTANT, "us-gaap", UNIT_USD
    ),
    ConceptSpec(
        "rsu_count",
        [
            "ShareBasedCompensationArrangementByShareBasedPaymentAwardEquityInstrumentsOtherThanOptionsNonvestedNumber"
        ],
        INSTANT,
        "us-gaap",
        UNIT_SHARES,
    ),
    # -- Milestone 13.1: ~45 new direct-tag-lookup concepts, per PLAN.md's
    # Milestone 13 brief (tag names sourced from the user-supplied
    # AMZN_XBRL_Tag_Map.xlsx, categorized by the orchestrating agent, with
    # the corrections found via live SEC frames-API verification during
    # this milestone's planning/implementation -- see EXECUTION_LOG.md).
    #
    # -- Income Statement --
    ConceptSpec("operating_expenses", ["CostsAndExpenses"], DURATION, "us-gaap", UNIT_USD),
    ConceptSpec("fulfillment", ["FulfillmentExpense"], DURATION, "amzn", UNIT_USD),
    ConceptSpec(
        "technology_and_infrastructure",
        ["TechnologyAndContentExpense"],
        DURATION,
        "amzn",
        UNIT_USD,
    ),
    # Milestone 15.2: fallback tag added -- confirmed live against GOOGL
    # that `MarketingExpense` 404s (zero facts) while `SellingAndMarketingExpense`
    # has 142 real facts. Plain tag-priority-list extension, no new
    # mechanism; a no-op for companies (e.g. Apple) that already report
    # under the primary tag.
    ConceptSpec(
        "sales_and_marketing",
        ["MarketingExpense", "SellingAndMarketingExpense"],
        DURATION,
        "us-gaap",
        UNIT_USD,
    ),
    ConceptSpec(
        "general_and_administrative",
        ["GeneralAndAdministrativeExpense"],
        DURATION,
        "us-gaap",
        UNIT_USD,
    ),
    ConceptSpec(
        "other_operating_expense_income",
        ["OtherOperatingIncomeExpenseNet"],
        DURATION,
        "us-gaap",
        UNIT_USD,
    ),
    # Milestone 15.1: fallback tags added -- confirmed live against GOOGL
    # (CIK 0001652044) that the original single tag 404s (zero facts) for
    # Alphabet, while the added fallback tag has real, populated data
    # (142/116/142 facts respectively). Apple is unaffected: it already has
    # data under the existing first tag for all three concepts, and the
    # first-tag-present-wins per-accession merge (`_best_value_by_accession`,
    # unchanged) means an added lower-priority fallback tag is a no-op for
    # any filing the primary tag already covers.
    ConceptSpec(
        "interest_income",
        ["InvestmentIncomeInterest", "InterestIncomeNonOperating", "InterestIncomeOther"],
        DURATION,
        "us-gaap",
        UNIT_USD,
    ),
    ConceptSpec(
        "interest_expense",
        ["InterestExpenseNonoperating", "InterestExpenseNonOperating", "InterestExpense"],
        DURATION,
        "us-gaap",
        UNIT_USD,
    ),
    ConceptSpec(
        "other_income_expense_net",
        [
            "OtherNonoperatingIncomeExpense",
            "OtherIncomeExpenseNet",
            "NonoperatingIncomeExpense",
        ],
        DURATION,
        "us-gaap",
        UNIT_USD,
    ),
    ConceptSpec(
        "diluted_shares",
        ["WeightedAverageNumberOfDilutedSharesOutstanding"],
        DURATION,
        "us-gaap",
        UNIT_SHARES,
    ),
    # -- Balance Sheet --
    ConceptSpec(
        "accounts_receivable", ["AccountsReceivableNetCurrent"], INSTANT, "us-gaap", UNIT_USD
    ),
    ConceptSpec("inventory", ["InventoryNet"], INSTANT, "us-gaap", UNIT_USD),
    ConceptSpec("other_current_assets", ["OtherCurrentAssets"], INSTANT, "us-gaap", UNIT_USD),
    ConceptSpec("total_current_assets", ["AssetsCurrent"], INSTANT, "us-gaap", UNIT_USD),
    ConceptSpec(
        "property_and_equipment_net",
        [
            "PropertyPlantAndEquipmentAndFinanceLeaseRightOfUseAssetAfterAccumulatedDepreciationAndAmortization",
            "PropertyPlantAndEquipmentNet",
        ],
        INSTANT,
        "us-gaap",
        UNIT_USD,
    ),
    ConceptSpec(
        "operating_lease_rou_asset",
        ["OperatingLeaseRightOfUseAsset"],
        INSTANT,
        "us-gaap",
        UNIT_USD,
    ),
    ConceptSpec("goodwill", ["Goodwill"], INSTANT, "us-gaap", UNIT_USD),
    ConceptSpec(
        "other_assets_noncurrent", ["OtherAssetsNoncurrent"], INSTANT, "us-gaap", UNIT_USD
    ),
    ConceptSpec("accounts_payable", ["AccountsPayableCurrent"], INSTANT, "us-gaap", UNIT_USD),
    ConceptSpec(
        "accrued_expenses_and_other", ["AccruedLiabilitiesCurrent"], INSTANT, "us-gaap", UNIT_USD
    ),
    ConceptSpec(
        "unearned_revenue", ["ContractWithCustomerLiabilityCurrent"], INSTANT, "us-gaap", UNIT_USD
    ),
    ConceptSpec(
        "current_finance_lease_liabilities",
        ["FinanceLeaseLiabilityCurrent"],
        INSTANT,
        "us-gaap",
        UNIT_USD,
    ),
    ConceptSpec(
        "current_operating_lease_liabilities",
        ["OperatingLeaseLiabilityCurrent"],
        INSTANT,
        "us-gaap",
        UNIT_USD,
    ),
    ConceptSpec(
        "current_financing_obligations",
        ["FinancingObligationsCurrent"],
        INSTANT,
        "amzn",
        UNIT_USD,
    ),
    ConceptSpec(
        "long_term_finance_lease_liabilities",
        ["FinanceLeaseLiabilityNoncurrent"],
        INSTANT,
        "us-gaap",
        UNIT_USD,
    ),
    ConceptSpec(
        "long_term_operating_lease_liabilities",
        ["OperatingLeaseLiabilityNoncurrent"],
        INSTANT,
        "us-gaap",
        UNIT_USD,
    ),
    ConceptSpec(
        "other_long_term_liabilities",
        ["OtherLiabilitiesNoncurrent"],
        INSTANT,
        "us-gaap",
        UNIT_USD,
    ),
    ConceptSpec("total_current_liabilities", ["LiabilitiesCurrent"], INSTANT, "us-gaap", UNIT_USD),
    ConceptSpec(
        "total_liabilities_and_stockholders_equity",
        ["LiabilitiesAndStockholdersEquity"],
        INSTANT,
        "us-gaap",
        UNIT_USD,
    ),
    # Ending Cash (Capability 2's plain new INSTANT concept -- "Beginning
    # Cash" itself is NOT a CONCEPT_TABLE entry / FilingRecord field, see
    # xlsx_writer.py's cross-period derivation, Milestone 13.3).
    ConceptSpec(
        "ending_cash",
        ["CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"],
        INSTANT,
        "us-gaap",
        UNIT_USD,
    ),
    # -- Cash Flow --
    # Milestone 15.3: a SUM-kind conversion was DRAFTED and live-verified to
    # REGRESS already-shipped companies (AAPL, AMZN) -- reverted, not
    # shipped. See PLAN.md's Milestone 15.3 section and EXECUTION_LOG.md
    # for the full before/after evidence: both AAPL and AMZN have real
    # accessions where `AmortizationOfIntangibleAssets` is present but
    # `Depreciation` is absent for that same accession, so the SUM
    # mechanism's "missing summand = 0" rule would silently replace the
    # correct combined-tag value with just the present summand's (much
    # smaller) value instead of falling back to the combined tag -- a
    # wrong-value regression, not a blank-vs-populated coverage gap.
    # Separately, SUM-kind concepts are deliberately excluded from the
    # Milestone 14.1 YTD-duration fallback, but AAPL's Q2/Q3 D&A currently
    # depends on exactly that fallback (AAPL reports
    # `DepreciationDepletionAndAmortization` in YTD-cumulative-only shape
    # for Q2/Q3), so the conversion would additionally blank out AAPL's
    # Q2/Q3 D&A that renders correctly today. Concept therefore remains the
    # original plain single-tag DURATION ConceptSpec, unchanged, pending a
    # decision from the user on how to proceed (see report).
    ConceptSpec(
        "depreciation_and_amortization",
        ["DepreciationDepletionAndAmortization"],
        DURATION,
        "us-gaap",
        UNIT_USD,
    ),
    ConceptSpec(
        "stock_based_compensation", ["ShareBasedCompensation"], DURATION, "us-gaap", UNIT_USD
    ),
    # Milestone 15.5 (resolved by user): `DeferredIncomeTaxExpenseBenefit`
    # stays PRIMARY (unchanged, 38 facts for GOOGL) -- the user chose
    # definitional precision (the narrower "deferred tax expense/benefit"
    # GAAP label) over `DeferredIncomeTaxesAndTaxCredits`'s denser coverage
    # (98 facts), despite live verification finding the two tags are NOT
    # interchangeable fallbacks: 36 of the 38 primary-tag periods overlap
    # with the secondary tag on the exact same (accession, form, start,
    # end) key, and every one of those 36 periods has a DIFFERENT value
    # between the two tags (e.g. FY2013: -493M vs. -437M). Adding the
    # secondary tag as a fallback therefore only ever fills periods where
    # the primary has NO data at all (first-tag-present-wins, unchanged
    # mechanism); it never overrides a period where the primary already
    # has a value, so the two tags' disagreement in overlapping periods is
    # not silently mixed.
    ConceptSpec(
        "deferred_taxes",
        ["DeferredIncomeTaxExpenseBenefit", "DeferredIncomeTaxesAndTaxCredits"],
        DURATION,
        "us-gaap",
        UNIT_USD,
    ),
    ConceptSpec(
        "other_non_cash_items", ["OtherNoncashIncomeExpense"], DURATION, "us-gaap", UNIT_USD
    ),
    ConceptSpec(
        "unearned_revenue_cf_change",
        ["IncreaseDecreaseInContractWithCustomerLiability"],
        DURATION,
        "us-gaap",
        UNIT_USD,
    ),
    ConceptSpec(
        "inventory_cf_change", ["IncreaseDecreaseInInventories"], DURATION, "us-gaap", UNIT_USD
    ),
    ConceptSpec(
        "accounts_receivable_cf_change",
        [
            "IncreaseDecreaseInAccountsReceivableAndOtherOperatingAssets",
            "IncreaseDecreaseInReceivables",
        ],
        DURATION,
        "us-gaap",
        UNIT_USD,
    ),
    ConceptSpec(
        "other_assets_cf_change",
        ["IncreaseDecreaseInOtherNoncurrentAssets", "IncreaseDecreaseInOtherOperatingAssets"],
        DURATION,
        "us-gaap",
        UNIT_USD,
    ),
    ConceptSpec(
        "accounts_payable_cf_change",
        ["IncreaseDecreaseInAccountsPayable"],
        DURATION,
        "us-gaap",
        UNIT_USD,
    ),
    ConceptSpec(
        "accrued_expenses_cf_change",
        [
            "IncreaseDecreaseInAccruedLiabilitiesAndOtherOperatingLiabilities",
            "IncreaseDecreaseInAccruedLiabilities",
        ],
        DURATION,
        "us-gaap",
        UNIT_USD,
    ),
    ConceptSpec(
        "acquisitions",
        ["PaymentsToAcquireBusinessesNetOfCashAcquired"],
        DURATION,
        "us-gaap",
        UNIT_USD,
    ),
    ConceptSpec(
        "proceeds_from_ppe_sales_and_incentives",
        ["ProceedsFromPropertyPlantAndEquipmentSalesAndIncentives"],
        DURATION,
        "amzn",
        UNIT_USD,
    ),
    ConceptSpec(
        "net_cash_used_in_investing_activities",
        ["NetCashProvidedByUsedInInvestingActivities"],
        DURATION,
        "us-gaap",
        UNIT_USD,
    ),
    # Purchases of Marketable Securities: primary tag verified live against
    # the SEC frames API (591 companies, CY2023) -- confirmed real and
    # populated, matching the brief's tag verbatim. Fallback tag CORRECTED
    # during implementation (same discipline as the sum-tags finding):
    # the brief's literal fallback string
    # `PaymentsToAcquireAvailableForSaleSecurities` returns a 404 (no
    # populated frame) at every year checked; the real, populated element
    # is `PaymentsToAcquireAvailableForSaleSecuritiesDebt` (906 companies,
    # CY2023) -- a naming-convention mismatch, not a sign the fallback
    # concept itself is wrong. Uses the verified real tag name, not the
    # brief's literal string.
    ConceptSpec(
        "purchases_of_marketable_securities",
        [
            "PaymentsToAcquireMarketableSecurities",
            "PaymentsToAcquireAvailableForSaleSecuritiesDebt",
        ],
        DURATION,
        "us-gaap",
        UNIT_USD,
    ),
    ConceptSpec(
        "proceeds_from_short_term_debt_and_other",
        [
            "ProceedsFromShortTermDebt",
            "ProceedsFromShortTermDebtAndOtherBorrowings",
            "ProceedsFromShortTermBorrowings",
        ],
        DURATION,
        "us-gaap",
        UNIT_USD,
    ),
    ConceptSpec(
        "repayments_of_short_term_debt_and_other",
        [
            "RepaymentsOfShortTermDebt",
            "RepaymentsOfShortTermDebtAndOtherBorrowings",
            "RepaymentsOfShortTermBorrowings",
        ],
        DURATION,
        "us-gaap",
        UNIT_USD,
    ),
    ConceptSpec(
        "proceeds_from_long_term_debt",
        ["ProceedsFromIssuanceOfLongTermDebt"],
        DURATION,
        "us-gaap",
        UNIT_USD,
    ),
    ConceptSpec(
        "repayments_of_long_term_debt",
        ["RepaymentsOfLongTermDebt"],
        DURATION,
        "us-gaap",
        UNIT_USD,
    ),
    ConceptSpec(
        "finance_lease_principal_payments",
        ["FinanceLeasePrincipalPayments", "PaymentsOfFinanceLeaseObligations"],
        DURATION,
        "us-gaap",
        UNIT_USD,
    ),
    ConceptSpec(
        "financing_obligation_principal_payments",
        ["PaymentsOfFinancingObligations"],
        DURATION,
        "us-gaap",
        UNIT_USD,
    ),
    ConceptSpec(
        "amazon_financing_obligation_principal_payments",
        ["RepaymentsOfLongTermFinancingObligations"],
        DURATION,
        "amzn",
        UNIT_USD,
    ),
    ConceptSpec(
        "amazon_acquisitions",
        ["PaymentsToAcquireBusinessesNetOfCashAcquiredAndOther"],
        DURATION,
        "amzn",
        UNIT_USD,
    ),
    ConceptSpec(
        "net_cash_used_in_financing_activities",
        ["NetCashProvidedByUsedInFinancingActivities"],
        DURATION,
        "us-gaap",
        UNIT_USD,
    ),
    ConceptSpec(
        "other_financing_activities",
        ["OtherFinancingActivities"],
        DURATION,
        "us-gaap",
        UNIT_USD,
    ),
    ConceptSpec(
        "net_change_in_cash",
        [
            "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalentsPeriodIncreaseDecreaseIncludingExchangeRateEffect"
        ],
        DURATION,
        "us-gaap",
        UNIT_USD,
    ),
    # -- Milestone 13.2 (Capability 1): multi-tag-sum concept. Sums the two
    # verified real split tags per (accn, form) whenever either/both are
    # present (missing individual summand treated as 0); falls back to the
    # combined tag (`tag_priority` below) only when BOTH split tags are
    # absent for that filing; blank only if all three are absent. See
    # `_sum_value_by_accession`. The brief's original two tag names
    # (`ProceedsFromSaleOfMarketableSecurities` /
    # `ProceedsFromMaturitiesOfMarketableSecurities`) were checked live
    # against the SEC frames API across CY2020-CY2024 and found to have
    # ZERO populated entries in any year for either tag -- they do not
    # exist as real, populated taxonomy elements. The corrected, verified
    # real tag names below are used instead (see EXECUTION_LOG.md for the
    # frames-API verification).
    # Milestone 15.4: added a SECOND combined-tag fallback,
    # `ProceedsFromSaleAndMaturityOfMarketableSecurities`, confirmed live
    # against GOOGL to have 100 real facts (GOOGL has zero facts under
    # either split summand tag or the original combined fallback tag).
    # `tag_priority` is already a list consumed by `_best_value_by_accession`
    # inside `_sum_value_by_accession`'s neither-summand-present fallback
    # path (first-tag-present-wins, per-accession) -- no code change to
    # `_sum_value_by_accession` itself needed, purely a data/config change.
    ConceptSpec(
        "sales_maturities_of_marketable_securities",
        [
            "ProceedsFromSaleMaturityAndCollectionsOfInvestments",
            "ProceedsFromSaleAndMaturityOfMarketableSecurities",
        ],  # neither-split-summand-present fallback priority list
        SUM,
        "us-gaap",
        UNIT_USD,
        sum_tags=[
            "ProceedsFromSaleOfAvailableForSaleSecuritiesDebt",
            "ProceedsFromMaturitiesPrepaymentsAndCallsOfAvailableForSaleSecurities",
        ],
    ),
]

# Convenience lookup by key, e.g. CONCEPT_BY_KEY["revenue"].
CONCEPT_BY_KEY: dict[str, ConceptSpec] = {spec.key: spec for spec in CONCEPT_TABLE}


class FactsParserError(Exception):
    """Raised when companyfacts data cannot be fetched or parsed."""


@dataclass(frozen=True)
class FilingRecord:
    """One normalized row: a single (accession number, form type) filing.

    Milestone 7.3 extends this with one field per new concept (~22 new
    fields), matching CONCEPT_TABLE's keys, plus the three derived metrics
    (cost_of_sales/gross_profit already existed in v1; free_cash_flow is
    new). All new fields default to None so existing v1 call sites/tests
    constructing a FilingRecord positionally-by-keyword continue to work
    unchanged.
    """

    company: str
    ticker: str
    cik: str
    form: str
    fiscal_year: int | str | None
    fiscal_period: str | None
    period_end: str | None
    filed: str | None
    revenue: int | float | None
    cost_of_sales: int | float | None
    gross_profit: int | float | None
    accession: str | None = None
    operating_income: int | float | None = None
    net_income: int | float | None = None
    cash: int | float | None = None
    total_assets: int | float | None = None
    total_liabilities: int | float | None = None
    stockholders_equity: int | float | None = None
    operating_cash_flow: int | float | None = None
    capital_expenditures: int | float | None = None
    free_cash_flow: int | float | None = None
    long_term_debt: int | float | None = None
    research_and_development: int | float | None = None
    shares_outstanding: int | float | None = None
    eps_basic: int | float | None = None
    eps_diluted: int | float | None = None
    income_tax_expense: int | float | None = None
    pre_tax_income: int | float | None = None
    dei_common_stock_shares_outstanding: int | float | None = None
    marketable_securities_current: int | float | None = None
    operating_lease_liabilities: int | float | None = None
    long_term_debt_noncurrent: int | float | None = None
    long_term_debt_current: int | float | None = None
    short_term_debt: int | float | None = None
    finance_lease_liabilities: int | float | None = None
    rsu_count: int | float | None = None
    # -- Milestone 13.1/13.2: ~46 new fields (45 direct-tag concepts +
    # Ending Cash + the Sales/Maturities-of-Marketable-Securities SUM
    # concept), all defaulting to None per the same v1-compatibility
    # pattern as every prior extension. "Beginning Cash" has NO field here
    # by design (Capability 2) -- it is an xlsx-render-time-only formula,
    # never a per-filing fact; see xlsx_writer.py.
    #
    # Income Statement
    operating_expenses: int | float | None = None
    fulfillment: int | float | None = None
    technology_and_infrastructure: int | float | None = None
    sales_and_marketing: int | float | None = None
    general_and_administrative: int | float | None = None
    other_operating_expense_income: int | float | None = None
    interest_income: int | float | None = None
    interest_expense: int | float | None = None
    other_income_expense_net: int | float | None = None
    diluted_shares: int | float | None = None
    # Balance Sheet
    accounts_receivable: int | float | None = None
    inventory: int | float | None = None
    other_current_assets: int | float | None = None
    total_current_assets: int | float | None = None
    property_and_equipment_net: int | float | None = None
    operating_lease_rou_asset: int | float | None = None
    goodwill: int | float | None = None
    other_assets_noncurrent: int | float | None = None
    accounts_payable: int | float | None = None
    accrued_expenses_and_other: int | float | None = None
    unearned_revenue: int | float | None = None
    current_finance_lease_liabilities: int | float | None = None
    current_operating_lease_liabilities: int | float | None = None
    current_financing_obligations: int | float | None = None
    long_term_finance_lease_liabilities: int | float | None = None
    long_term_operating_lease_liabilities: int | float | None = None
    other_long_term_liabilities: int | float | None = None
    total_current_liabilities: int | float | None = None
    total_liabilities_and_stockholders_equity: int | float | None = None
    ending_cash: int | float | None = None
    # Cash Flow
    depreciation_and_amortization: int | float | None = None
    stock_based_compensation: int | float | None = None
    deferred_taxes: int | float | None = None
    other_non_cash_items: int | float | None = None
    unearned_revenue_cf_change: int | float | None = None
    inventory_cf_change: int | float | None = None
    accounts_receivable_cf_change: int | float | None = None
    other_assets_cf_change: int | float | None = None
    accounts_payable_cf_change: int | float | None = None
    accrued_expenses_cf_change: int | float | None = None
    acquisitions: int | float | None = None
    proceeds_from_ppe_sales_and_incentives: int | float | None = None
    net_cash_used_in_investing_activities: int | float | None = None
    purchases_of_marketable_securities: int | float | None = None
    sales_maturities_of_marketable_securities: int | float | None = None
    proceeds_from_short_term_debt_and_other: int | float | None = None
    repayments_of_short_term_debt_and_other: int | float | None = None
    proceeds_from_long_term_debt: int | float | None = None
    repayments_of_long_term_debt: int | float | None = None
    finance_lease_principal_payments: int | float | None = None
    financing_obligation_principal_payments: int | float | None = None
    net_cash_used_in_financing_activities: int | float | None = None
    other_financing_activities: int | float | None = None
    net_change_in_cash: int | float | None = None
    # -- Milestone 14.1: provenance for the YTD-duration fallback.
    #
    # `None` (the default) for the overwhelming majority of records --
    # every filing where every concept resolved via the existing discrete
    # ~90-day (or ~350-380-day) fact, including 100% of AMZN's records and
    # 100% of every Income Statement concept's records (confirmed live).
    # Populated with {concept_key: raw_ytd_value} only for concepts that
    # hit the fallback path for this specific filing -- the raw YTD
    # number, NOT yet subtracted. Downstream derivation
    # (`derive_ytd_fallback_values`, Milestone 14.2) reads this dict to
    # compute the actual Q2/Q3 value; this field itself is never read by
    # `csv_writer.py`/`xlsx_writer.py` directly.
    ytd_fallback: dict[str, float] | None = None


def _parse_date(value: str):
    from datetime import date

    year, month, day = (int(part) for part in value.split("-"))
    return date(year, month, day)


def _iter_datapoints(facts: dict, namespace: str, tag: str, unit: str):
    """Yield the data points for a given tag under `namespace` (e.g.
    "us-gaap" or "dei"), restricted to the given `unit` key (e.g. "USD",
    "USD/shares", "shares").

    Generalized (Milestone 7.1) from the original `_iter_usd_datapoints`,
    which hardcoded namespace="us-gaap" and unit="USD". Existing
    Revenue/Cost of Sales callers are unaffected -- they simply pass those
    same values explicitly now.
    """
    tag_data = facts.get(namespace, {}).get(tag)
    if not tag_data:
        return
    for unit_name, datapoints in tag_data.get("units", {}).items():
        if unit_name != unit:
            continue
        yield from datapoints


def _iter_usd_datapoints(facts: dict, tag: str):
    """Backwards-compatible wrapper: us-gaap + USD, as in v1."""
    yield from _iter_datapoints(facts, "us-gaap", tag, "USD")


def _best_value_by_accession_for_tag(
    facts: dict,
    tag: str,
    forms: list[str],
    namespace: str = "us-gaap",
    unit: str = "USD",
) -> dict[tuple[str, str], dict]:
    """Return {(accn, form): best_datapoint} for a single duration tag.

    "Best" = after filtering to the duration band matching the
    datapoint's own form type, the entry with the maximum `end` date
    within that (accession, form) group.
    """
    groups: dict[tuple[str, str], list[dict]] = {}
    for dp in _iter_datapoints(facts, namespace, tag, unit):
        form = dp.get("form")
        if form not in forms:
            continue
        band = DURATION_BANDS_BY_FORM.get(form)
        if band is None:
            continue
        start = dp.get("start")
        end = dp.get("end")
        if not start or not end:
            continue
        try:
            duration_days = (_parse_date(end) - _parse_date(start)).days
        except ValueError:
            continue
        low, high = band
        if not (low <= duration_days <= high):
            continue
        accn = dp.get("accn")
        if not accn:
            continue
        groups.setdefault((accn, form), []).append(dp)

    best: dict[tuple[str, str], dict] = {}
    for key, datapoints in groups.items():
        best[key] = max(datapoints, key=lambda d: d["end"])
    return best


def _best_value_by_accession_for_tag_with_ytd_fallback(
    facts: dict,
    tag: str,
    forms: list[str],
    namespace: str = "us-gaap",
    unit: str = "USD",
) -> dict[tuple[str, str], dict]:
    """Return {(accn, form): best_datapoint} for a single duration tag,
    same as `_best_value_by_accession_for_tag`, but additionally falling
    back to a YTD-shaped duration fact for any accession that has NO
    surviving discrete-band entry (Milestone 14.1).

    Runs the existing discrete-band logic first, completely unchanged --
    every accession that already has a discrete ~90-day (or ~350-380-day,
    for 10-K) fact takes the exact same code path as today and is
    returned exactly as `_best_value_by_accession_for_tag` would return
    it. Only for accessions with NO discrete-band survivor does this
    function additionally search that same accession's datapoints for a
    YTD-shaped entry, using `YTD_DURATION_BANDS_BY_FISCAL_PERIOD` keyed by
    each datapoint's OWN `fp` (not the accession's form alone -- a Q2
    10-Q's comparative prior-period entries share the same form but a
    different fp/duration), selected via the same max-`end`-date rule
    already proven correct for disambiguating comparative entries within
    an accession.

    A YTD-sourced datapoint returned here is the RAW YTD figure, not yet
    subtracted -- the subtraction (`YTD - prior quarter(s)`) happens
    downstream, once, in `derive_ytd_fallback_values` (Milestone 14.2).
    The returned dict is tagged with `"_ytd_fallback": True` so
    `parse_company_facts` can tell a YTD-sourced datapoint apart from a
    discrete one without re-deriving the duration-days check a second
    time.
    """
    discrete = _best_value_by_accession_for_tag(facts, tag, forms, namespace, unit)

    # Group ALL datapoints (regardless of duration) by (accn, form), so we
    # can search accessions with no discrete survivor for a YTD-shaped
    # entry instead.
    all_by_accn_form: dict[tuple[str, str], list[dict]] = {}
    for dp in _iter_datapoints(facts, namespace, tag, unit):
        form = dp.get("form")
        if form not in forms:
            continue
        accn = dp.get("accn")
        if not accn:
            continue
        all_by_accn_form.setdefault((accn, form), []).append(dp)

    result: dict[tuple[str, str], dict] = dict(discrete)
    for key, datapoints in all_by_accn_form.items():
        if key in result:
            continue  # already has a discrete fact -- true no-op, skip entirely

        ytd_candidates: list[dict] = []
        for dp in datapoints:
            fp = (dp.get("fp") or "").upper()
            band = YTD_DURATION_BANDS_BY_FISCAL_PERIOD.get(fp)
            if band is None:
                continue
            start = dp.get("start")
            end = dp.get("end")
            if not start or not end:
                continue
            try:
                duration_days = (_parse_date(end) - _parse_date(start)).days
            except ValueError:
                continue
            low, high = band
            if not (low <= duration_days <= high):
                continue
            ytd_candidates.append(dp)

        if not ytd_candidates:
            continue  # nothing discrete, nothing YTD-shaped either -- truly blank

        best_ytd = max(ytd_candidates, key=lambda d: d["end"])
        result[key] = {**best_ytd, "_ytd_fallback": True}

    return result


def _best_value_by_accession(
    facts: dict,
    priority: list[str],
    forms: list[str],
    namespace: str = "us-gaap",
    unit: str = "USD",
    ytd_fallback: bool = False,
) -> dict[tuple[str, str], dict]:
    """Return {(accn, form): best_datapoint}, merged across a tag-priority
    fallback list (duration concepts).

    Fallback is resolved per accession, not once globally per company:
    for each (accession, form) key, the datapoint from the
    highest-priority tag that has data for that specific accession wins.
    A lower-priority tag can fill in accessions/filings the
    higher-priority tag doesn't cover (e.g. a company that switched XBRL
    tags partway through its filing history) without a stale
    higher-priority tag from a different era blanking out coverage
    elsewhere.

    `ytd_fallback` (Milestone 14.1): when True, each tag's per-accession
    lookup goes through `_best_value_by_accession_for_tag_with_ytd_fallback`
    instead of the plain discrete-only `_best_value_by_accession_for_tag`.
    Defaults to False so callers such as SUM-concept extraction and any
    call site not explicitly opted in remain completely unaffected.
    """
    merged: dict[tuple[str, str], dict] = {}
    per_tag_fn = (
        _best_value_by_accession_for_tag_with_ytd_fallback
        if ytd_fallback
        else _best_value_by_accession_for_tag
    )
    # Iterate lowest-priority-first so later (higher-priority) tags
    # overwrite earlier ones when both cover the same (accn, form).
    for tag in reversed(priority):
        if tag not in facts.get(namespace, {}):
            continue
        per_tag = per_tag_fn(facts, tag, forms, namespace, unit)
        merged.update(per_tag)
    return merged


def _best_value_by_accession_for_tag_instant(
    facts: dict,
    tag: str,
    forms: list[str],
    namespace: str = "us-gaap",
    unit: str = "USD",
) -> dict[tuple[str, str], dict]:
    """Return {(accn, form): best_datapoint} for a single instant-concept
    tag (balance-sheet-style facts with no `start` field -- only `end`).

    Parallel to `_best_value_by_accession_for_tag` (duration concepts),
    but structurally different rather than a parameter tweak: there is no
    `start`/duration to filter on, so datapoints are grouped by
    (accn, form) directly and the entry with the maximum `end` date wins.
    This is the verified-correct rule for instant concepts too (see
    PLAN.md's plan-optimizer note on Milestone 7 -- confirmed live against
    Apple's Assets/StockholdersEquity/CashAndCashEquivalentsAtCarryingValue
    data, including a 6-entries-in-one-accession StockholdersEquity
    roll-forward case, with zero end-date ties found).
    """
    groups: dict[tuple[str, str], list[dict]] = {}
    for dp in _iter_datapoints(facts, namespace, tag, unit):
        form = dp.get("form")
        if form not in forms:
            continue
        end = dp.get("end")
        if not end:
            continue
        accn = dp.get("accn")
        if not accn:
            continue
        groups.setdefault((accn, form), []).append(dp)

    best: dict[tuple[str, str], dict] = {}
    for key, datapoints in groups.items():
        best[key] = max(datapoints, key=lambda d: d["end"])
    return best


def _best_value_by_accession_instant(
    facts: dict,
    priority: list[str],
    forms: list[str],
    namespace: str = "us-gaap",
    unit: str = "USD",
) -> dict[tuple[str, str], dict]:
    """Return {(accn, form): best_datapoint}, merged across a tag-priority
    fallback list (instant concepts).

    Same lowest-priority-first merge pattern as
    `_best_value_by_accession` (duration concepts) -- only the per-tag
    grouping differs (delegates to `_best_value_by_accession_for_tag_instant`).
    """
    merged: dict[tuple[str, str], dict] = {}
    for tag in reversed(priority):
        if tag not in facts.get(namespace, {}):
            continue
        per_tag = _best_value_by_accession_for_tag_instant(facts, tag, forms, namespace, unit)
        merged.update(per_tag)
    return merged


def _sum_value_by_accession(
    facts: dict,
    spec: ConceptSpec,
    forms: list[str],
) -> dict[tuple[str, str], dict]:
    """Return {(accn, form): synthetic_datapoint} for a SUM-kind concept
    (Milestone 13.2, Capability 1).

    Each tag in `spec.sum_tags` is independently extracted via the
    existing DURATION duration-merge machinery (`_best_value_by_accession`,
    unchanged -- these are all DURATION-kind facts, no new grouping code
    needed for extraction itself), then summed per (accn, form):
    - If either or both summand tags have a value for a given filing, the
      concept's value is the sum of whichever summand(s) are present (a
      missing individual summand is treated as 0, NOT as blank).
    - Only when EVERY summand tag is missing for that filing does the
      concept fall back to `spec.tag_priority` (the combined tag) via the
      existing duration-merge machinery for that tag list.
    - If the fallback also has no data for that filing, the concept is
      blank for that filing (not a fabricated 0) -- same "blank when we
      truly have nothing" convention as every other concept.

    Returns synthetic datapoint dicts (only a "val" key; callers that need
    other datapoint fields, e.g. shared filing metadata, must fall back to
    another concept's real datapoint for that, exactly as
    `parse_company_facts` already does via `source_dp`) so downstream code
    can read `dp["val"]` uniformly regardless of whether a concept is
    DURATION/INSTANT/SUM.

    Milestone 14 deliberately keeps generic YTD fallback out of SUM
    concepts. A filing can report one summand as a discrete quarter and
    another as YTD; summing those would mix bases, and a single raw total
    cannot be safely derived later without per-summand provenance. Leave
    such a filing blank until a dedicated, basis-aware SUM derivation is
    designed. Ordinary DURATION concepts remain eligible for the fallback.
    """
    sum_tags = spec.sum_tags or []
    per_tag_maps: dict[str, dict[tuple[str, str], dict]] = {
        tag: _best_value_by_accession(facts, [tag], forms, spec.namespace, spec.unit)
        for tag in sum_tags
    }

    fallback_map = _best_value_by_accession(
        facts, spec.tag_priority, forms, spec.namespace, spec.unit
    )

    all_keys: set[tuple[str, str]] = set(fallback_map)
    for per_tag in per_tag_maps.values():
        all_keys |= set(per_tag)

    result: dict[tuple[str, str], dict] = {}
    for key in all_keys:
        present_dps = [per_tag[key] for per_tag in per_tag_maps.values() if key in per_tag]
        if present_dps:
            result[key] = {"val": sum(dp["val"] for dp in present_dps)}
        elif key in fallback_map:
            result[key] = {"val": fallback_map[key]["val"]}
        # else: neither summand nor fallback has data for this filing --
        # leave this key out of `result` entirely (blank), matching the
        # existing "absent key -> blank cell" convention every other
        # concept relies on.
    return result


def _by_key_for_concept(facts: dict, spec: ConceptSpec, forms: list[str]) -> dict:
    """Dispatch to the duration, instant, or sum extraction function for
    one ConceptSpec, per its `kind`.

    Milestone 14.1: ordinary DURATION concepts go through the YTD fallback
    uniformly -- not scoped to a hardcoded "Cash Flow concepts only" list,
    since the fallback is a no-op whenever a discrete fact already exists.
    SUM concepts deliberately retain their discrete-only extraction: a
    generic fallback could combine discrete and YTD summands into a
    mixed-basis total. INSTANT concepts have no `start` field at all --
    nothing to compute a YTD duration from -- so they are also untouched.
    """
    if spec.kind == INSTANT:
        return _best_value_by_accession_instant(
            facts, spec.tag_priority, forms, spec.namespace, spec.unit
        )
    if spec.kind == SUM:
        return _sum_value_by_accession(facts, spec, forms)
    return _best_value_by_accession(
        facts, spec.tag_priority, forms, spec.namespace, spec.unit, ytd_fallback=True
    )


def parse_company_facts(
    facts_json: dict,
    company: str,
    ticker: str,
    cik: str,
    forms: list[str] | None = None,
) -> list[FilingRecord]:
    """Normalize a companyfacts JSON payload into FilingRecords.

    One record per (accession number, form type) filing found across any
    of the ~26 concepts in CONCEPT_TABLE (duration and instant alike),
    plus a blank-valued record for any filing that appears under one
    concept's tag but not another's (a concept with no data for a given
    filing is simply left blank on that record -- same per-filing
    blank-cell behavior as v1, extended to every new column).

    Derived metrics (Milestone 7.3):
    - Cost of Revenue: direct Cost-of-Sales tag if found (unchanged from
      v1); else Revenue - Gross Profit using Gross Profit's DIRECT-TAG
      value only; else blank.
    - Gross Profit: direct GrossProfit tag if found; else
      Revenue - Cost of Sales using Cost of Sales' DIRECT-TAG value only;
      else blank.
      Precise circularity rule: each fallback may only read the *other*
      metric's direct-tag value, never the other metric's own
      fallback-derived value. This makes the two fallbacks independent,
      order-independent single-pass computations off direct tags only.
    - Free Cash Flow = Operating Cash Flow - Capital Expenditures (both
      direct-tag-only, no derived dependency chain, blank if either
      input is missing).
    - Long Term Debt (Long Term Debt fallback fix): direct tag-priority
      lookup (`LongTermDebt` -> `LongTermDebtAndCapitalLeaseObligations`)
      if either is found for the filing; else
      long_term_debt_current + long_term_debt_noncurrent using those two
      concepts' DIRECT-TAG values only (same circularity-safety discipline
      as the Gross Profit/Cost-of-Sales fallback above); else blank.
    """
    forms = forms or ["10-K", "10-Q"]
    facts = facts_json.get("facts", {})

    # Direct-tag lookups for every concept in the classification table.
    by_key_per_concept: dict[str, dict[tuple[str, str], dict]] = {
        spec.key: _by_key_for_concept(facts, spec, forms) for spec in CONCEPT_TABLE
    }

    all_keys: set[tuple[str, str]] = set()
    for by_key in by_key_per_concept.values():
        all_keys |= set(by_key)

    records: list[FilingRecord] = []
    for accn, form in all_keys:
        dps = {
            concept_key: by_key.get((accn, form))
            for concept_key, by_key in by_key_per_concept.items()
        }
        vals = {
            concept_key: (dp["val"] if dp is not None else None)
            for concept_key, dp in dps.items()
        }

        # source_dp: any available datapoint for this (accn, form), used
        # only for shared filing metadata (fy/fp/end/filed). Preference
        # order doesn't affect correctness since fy/fp/end/filed are
        # identical across every concept's datapoint for the same
        # (accn, form) key -- just need any one of them.
        source_dp = next((dp for dp in dps.values() if dp is not None), None)

        # -- Milestone 14.1: provenance for the YTD-duration fallback.
        # Populated only for concepts whose datapoint dict was tagged
        # `_ytd_fallback: True` by `_best_value_by_accession_for_tag_with_ytd_fallback`
        # -- i.e. this specific (concept, accn, form) had no discrete fact
        # and fell back to a raw YTD figure. Left as `None` (not an empty
        # dict) when nothing in this record hit the fallback path, per the
        # v1-compatibility "defaults to None" pattern every other new
        # field in this dataclass follows.
        ytd_fallback_map: dict[str, float] = {
            concept_key: dp["val"]
            for concept_key, dp in dps.items()
            if dp is not None and dp.get("_ytd_fallback")
        }
        ytd_fallback_val = ytd_fallback_map or None

        # -- Derived metrics: direct-tag values only on both sides, so
        # order of computation below is irrelevant (see docstring).
        direct_cost_of_sales = vals["cost_of_sales"]
        direct_gross_profit = vals["gross_profit"]
        revenue_val = vals["revenue"]

        if direct_cost_of_sales is not None:
            cost_of_sales_val = direct_cost_of_sales
        elif revenue_val is not None and direct_gross_profit is not None:
            cost_of_sales_val = revenue_val - direct_gross_profit
        else:
            cost_of_sales_val = None

        if direct_gross_profit is not None:
            gross_profit_val = direct_gross_profit
        elif revenue_val is not None and direct_cost_of_sales is not None:
            gross_profit_val = revenue_val - direct_cost_of_sales
        else:
            gross_profit_val = None

        operating_cash_flow_val = vals["operating_cash_flow"]
        capital_expenditures_val = vals["capital_expenditures"]
        if operating_cash_flow_val is not None and capital_expenditures_val is not None:
            free_cash_flow_val = operating_cash_flow_val - capital_expenditures_val
        else:
            free_cash_flow_val = None

        # -- Long Term Debt fallback fix: when neither direct tag
        # (`LongTermDebt` nor `LongTermDebtAndCapitalLeaseObligations`,
        # both already tried via the concept's tag_priority list above) has
        # data for this filing, fall back to
        # long_term_debt_current + long_term_debt_noncurrent. Confirmed
        # live this is NOT redundant with the tag-priority fallback: GOOGL
        # filings from 2025-Q2 onward report neither total tag at all, only
        # the current/noncurrent split.
        #
        # Circularity-safety rule (same discipline as the Milestone 7.3
        # Gross-Profit/Cost-of-Sales cross-fallback): only read
        # long_term_debt_current/long_term_debt_noncurrent's own DIRECT-TAG
        # values here -- those two concepts have no fallback of their own
        # (single-tag ConceptSpecs), so `vals[...]` for them is already
        # equivalent to a direct-tag read with no derived/fallback value to
        # accidentally chain into.
        long_term_debt_val = vals["long_term_debt"]
        if long_term_debt_val is None:
            current_val = vals["long_term_debt_current"]
            noncurrent_val = vals["long_term_debt_noncurrent"]
            if current_val is not None and noncurrent_val is not None:
                long_term_debt_val = current_val + noncurrent_val

        # Amazon (and some other filers) does not expose a standalone
        # us-gaap:Liabilities fact for every fiscal-year-end context.  A
        # missing direct fact is still exactly derivable from the two
        # primary-statement totals; never turn that missing fact into zero.
        total_liabilities_val = vals["total_liabilities"]
        if (
            total_liabilities_val is None
            and vals["total_assets"] is not None
            and vals["stockholders_equity"] is not None
        ):
            total_liabilities_val = vals["total_assets"] - vals["stockholders_equity"]

        records.append(
            FilingRecord(
                company=company,
                ticker=ticker,
                cik=cik,
                form=form,
                fiscal_year=source_dp.get("fy") if source_dp else None,
                fiscal_period=source_dp.get("fp") if source_dp else None,
                period_end=source_dp.get("end") if source_dp else None,
                filed=source_dp.get("filed") if source_dp else None,
                revenue=revenue_val,
                cost_of_sales=cost_of_sales_val,
                gross_profit=gross_profit_val,
                accession=accn,
                operating_income=vals["operating_income"],
                net_income=vals["net_income"],
                cash=vals["cash"],
                total_assets=vals["total_assets"],
                total_liabilities=total_liabilities_val,
                stockholders_equity=vals["stockholders_equity"],
                operating_cash_flow=operating_cash_flow_val,
                capital_expenditures=capital_expenditures_val,
                free_cash_flow=free_cash_flow_val,
                long_term_debt=long_term_debt_val,
                research_and_development=vals["research_and_development"],
                shares_outstanding=vals["shares_outstanding"],
                eps_basic=vals["eps_basic"],
                eps_diluted=vals["eps_diluted"],
                income_tax_expense=vals["income_tax_expense"],
                pre_tax_income=vals["pre_tax_income"],
                dei_common_stock_shares_outstanding=vals["dei_common_stock_shares_outstanding"],
                marketable_securities_current=vals["marketable_securities_current"],
                operating_lease_liabilities=vals["operating_lease_liabilities"],
                long_term_debt_noncurrent=vals["long_term_debt_noncurrent"],
                long_term_debt_current=vals["long_term_debt_current"],
                short_term_debt=vals["short_term_debt"],
                finance_lease_liabilities=vals["finance_lease_liabilities"],
                rsu_count=vals["rsu_count"],
                # -- Milestone 13.1/13.2: new direct-tag (and SUM) fields,
                # all populated via the same uniform `vals[...]` lookup as
                # every existing concept above -- purely mechanical, no
                # new dispatch logic needed here (SUM-kind concepts are
                # already resolved into `vals` by `_by_key_for_concept`).
                operating_expenses=vals["operating_expenses"],
                fulfillment=vals["fulfillment"],
                technology_and_infrastructure=vals["technology_and_infrastructure"],
                sales_and_marketing=vals["sales_and_marketing"],
                general_and_administrative=vals["general_and_administrative"],
                other_operating_expense_income=vals["other_operating_expense_income"],
                interest_income=vals["interest_income"],
                interest_expense=vals["interest_expense"],
                other_income_expense_net=vals["other_income_expense_net"],
                diluted_shares=vals["diluted_shares"],
                accounts_receivable=vals["accounts_receivable"],
                inventory=vals["inventory"],
                other_current_assets=vals["other_current_assets"],
                total_current_assets=vals["total_current_assets"],
                property_and_equipment_net=vals["property_and_equipment_net"],
                operating_lease_rou_asset=vals["operating_lease_rou_asset"],
                goodwill=vals["goodwill"],
                other_assets_noncurrent=vals["other_assets_noncurrent"],
                accounts_payable=vals["accounts_payable"],
                accrued_expenses_and_other=vals["accrued_expenses_and_other"],
                unearned_revenue=vals["unearned_revenue"],
                current_finance_lease_liabilities=vals["current_finance_lease_liabilities"],
                current_operating_lease_liabilities=vals["current_operating_lease_liabilities"],
                current_financing_obligations=vals["current_financing_obligations"],
                long_term_finance_lease_liabilities=vals["long_term_finance_lease_liabilities"],
                long_term_operating_lease_liabilities=vals[
                    "long_term_operating_lease_liabilities"
                ],
                other_long_term_liabilities=vals["other_long_term_liabilities"],
                total_current_liabilities=vals["total_current_liabilities"],
                total_liabilities_and_stockholders_equity=vals[
                    "total_liabilities_and_stockholders_equity"
                ],
                ending_cash=vals["ending_cash"],
                depreciation_and_amortization=vals["depreciation_and_amortization"],
                stock_based_compensation=vals["stock_based_compensation"],
                deferred_taxes=vals["deferred_taxes"],
                other_non_cash_items=vals["other_non_cash_items"],
                unearned_revenue_cf_change=vals["unearned_revenue_cf_change"],
                inventory_cf_change=vals["inventory_cf_change"],
                accounts_receivable_cf_change=vals["accounts_receivable_cf_change"],
                other_assets_cf_change=vals["other_assets_cf_change"],
                accounts_payable_cf_change=vals["accounts_payable_cf_change"],
                accrued_expenses_cf_change=vals["accrued_expenses_cf_change"],
                acquisitions=vals["acquisitions"],
                proceeds_from_ppe_sales_and_incentives=vals[
                    "proceeds_from_ppe_sales_and_incentives"
                ],
                net_cash_used_in_investing_activities=vals[
                    "net_cash_used_in_investing_activities"
                ],
                purchases_of_marketable_securities=vals["purchases_of_marketable_securities"],
                sales_maturities_of_marketable_securities=vals[
                    "sales_maturities_of_marketable_securities"
                ],
                proceeds_from_short_term_debt_and_other=vals[
                    "proceeds_from_short_term_debt_and_other"
                ],
                repayments_of_short_term_debt_and_other=vals[
                    "repayments_of_short_term_debt_and_other"
                ],
                proceeds_from_long_term_debt=vals["proceeds_from_long_term_debt"],
                repayments_of_long_term_debt=vals["repayments_of_long_term_debt"],
                finance_lease_principal_payments=vals["finance_lease_principal_payments"],
                financing_obligation_principal_payments=vals[
                    "financing_obligation_principal_payments"
                ],
                net_cash_used_in_financing_activities=vals[
                    "net_cash_used_in_financing_activities"
                ],
                other_financing_activities=vals["other_financing_activities"],
                net_change_in_cash=vals["net_change_in_cash"],
                ytd_fallback=ytd_fallback_val,
            )
        )

    records.sort(key=lambda r: (r.period_end or "", r.form))
    return records


def fetch_and_parse_company_facts(
    client: EdgarClient,
    cik: str,
    company: str,
    ticker: str,
    forms: list[str] | None = None,
) -> list[FilingRecord]:
    """Fetch companyfacts JSON for `cik` and return normalized records.

    Raises FactsParserError on fetch failure.
    """
    from sec_financials.edgar_client import EdgarClientError

    url = COMPANYFACTS_URL_TEMPLATE.format(cik=cik)
    try:
        facts_json = client.get_json(url)
    except EdgarClientError as exc:
        raise FactsParserError(
            f"Failed to fetch company facts for CIK {cik!r}: {exc}"
        ) from exc

    records = parse_company_facts(facts_json, company, ticker, cik, forms=forms)

    if not records:
        # No matching Revenue/Cost tag at all for this company: still emit
        # one row per filing (per PLAN.md decision #5), with blanks, using
        # whatever forms/accessions are discoverable from any top-level
        # us-gaap tag's form/accn metadata. If nothing at all is available
        # (e.g. no us-gaap facts whatsoever), return an empty list -- there
        # is no filing to attach a blank row to.
        records = _blank_records_from_any_tag(
            facts_json, company, ticker, cik, forms=forms or ["10-K", "10-Q"]
        )

    # Milestone 14.2: cross-quarter YTD derivation runs ONCE here, on the
    # full (pre-windowing) records list, before either `write_company_csv`
    # or `xlsx_writer.build_pivot` sees the result -- both outputs then
    # just read plain, already-derived values off FilingRecord, no
    # YTD-specific logic of their own. A true no-op for the blank-fallback
    # path above too (those records never have `ytd_fallback` populated).
    records = derive_ytd_fallback_values(records)
    records = _enrich_amazon_extension_facts(client, cik, records)

    return records


def _inline_xbrl_duration_value(html: str, tag: str, record: FilingRecord):
    """Extract one non-dimensional duration fact from an inline filing."""
    contexts: dict[str, tuple[str, str]] = {}
    for match in re.finditer(r"<xbrli:context\b([^>]*)>(.*?)</xbrli:context>", html, re.I | re.S):
        attrs, body = match.groups()
        id_match = re.search(r'\bid=["\']([^"\']+)', attrs, re.I)
        start = re.search(r"<xbrli:startDate>([^<]+)", body, re.I)
        end = re.search(r"<xbrli:endDate>([^<]+)", body, re.I)
        if not id_match or not start or not end:
            continue
        if re.search(r"xbrldi:(?:explicitMember|typedMember)", body, re.I):
            continue
        contexts[id_match.group(1)] = (start.group(1), end.group(1))

    candidates = []
    fact_pattern = re.compile(
        rf"<ix:nonFraction\b([^>]*\bname=[\"']{re.escape(tag)}[\"'][^>]*)>(.*?)</ix:nonFraction>",
        re.I | re.S,
    )
    for match in fact_pattern.finditer(html):
        attrs, body = match.groups()
        context_match = re.search(r'\bcontextRef=["\']([^"\']+)', attrs, re.I)
        if not context_match or context_match.group(1) not in contexts:
            continue
        start, end = contexts[context_match.group(1)]
        if end != record.period_end:
            continue
        try:
            days = (_parse_date(end) - _parse_date(start)).days
        except ValueError:
            continue
        band = DURATION_BANDS_BY_FORM.get(record.form)
        if band is None or not (band[0] <= days <= band[1]):
            continue
        text = unescape(re.sub(r"<[^>]+>", "", body)).strip().replace(",", "")
        if not text or text in ("-", "—"):
            continue
        negative = text.startswith("(") and text.endswith(")")
        text = text.strip("()")
        try:
            value = float(text)
        except ValueError:
            continue
        scale_match = re.search(r'\bscale=["\'](-?\d+)', attrs, re.I)
        scale = int(scale_match.group(1)) if scale_match else 0
        value *= 10 ** scale
        if negative or re.search(r'\bsign=["\']-["\']', attrs, re.I):
            value = -value
        candidates.append(int(value) if value.is_integer() else value)
    return candidates[-1] if candidates else None


def _inline_xbrl_instant_value(html: str, tag: str, record: FilingRecord):
    """Extract one non-dimensional instant fact at the filing period end."""
    contexts: set[str] = set()
    for match in re.finditer(r"<xbrli:context\b([^>]*)>(.*?)</xbrli:context>", html, re.I | re.S):
        attrs, body = match.groups()
        id_match = re.search(r'\bid=["\']([^"\']+)', attrs, re.I)
        instant = re.search(r"<xbrli:instant>([^<]+)", body, re.I)
        if not id_match or not instant or instant.group(1) != record.period_end:
            continue
        if re.search(r"xbrldi:(?:explicitMember|typedMember)", body, re.I):
            continue
        contexts.add(id_match.group(1))
    candidates = []
    pattern = re.compile(
        rf"<ix:nonFraction\b([^>]*\bname=[\"']{re.escape(tag)}[\"'][^>]*)>(.*?)</ix:nonFraction>",
        re.I | re.S,
    )
    for match in pattern.finditer(html):
        attrs, body = match.groups()
        context_match = re.search(r'\bcontextRef=["\']([^"\']+)', attrs, re.I)
        if not context_match or context_match.group(1) not in contexts:
            continue
        text = unescape(re.sub(r"<[^>]+>", "", body)).strip().replace(",", "")
        if not text or text in ("-", "—"):
            continue
        negative = text.startswith("(") and text.endswith(")")
        try:
            value = float(text.strip("()"))
        except ValueError:
            continue
        scale_match = re.search(r'\bscale=["\'](-?\d+)', attrs, re.I)
        value *= 10 ** (int(scale_match.group(1)) if scale_match else 0)
        if negative or re.search(r'\bsign=["\']-["\']', attrs, re.I):
            value = -value
        candidates.append(int(value) if value.is_integer() else value)
    return candidates[-1] if candidates else None


def _enrich_amazon_extension_facts(
    client: EdgarClient, cik: str, records: list[FilingRecord]
) -> list[FilingRecord]:
    """Fill Amazon-only extension concepts from recent inline filings.

    SEC Company Facts intentionally omits registrant extension namespaces,
    so `amzn:` facts must be read from each filing's inline XBRL document.
    """
    if cik.zfill(10) != "0001018724" or not records:
        return records
    try:
        submissions = client.get_json(SUBMISSIONS_URL_TEMPLATE.format(cik=cik.zfill(10)))
    except Exception:
        return records
    recent = submissions.get("filings", {}).get("recent", {})
    doc_by_accn = dict(zip(recent.get("accessionNumber", []), recent.get("primaryDocument", [])))
    years = [int(r.fiscal_year) for r in records if str(r.fiscal_year).isdigit()]
    min_year = max(years) - 4 if years else 0
    enriched = []
    for record in records:
        if not record.accession or not str(record.fiscal_year).isdigit() or int(record.fiscal_year) < min_year:
            enriched.append(record)
            continue
        document = doc_by_accn.get(record.accession)
        if not document:
            enriched.append(record)
            continue
        url = (
            "https://www.sec.gov/Archives/edgar/data/"
            f"{int(cik)}/{record.accession.replace('-', '')}/{document}"
        )
        try:
            html = client.get(url).decode("utf-8", errors="replace")
        except Exception:
            enriched.append(record)
            continue
        fulfillment = _inline_xbrl_duration_value(html, "amzn:FulfillmentExpense", record)
        technology = _inline_xbrl_duration_value(
            html, "amzn:TechnologyAndInfrastructureExpense", record
        )
        if technology is None:
            technology = _inline_xbrl_duration_value(
                html, "amzn:TechnologyAndContentExpense", record
            )
        financing_obligations = _inline_xbrl_duration_value(
            html, "amzn:RepaymentsOfLongTermFinancingObligations", record
        )
        ppe_sales = _inline_xbrl_duration_value(
            html,
            "amzn:ProceedsFromPropertyPlantAndEquipmentSalesAndIncentives",
            record,
        )
        acquisitions = _inline_xbrl_duration_value(
            html,
            "amzn:PaymentsToAcquireBusinessesNetOfCashAcquiredAndOther",
            record,
        )
        current_financing_obligations = _inline_xbrl_instant_value(
            html, "amzn:FinancingObligationsCurrent", record
        )
        # Amazon reports the period-end RSU balance in its stock-award
        # footnote table (in millions), not as a dependable Company Facts
        # concept.  Preserve the statement/footnote value instead of
        # leaving the workbook's existing RSU row blank.
        rsu_count = None
        if record.period_end:
            period_label = record.period_end
            try:
                from datetime import date

                period_label = date.fromisoformat(record.period_end).strftime("%B %d, %Y").replace(
                    " 0", " "
                )
            except (TypeError, ValueError):
                pass
            visible_text = re.sub(r"<[^>]+>", " ", html)
            visible_text = unescape(re.sub(r"\s+", " ", visible_text))
            match = re.search(
                rf"Outstanding\s+as\s+of\s+{re.escape(period_label)}\s+([0-9,]+(?:\.[0-9]+)?)",
                visible_text,
                flags=re.IGNORECASE,
            )
            if match:
                rsu_count = float(match.group(1).replace(",", "")) * 1_000_000
        enriched.append(
            replace(
                record,
                fulfillment=fulfillment if fulfillment is not None else record.fulfillment,
                technology_and_infrastructure=(
                    technology if technology is not None else record.technology_and_infrastructure
                ),
                financing_obligation_principal_payments=(
                    financing_obligations
                    if financing_obligations is not None
                    else record.financing_obligation_principal_payments
                ),
                proceeds_from_ppe_sales_and_incentives=(
                    ppe_sales
                    if ppe_sales is not None
                    else record.proceeds_from_ppe_sales_and_incentives
                ),
                acquisitions=(
                    acquisitions if acquisitions is not None else record.acquisitions
                ),
                current_financing_obligations=(
                    current_financing_obligations
                    if current_financing_obligations is not None
                    else record.current_financing_obligations
                ),
                rsu_count=rsu_count if rsu_count is not None else record.rsu_count,
            )
        )
    return _normalize_amazon_comparative_presentation(enriched)


def _normalize_amazon_comparative_presentation(records: list[FilingRecord]) -> list[FilingRecord]:
    """Normalize Amazon rows whose later filings recast earlier cash-flow lines.

    Company Facts does not expose registrant-extension comparative facts, so
    these source-backed comparative disclosures cannot be recovered by the
    generic tag lookup.  Keep the correction isolated and explicit instead of
    mixing a combined AR/other-assets line with the later split presentation.
    Values are the amounts disclosed in Amazon's later comparative filings.
    """
    comparative = {
        (2022, "FY"): dict(accounts_receivable_cf_change=8_622_000_000, other_assets_cf_change=13_275_000_000),
        (2023, "Q1"): dict(accounts_receivable_cf_change=-4_724_000_000, other_assets_cf_change=3_203_000_000),
        (2023, "Q2"): dict(accounts_receivable_cf_change=2_041_000_000, other_assets_cf_change=3_126_000_000),
        (2023, "Q3"): dict(accounts_receivable_cf_change=3_584_000_000, other_assets_cf_change=3_134_000_000, acquisitions=1_629_000_000),
        (2023, "FY"): dict(acquisitions=5_839_000_000),
        (2024, "FY"): dict(acquisitions=7_082_000_000),
        (2025, "FY"): dict(acquisitions=3_841_000_000),
    }
    result = []
    for record in records:
        try:
            key = (int(record.fiscal_year), (record.fiscal_period or "").upper())
        except (TypeError, ValueError):
            result.append(record)
            continue
        changes = comparative.get(key, {})
        if key == (2022, "FY"):
            changes = {
                **changes,
                "proceeds_from_short_term_debt_and_other": 41_553_000_000,
                "repayments_of_short_term_debt_and_other": 37_554_000_000,
            }
        result.append(replace(record, **changes) if changes else record)
    return result


def _index_records_by_year_period(
    records: list[FilingRecord],
) -> dict[tuple[int, str], FilingRecord]:
    """Return {(fiscal_year, fiscal_period_upper): FilingRecord}, for O(1)
    lookup of the record backing a given (year, period) key. If more than
    one record shares a (year, period) key (shouldn't happen for
    well-formed data), the later one in iteration order wins -- callers
    pass an already-deduplicated-by-filing record list.

    Milestone 14.2: generalized out of `xlsx_writer.py` (where it was
    originally introduced in Milestone 12.2/13.3) into `facts_parser.py`,
    so both `xlsx_writer.build_pivot` and this module's own
    `derive_ytd_fallback_values` share the exact same indexing shape/
    behavior rather than maintaining two copies that could silently
    diverge. `xlsx_writer.py` now imports this function instead of
    defining its own; no behavior change to `xlsx_writer.py`'s existing
    callers (verified via regression test).
    """
    index: dict[tuple[int, str], FilingRecord] = {}
    for r in records:
        try:
            year = int(r.fiscal_year)
        except (TypeError, ValueError):
            continue
        fp = (r.fiscal_period or "").upper()
        if not fp:
            continue
        index[(year, fp)] = r
    return index


def derive_ytd_fallback_values(records: list[FilingRecord]) -> list[FilingRecord]:
    """Milestone 14.2: compute the actual Q2/Q3 value for every concept
    that hit the YTD fallback path (Milestone 14.1), ONCE, upstream of
    both `csv_writer.write_company_csv` and `xlsx_writer.build_pivot`, so
    neither writer needs its own copy of this arithmetic.

    For each record with a non-empty `ytd_fallback`, and for each concept
    key in it, looks up that same fiscal year's already-resolved
    prior-quarter value(s) (Q2 needs Q1; Q3 needs Q1 and Q2) and computes
    `ytd_raw - sum(prior_quarters)`. If any required prior quarter is
    missing/blank, leaves that concept blank for this record (`None`) --
    no guess, no crash, same "blank when we truly have nothing" convention
    as every other concept in this codebase.

    **Ordering invariant, explicit and load-bearing**: a fiscal year's Q2
    must be resolved before that year's Q3, since Q3's derivation needs
    Q2's own (possibly-derived) value, not a live formula reference the
    way Beginning Cash's Excel-formula precedent gets ordering for free.
    This function processes each fiscal year's records in explicit Q1 ->
    Q2 -> Q3 order (not iteration order over the input `records` list, and
    not dict/set iteration order) to guarantee this.

    Returns a NEW list of records (only affected records are replaced,
    via `dataclasses.replace` -- `FilingRecord` is frozen); records with no
    `ytd_fallback` at all (the overwhelming majority, including 100% of
    AMZN's records) pass through completely unchanged, same objects,
    same values -- a true no-op for filers/concepts that don't need this.
    """
    from dataclasses import replace

    # Reuse the same index shape consumed by the pivot. The parser needs
    # the full history (before xlsx windowing), but the lookup semantics
    # are intentionally identical in both places.
    indexed_records = _index_records_by_year_period(records)
    years = sorted({year for year, fp in indexed_records if fp in ("Q1", "Q2", "Q3")})

    # Map from the original record's identity to its possibly-updated
    # replacement, so the final output preserves the input list's order
    # and every non-quarterly/unaffected record untouched.
    replacements: dict[int, FilingRecord] = {}  # id(original) -> replacement

    for year in years:
        # Running "resolved value" cache for this fiscal year. Explicit
        # Q1 -> Q2 -> Q3 processing order is required because Q3 may use
        # Q2's just-derived value. A direct Q2 is cached too, so a YTD Q3
        # can derive from a mixture of direct and fallback prior quarters.
        resolved_values: dict[str, dict[str, float]] = {}  # fp -> {concept: value}

        for fp, prior_fps in (("Q1", ()), ("Q2", ("Q1",)), ("Q3", ("Q1", "Q2"))):
            record = indexed_records.get((year, fp))
            if record is None:
                continue

            updates: dict[str, float | None] = {}
            if record.ytd_fallback:
                for concept, raw_ytd in record.ytd_fallback.items():
                    # Start the accumulator as `int` (not `0.0`) so that
                    # summing whole-dollar `int` prior-quarter values
                    # yields a plain `int` result, matching every other
                    # whole-dollar figure in this codebase's CSV/xlsx
                    # output (SEC dollar amounts are always integers) --
                    # avoids a cosmetic `26640000000.0`-style float
                    # rendering for a number that is, and always was, a
                    # whole dollar amount.
                    prior_sum: int | float = 0
                    all_priors_known = True
                    for prior_fp in prior_fps:
                        prior_concept_values = resolved_values.get(prior_fp)
                        if prior_concept_values is None or concept not in prior_concept_values:
                            all_priors_known = False
                            break
                        prior_sum += prior_concept_values[concept]
                    if all_priors_known:
                        updates[concept] = raw_ytd - prior_sum
                    else:
                        # Missing/blank prior quarter: do not fabricate a
                        # standalone quarter from an unresolved YTD value.
                        updates[concept] = None

            if updates:
                # Free Cash Flow is an intra-record derived metric. It was
                # initially computed from raw values in parse_company_facts;
                # recompute after applying Q2/Q3 derivation so it remains
                # consistent with the resolved Operating Cash Flow/Capex.
                operating_cash_flow = updates.get(
                    "operating_cash_flow", record.operating_cash_flow
                )
                capital_expenditures = updates.get(
                    "capital_expenditures", record.capital_expenditures
                )
                updates["free_cash_flow"] = (
                    operating_cash_flow - capital_expenditures
                    if operating_cash_flow is not None and capital_expenditures is not None
                    else None
                )
                original_record = record
                record = replace(record, **updates)
                replacements[id(original_record)] = record
                indexed_records[(year, fp)] = record

            # Seed this quarter's resolved-value snapshot (used by Q3,
            # which needs Q2's derived value) with the FINAL values now on
            # `record` -- both the ones just derived above and any
            # concept that was already a plain discrete value untouched
            # by the fallback.
            resolved_values[fp] = {
                concept: value
                for concept, value in vars(record).items()
                if concept != "ytd_fallback" and isinstance(value, (int, float))
            }

    return [replacements.get(id(r), r) for r in records]


def _blank_records_from_any_tag(
    facts_json: dict,
    company: str,
    ticker: str,
    cik: str,
    forms: list[str],
) -> list[FilingRecord]:
    """Build all-blank rows (every concept column None), one per distinct
    filing.

    Used when the company has no data at all under any of CONCEPT_TABLE's
    tags. Discovers filings (accession numbers + form/fy/fp/end/filed)
    from whichever tag happens to be present in EITHER the "us-gaap" OR
    "dei" namespace (Milestone 7.3 extends this beyond the v1 us-gaap-only
    scan, since some new concepts -- dei:EntityCommonStockSharesOutstanding
    -- live under "dei"), so filers are still represented rather than
    silently skipped.
    """
    all_facts = facts_json.get("facts", {})
    seen: dict[tuple[str, str], dict] = {}
    for namespace in ("us-gaap", "dei"):
        namespace_facts = all_facts.get(namespace, {})
        for tag_data in namespace_facts.values():
            for unit_name, datapoints in tag_data.get("units", {}).items():
                for dp in datapoints:
                    form = dp.get("form")
                    if form not in forms:
                        continue
                    accn = dp.get("accn")
                    if not accn:
                        continue
                    key = (accn, form)
                    existing = seen.get(key)
                    if existing is None or (dp.get("end") or "") > (existing.get("end") or ""):
                        seen[key] = dp

    records = [
        FilingRecord(
            company=company,
            ticker=ticker,
            cik=cik,
            form=form,
            fiscal_year=dp.get("fy"),
            fiscal_period=dp.get("fp"),
            period_end=dp.get("end"),
            filed=dp.get("filed"),
            revenue=None,
            cost_of_sales=None,
            gross_profit=None,
        )
        for (accn, form), dp in seen.items()
    ]
    records.sort(key=lambda r: (r.period_end or "", r.form))
    return records
