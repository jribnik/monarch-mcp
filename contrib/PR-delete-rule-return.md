# `delete_transaction_rule` always returns `False`, even on success

## Summary

`delete_transaction_rule` **deletes the rule correctly but always returns
`False`**, so callers can't tell success from failure (and may wrongly conclude
the delete failed).

The `deleteTransactionRule` mutation is now backed by a V2 type
(`DeleteTransactionRuleV2Mutation`) that returns `deleted: false` **even on a
successful delete**. Both delete implementations trust that flag:

```python
# monarchmoney/monarchmoney.py
return result.get("deleteTransactionRule", {}).get("deleted", False)

# monarchmoney/services/transaction_service.py
success = delete_result.get("deleted", False)
...
return success
```

so they return `False` for every successful deletion.

## Evidence

Deleting a real rule and inspecting the raw response:

```json
{"deleteTransactionRule": {"deleted": false, "errors": null,
 "__typename": "DeleteTransactionRuleV2Mutation"}}
```

`deleted` is `false`, `errors` is `null`, and the rule **is** actually gone
(confirmed by re-fetching `get_transaction_rules` — the rule no longer appears).

Failure modes, by contrast, don't return a clean payload — they raise:

```
delete id "999999999999999999"  -> GraphQLError: "Not found" (path: deleteTransactionRule)
```

So `deleted` no longer signals success; the presence/absence of `errors` (with
real failures raising) does.

## Fix

Stop trusting `deleted`; treat a returned payload with no `errors` as success
(genuine failures raise or populate `errors`):

```python
payload = result.get("deleteTransactionRule") or {}
return not payload.get("errors")
```

Applied to both `MonarchMoney.delete_transaction_rule`
(`monarchmoney.py`) and `TransactionService.delete_transaction_rule`
(`services/transaction_service.py`).

## Testing

Against a live account: created a throwaway rule, deleted it — the method now
returns `True`, and the rule is confirmed gone via `get_transaction_rules`.
Deleting a nonexistent id still raises, as before.

## Not included

`delete_all_transaction_rules` uses the same `deleted`-flag pattern and is
*likely* affected too, but I couldn't verify it without deleting all rules on a
live account, so I left it out of this PR. Worth a look by someone able to test
it safely.
