# `create_transaction_rule` silently drops most actions & criteria

## Summary

`create_transaction_rule` accepts parameters for `set_merchant_action`,
`set_hide_from_reports_action`, `needs_review_by_user_action`,
`send_notification_action`, `review_status_action`, `link_goal_action` (and
`unassign_needs_review_by_user_action`) — but **none of them are forwarded** into
the mutation input. The `rule_input` dict only ever includes `setCategoryAction`,
`addTagsAction`, and `splitTransactionsAction`.

Result: any rule that *only* uses one of the dropped actions fails server-side
with **"Transaction rule must have one action"**, and you can't create
rename-merchant or hide-from-reports rules at all.

Separately, the API supports **`merchantNameCriteria`** and
`originalStatementCriteria` (how UI-created rules actually match — see the
companion `get_transaction_rules` PR), but there was no way to pass them.

## Fix

- Add `merchant_name_criteria` and `original_statement_criteria` parameters.
- Forward every already-declared-but-dropped action, plus the two new criteria,
  into `rule_input` when provided.

```python
if set_merchant_action is not None:
    rule_input["setMerchantAction"] = set_merchant_action
if set_hide_from_reports_action is not None:
    rule_input["setHideFromReportsAction"] = set_hide_from_reports_action
# ...needsReviewByUserAction, sendNotificationAction, reviewStatusAction,
#    linkGoalAction, merchantNameCriteria, originalStatementCriteria
```

## Testing

Against a live account (edited lib via `PYTHONPATH`):
- Before: `create_transaction_rule(set_hide_from_reports_action=True, ...)` →
  server error *"Transaction rule must have one action."*
- After: creating a hide-from-reports rule succeeds and reads back with
  `setHideFromReportsAction: true`. Verified `merchant_criteria` (contains)
  matches via `preview_transaction_rule` (e.g. 2 "Internal Revenue Service"
  transactions), and three such hide rules were created and confirmed.

## Note

`merchantNameCriteria` still reads back null after create — the API appears to
accept `merchantCriteria` (contains) for merchant-name matching and ignores the
`merchantNameCriteria` *input* key, so the practical matcher is
`merchant_criteria`. The new params are forwarded regardless in case the input
schema exposes them; they're harmless when unused.
