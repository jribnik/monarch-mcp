# Fix service-level `get_transactions` and `get_merchants` against the current schema

## Summary

Two `TransactionService` methods are fully broken on the current API (every call
raises the generic `"Something went wrong while processing"` GraphQL error):

- **`get_transactions`** — sends filters as top-level `allTransactions(first:, offset:, startDate:, merchantIds:, …)` arguments, which no longer exist, and selects two removed Transaction fields.
- **`get_merchants`** — selects two removed Merchant fields.

Both were confirmed against a live account, and the correct shapes were recovered
from the web app's own GraphQL requests (captured via a HAR) plus field-probing.

## `get_transactions`

The current schema takes a single `TransactionFilterInput` and moves paging onto
the nested `results()` connection:

```graphql
allTransactions(filters: $filters) {
  totalCount
  results(offset: $offset, limit: $limit, orderBy: $orderBy) { ...TransactionFields }
}
```

Filter-key mapping (old top-level arg → `filters` key), all verified live:

| old | new |
|---|---|
| `merchantIds` | `merchants` |
| `categoryIds` | `categories` |
| `accountIds` | `accounts` |
| `tagIds` | `tags` |
| `startDate` / `endDate` | `startDate` / `endDate` |
| `minAmount` / `maxAmount` | `absAmountGte` / `absAmountLte` |
| **`isCredit`** | **`creditsOnly`** (old key rejected; `True`=credits, `False`=debits — verified: 621 + 3275 = 3896 total) |

Removed Transaction fields (rejected by the schema): **`originalDescription`**
(use `plaidName` / `dataProviderDescription`) and **`isChild`** (use
`isSplitTransaction`).

## `get_merchants`

Removed rejected Merchant fields **`lastTransactionDate`** and **`categories`**;
added **`createdAt`** (which the web app's merchant query uses). Remaining valid
fields: `id`, `name`, `logoUrl`, `transactionCount`, `createdAt`.

## Testing

Against a live account, with the edited library loaded, all of these now succeed
(previously every one raised):

- `get_transactions()` → 3896 total
- `get_transactions(merchant_ids=[…])` → 165 (single merchant)
- `get_transactions(start_date=…, end_date=…, is_credit=False)` → 193
- `get_transactions(is_credit=True)` → 621
- `get_transactions(category_ids=[…], abs_amount_range=[20, None])` → 454
- `get_merchants()` → merchants with `transactionCount` + `createdAt`

## Not included / related

- The **top-level** `MonarchMoney.get_transactions` already works, but its
  `is_credit` handling has the *same* latent bug (it sends the rejected
  `isCredit` filter key, so the credit filter is silently ignored). Same
  `isCredit → creditsOnly` fix would apply; left out to keep this PR scoped to
  the fully-broken service methods.
- `get_merchant_details` / `get_edit_merchant` also select `lastTransactionDate`
  and may need similar treatment, but I didn't verify them here.
