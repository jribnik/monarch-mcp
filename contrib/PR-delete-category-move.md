# Support `moveToCategoryId` in `delete_transaction_category` (stop orphaning transactions)

## Summary

`delete_transaction_category` can't reassign a category's transactions on delete —
so deleting a category that still has transactions **silently orphans them to
"Uncategorized"** instead of moving them somewhere useful.

The GraphQL mutation already supports the move:

```graphql
mutation Web_DeleteCategory($id: UUID!, $moveToCategoryId: UUID) {
  deleteCategory(id: $id, moveToCategoryId: $moveToCategoryId) { deleted errors { ... } }
}
```

but the Python method neither accepts nor forwards `moveToCategoryId`:

```python
async def delete_transaction_category(self, category_id: str) -> bool:
    ...
    variables = {
        "id": category_id,          # <- moveToCategoryId never sent
    }
```

So `$moveToCategoryId` is always `null`, and any transactions under the deleted
category fall back to Uncategorized. For anyone consolidating/cleaning up
categories this is a data-loss footgun — you have to re-categorize everything by
hand afterward.

## Fix

Add an optional `move_to_category_id` parameter and pass it through:

```python
async def delete_transaction_category(
    self, category_id: str, move_to_category_id: Optional[str] = None
) -> bool:
    ...
    variables = {"id": category_id}
    if move_to_category_id is not None:
        variables["moveToCategoryId"] = move_to_category_id
```

(`monarchmoney/monarchmoney.py`, `delete_transaction_category`.) Behavior is
unchanged when the new arg is omitted; passing it reassigns the transactions in
the same call the API already supports.

## Testing

Against a live account: deleting a custom category ("Farmer's Market", 3 txns)
with `move_to_category_id` set to the Groceries category id returned
`deleted: true`, moved all 3 transactions into Groceries, and left **0**
transactions Uncategorized. Without the arg (current behavior) the same delete
orphans those 3 to Uncategorized.

## Notes

Independent of the domain-migration / gql-connector PR — this is a pure
method-signature/variable fix, no schema dependency.
