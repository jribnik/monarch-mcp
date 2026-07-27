# `get_transaction_rules`: request `merchantNameCriteria` + `originalStatementCriteria`

## Summary

`get_transaction_rules` returns rules whose criteria appear **empty**, even
though the rules clearly match on something and work in the web app. The reason:
the `TransactionRuleFields` fragment only requests `merchantCriteria`, but rules
created in the current Monarch UI store their match text in
**`merchantNameCriteria`** (match on merchant name) and
**`originalStatementCriteria`** (match on the raw statement). Those fields aren't
in the fragment, so they never come back — and `merchantCriteria` is `null` for
these rules, making them look criteria-less.

## Evidence

Captured the web app's own `Web_GetTransactionRules` request (HAR). Its
`TransactionRuleFields` fragment selects **three** merchant-side criteria, not one:

```graphql
merchantCriteria { operator value }
merchantNameCriteria { operator value }        # <-- missing from the library
originalStatementCriteria { operator value }   # <-- missing from the library
```

Querying a live account with those fields added, all 11 rules that previously
showed no criteria now resolve, e.g.:

| rule action | actual criteria (previously invisible) |
|---|---|
| Returns | `merchantNameCriteria eq "amazon"` + amount > 0 |
| OTB Sales | `merchantNameCriteria eq "ebay"` + amount > 0 |
| Home Improvement | `merchantNameCriteria eq "payrix"` + `originalStatementCriteria contains "mission pest"` |
| Groceries | `merchantNameCriteria eq "amazon"` + `originalStatementCriteria contains "amazon fresh"` |

## Fix

Add the two fields to the `TransactionRuleFields` fragment in
`get_transaction_rules` (`monarchmoney/monarchmoney.py`):

```graphql
merchantNameCriteria { operator value __typename }
originalStatementCriteria { operator value __typename }
```

## Testing

Against a live account: before, `get_transaction_rules()` showed
`merchantNameCriteria`/`originalStatementCriteria` for **0/11** rules (they
weren't requested); after, **11/11** rules resolve their criteria. Purely
additive — no change to existing fields or behavior.

## Not included

- The current schema also exposes newer criteria (`criteriaOwnerUserIds`,
  `criteriaBusinessEntityIds`, …) for household/business-entity features. Left
  out to keep this minimal and to only ship fields verified on a live account.
- The separate service-level `TransactionService.get_transaction_rules`
  (`services/transaction_service.py`) queries an entirely different legacy shape
  (`conditions`/`actions`/`isEnabled`) that doesn't match `TransactionRuleV2` at
  all — that needs its own rewrite and is out of scope here.
