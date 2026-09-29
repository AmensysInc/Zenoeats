# Legal

What a lawyer needs to review Zenoeats' customer-facing policies before
launch, and the record of each review.

| File | What it is |
|---|---|
| [`LEGAL_REVIEW_BRIEF.md`](LEGAL_REVIEW_BRIEF.md) | The brief: how Zenoeats works, what personal information it holds and who receives it, how long it is kept, every email it sends, the gaps found, and the questions to answer. **Start here** |
| [`review-2026-09-29/`](review-2026-09-29/) | The four pages as sent for this review, printed to PDF: Privacy Policy, Terms of Service, Refunds & Cancellations, Deleting your data |

## Sending it for review

Send the lawyer the brief and the four PDFs in `review-2026-09-29/`.
Before sending, fill in what only the business can supply (the brief's
section 2): the legal entity name, the address, the contact mailbox, where
the business operates, and the deletion response windows. The review
depends on them.

## After the review

1. Apply the lawyer's changes to the pages themselves, in `web/legal/*.html`.
   Those are what customers see; the PDFs here are only a snapshot of what
   was sent.
2. Replace every `PLACEHOLDER` in those pages.
3. Remove the "Draft — not yet reviewed by a lawyer" banner and the "Before
   this page goes live" paragraph from each page.
4. If the Terms changed materially, raise `CURRENT_VERSION` in
   `backend/app/services/terms.py`, so every customer agrees again on their
   next order.
5. Tick the items in `docs/operations/STEPS_BEFORE_PRODUCTION.md` §9.
6. Keep the lawyer's answers here as `review-<date>/answers.md`, next to the
   PDFs they refer to.

A later review gets its own `review-<date>/` folder with fresh PDFs, so each
answer stays next to the wording it was about.
