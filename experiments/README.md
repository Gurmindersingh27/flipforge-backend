# Quote Review + Missing Scope experiment

Status: executable manual-review experiment, not a shipped feature or customer validation.

Run from the backend repository using its existing Python environment:

```bash
python -m experiments.quote_review experiments/quote_review.synthetic.json
python -m experiments.quote_review experiments/quote_review.synthetic.json --select contractor-b
```

The supplied quotes, prices, property and evidence passages are invented test data. No real investor quote pair was supplied or successfully obtained during this work. The experiment does not establish real-world omission accuracy, local pricing or demand.

Reviewers supply common scope topics and classify each quote as included, excluded, not found, or not applicable. Exact excerpts are checked against supplied text. Explicit user confirmations have a separate origin and locator. The program does not interpret contractual language or automatically infer an exclusion from absence.

Unresolved work keeps the reviewed total unknown and blocks application. A reviewer can add a positive, explained allowance, identify a retained budget item that already covers it, or explain non-applicability. Blank and zero are not silently accepted as unknown costs. The quote total is counted once, additional allowances remain separate, selected baseline items are explicitly replaced, and the existing project contingency is applied once. Contractor contingency and overlap require a recorded reviewer decision.

Selecting a reviewed option produces an ordinary SaveDealRequest for the existing linked-revision workflow, with the unchanged engine recalculating the new scope. The CLI makes no network request and saves no deal. Integration tests exercise the proposal against the actual authenticated API with isolated fixtures, preserving the prior saved record and owner checks.

Persistence boundary: selected excerpts are stored in the existing quote item's notes and added allowances have their own source/notes. Existing length limits are enforced, never silently truncated. The complete two-option review and source texts remain in the experiment input/output; they do not persist as a structured production review. A production feature must add a properly typed, owner-controlled review record and immutable review snapshot before claiming full quote-review retention.

Next evaluation: obtain two redacted quote pairs from two active operators, plus their original budgets and observed decisions. Use the existing pilot intake. Have each operator identify actual exclusions, allowances and work handled elsewhere before seeing the prototype. Record missed differences, incorrect flags, correction minutes and decision time. Test whether retaining the review saves work on the next revision. These are outstanding experiments, not acceptance results.

Build judgment: a bounded manual review with evidence and explicit cost resolution is worth testing. Automatic extraction, OCR, document uploads and an AI missing-scope detector are not justified yet. The current product comparison should first show cost-neutral scope and exclusion changes clearly; the parallel frontend change does that.
