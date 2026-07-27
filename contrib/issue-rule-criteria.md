# `get_transaction_rules` returns null match-criteria on the current API

## Summary

On the current Monarch API, `get_transaction_rules()` returns rules whose
**match criteria are all `null`** — `merchantCriteria`, `categoryIds`, and
`accountIds` come back empty even for rules that clearly match on a merchant and
work correctly in the Monarch web UI. The **action** fields
(`setMerchantAction`, `setCategoryAction`, `amountCriteria`,
`recentApplicationCount`, `lastAppliedAt`, …) populate fine.

Net effect: a consumer can see what each rule *does* and when it last fired, but
not *what it matches on* — which makes programmatic rule review, dedup, or
safe deletion impossible (you can't tell what a rule triggers on).

## Reproduction

```python
rules = await mm.get_transaction_rules()
for r in rules["transactionRules"]:
    print(r["merchantCriteria"], r["setCategoryAction"] and r["setCategoryAction"]["name"])
```

Observed (12 rules on the account):

```
None  Entertainment & Recreation   # setMerchantAction=DLR, clearly a merchant rule
None  Returns                      # amountCriteria populated (gt 0), merchantCriteria null
None  Cash Back
... merchantCriteria is None for every rule ...
```

`amountCriteria` is correctly returned (e.g. `{operator: "gt", value: 0.0}`),
so the request/response path works — it's specifically the criteria fields in
the `TransactionRuleFields` fragment that no longer map to the live schema.

## Likely cause

Same class of drift as the `api.monarchmoney.com → api.monarch.com` move: the
`TransactionRuleV2` criteria fields appear to have been renamed/restructured
server-side, so the hardcoded fragment selects fields that resolve to `null`.

I couldn't confirm the new field names — schema introspection is disabled for
non-admin tokens (`"Introspection queries are disabled for non-admin users."`),
so this needs someone who can see the current `TransactionRuleV2` shape (e.g.
by capturing the web app's `GetTransactionRules` query) to update the fragment.

## Environment

- `monarchmoney-enhanced` @ current `main`
- API host `api.monarch.com`
- Separate from the login/GraphQL-connector PR (that one fixes auth + transport;
  this is about the rules read query specifically).
