# Sanctum Sanctorum — Submission Notes

## Deployment Information

- **Live URL**: `https://sanctum-sanctorum-anmol.onrender.com`
- **Local URL**: `http://localhost:8000` (Swagger UI available at `/docs`)
- **Seeded Member Accounts for Testing**:
  - `Member #1`: **Wong Li** (`wong@example.com`, Tier: `supreme` — unlimited borrowing, 15% discount, access to restricted grimoires)
  - `Member #2`: **Christine Palmer** (`christine@example.com`, Tier: `master` — max 5 loans, 10% discount, access to restricted grimoires)
  - `Member #3`: **Jonathan Pangborn** (`jonathan@example.com`, Tier: `adept` — max 3 loans, 5% discount)
  - `Member #4`: **Sara Lin** (`sara@example.com`, Tier: `apprentice` — max 1 loan, 0% discount)
- **Database**:
  - Local & Test Suite: SQLite with in-memory isolation per test.
  - Production: Compatible with PostgreSQL (e.g. Supabase, Render PostgreSQL, Neon, Railway) configured via `SANCTUM_DATABASE_URL`.

---

## 1. Summary of Completed Features

All features defined in [SPEC.md](SPEC.md) have been implemented and validated against the acceptance test suite:
- **Test Suite Result**: 202 passed / 202 tests (100% pass rate).
- **Books (`/books`)**:
  - Validation: Title and author length constraints (1–200 characters measured post-stripping).
  - Normalization: ISBN-13 hyphens/spaces stripped, exact 13-digit verification, and full ISBN-13 weighted checksum validation ($1, 3, 1, 3\dots$ modulo 10).
  - Uniqueness: 409 conflict on duplicate normalized ISBN.
  - CRUD & Search: Case-insensitive substring search (`q`) across title and author, boolean `restricted` filter, inclusive price range (`min_price`, `max_price`), strict sorting (`title`, `-title`, `price`, `-price` with id ascending tie-break), and paginated response reporting total pre-pagination matches.
  - Partial updates (`PATCH /books/{id}`): Updates only submitted fields, rejects explicit `null`s with 422, ignores `isbn` and extraneous keys.
- **Members (`/members`)**:
  - Registration: Email whitespace trimming and lowercasing, regex validation, duplicate email conflict rejection (409).
  - Tier verification: Proper hierarchical ranking (`apprentice` < `adept` < `master` < `supreme`).
  - Activity Statistics (`GET /members/{id}/stats`): Aggregation of paid orders, total spend in cents, active unreturned loans, strictly overdue loans (`now > due_at`), and cumulative late fees on returned loans.
- **Orders (`/orders`)**:
  - Pre-flight Validation: Schema-level rejection of empty item sets or duplicate book ids in one order (422).
  - Strict Verification Pipeline: Member existence (404) $\rightarrow$ Book existence (404) $\rightarrow$ Restricted title tier clearance (403) $\rightarrow$ Atomic all-or-nothing inventory stock availability (409).
  - Stock Reservation: Immediate reservation on order creation.
  - Pricing Engine: Freezing unit price at order time, tier discounts (0%, 5%, 10%, 15%) plus bulk volume discount (+5% when $\sum \text{quantity} \ge 10$), integer floor rounding for discount cents.
  - Order Lifecycle: Status transitions `pending` $\rightarrow$ `paid` (stock remains reserved) and `pending` $\rightarrow$ `cancelled` (restores inventory of all order items).
- **Loans (`/loans`)**:
  - Lending Constraints: 404 on missing entities $\rightarrow$ 403 on restricted items for tiers below Master $\rightarrow$ 409 if borrower owes any overdue books $\rightarrow$ 409 if borrower currently holds the same book $\rightarrow$ 409 if tier lending limit reached $\rightarrow$ 409 if stock is zero.
  - Dynamic Status: Computed at read time (`returned` if returned, `overdue` if `now > due_at`, else `active`). Strict boundary: at `now == due_at`, loan remains active.
  - Return Processing: Stock replenishment (+1) and late fee calculation ($25¢$ per started day late, with ceiling at the book's current price).
  - Member Loans Listing: Ordered by loan id ascending, with optional status query filter (`active`, `overdue`, `returned`).
- **Reports (`/reports`)**:
  - Top-selling books query aggregated over paid orders only, sorted by copies sold descending, then title ascending, with configurable limit (1–50).

---

## 2. Architectural Decisions & Trade-Offs

### Layered Separation of Concerns
- **Routers as Thin Transports**: Routers (`app/routers/*.py`) only handle request unpacking, dependency injection (`get_db`, `get_now`), and delegating to services. No SQL queries or domain rules exist in router endpoints.
- **Service Encapsulation**: Domain business rules (discount calculation, inventory decrementing/restoration, lending rules, fee math) live in `app/services/*.py`. This keeps logic testable and decoupled from the web framework.
- **Pydantic Validation Boundary**: Syntax and format validation (e.g. ISBN length, ISBN-13 checksum, email regex, positive integers, order item uniqueness) occurs at the schema boundary, immediately returning standard FastAPI 422 errors before any database transaction is initiated.

### Data Integrity & Atomic Transactions
- **All-or-Nothing Stock Reservation**: When placing an order with multiple titles, the inventory check validates all items before modifying any stock. If any single title has insufficient stock, the transaction aborts with HTTP 409 and leaves all inventory untouched.
- **Stock Reservation Lifecycle**: Stock is decremented at order creation (`pending`) rather than payment. This prevents overselling high-demand books while checkout is in progress. Cancelling the order safely restrains and restores the reserved inventory back to the catalog.
- **Dynamic Computed State vs. Static Database Fields**: Loan statuses (`active`, `overdue`, `returned`) are calculated at read-time against `clock.get_now()`. Storing an `overdue` status flag in the database would require cron workers or periodic database sweeps; calculating dynamically at read time ensures 100% accuracy with zero background job overhead.

### Multi-Database Dialect Compatibility (SQLite & PostgreSQL)
- In `app/db.py`, `connect_args={"check_same_thread": False}` is applied conditionally only when connecting to SQLite. When deployed against cloud PostgreSQL (Supabase / Render / Neon), the engine connects seamlessly without driver argument errors.
- URI normalization automatically handles `postgres://` prefixes generated by some cloud providers (e.g. Render/Heroku), translating them to `postgresql://`.

---

## 3. Spec Observations & Clarifications

1. **Bug in Starter Code for Tier Ranking (`tier_at_least`)**:
   - In `app/services/members.py`, the starter code contained:
     ```python
     return TIER_ORDER.index(tier) > TIER_ORDER.index(minimum)
     ```
     Because `>` was used instead of `>=`, members of `master` tier were rejected from accessing restricted books (even though the spec clearly states `RESTRICTED_MIN_TIER = 'master'`). Corrected to `>=`.
2. **Email Normalization Timing**:
   - The spec required email to be stripped and lowercased before regex checking. In the starter code, regex validation was performed directly on raw input without stripping, causing whitespace-padded valid emails to fail validation. Corrected by applying `.strip().lower()` inside the validator.
3. **Pagination Total Count**:
   - The starter `list_books` computed `total = len(books)` after applying `limit` and `offset`, which reported the page size rather than the total matching records in the catalogue. Updated to issue a pre-pagination count query (`func.count()`).
4. **Late Fee Price Cap vs. Price Fluctuations (`POST /loans/{id}/return`)**:
   - The spec specifies: `late_fee_cents = min(days_late * 25, book.price_cents), using the book's price at the time of return.`
   - In orders, prices are frozen at order creation time (`unit_price_cents`). For loans, using the price at return time means if an admin discounts a book to $1.00 via `PATCH /books/{id}`, an overdue borrower's fee cap drops to $1.00; conversely, if the book price increases, their fee cap rises. In production, snapshotting `borrow_price_cents` on the loan model would provide deterministic fee ceilings.
5. **Optional Extra Implemented: High-Concurrency Safe Stock Reservation (`POST /orders` & `POST /loans`)**:
   - To handle concurrent checkouts and borrows for the last copy safely, we implemented atomic conditional updates (`UPDATE books SET stock = stock - :qty WHERE id = :id AND stock >= :qty`). If `rowcount == 0`, the transaction rolls back immediately and raises HTTP 409 Conflict. This guarantees inventory integrity across concurrent requests without overselling or race conditions.
6. **Cross-Database Collation in Mixed-Case Title Sorting**:
   - As noted in `SPEC.md`, SQLite orders uppercase before lowercase (`A`, `Z`, `a`, `z`) while PostgreSQL default collations sort case-insensitively. Standardizing to `func.lower(Book.title)` guarantees identical deterministic ordering across all environments.
7. **Optional Extra Implemented: `GET /members` with Pagination**:
   - Implemented `GET /members?limit=20&offset=0` returning `MemberPage(items=[MemberOut], total, limit, offset)` matching the pagination style of the catalogue.

---

## 4. AI Usage

In adherence to Section 5 of [INSTRUCTIONS.md](INSTRUCTIONS.md):

- **Tools Used**: Antigravity IDE (Gemini 3.8 Flash High reasoning model).
- **Purposes**:
  1. *Repo Analysis & Planning*: Auditing test failures and comparing unimplemented functions against [SPEC.md](SPEC.md).
  2. *Algorithmic Implementation*: Implementing the ISBN-13 check digit formula and ceil-based late fee day calculation.
  3. *Refactoring & Clean Layering*: Ensuring router handlers remain thin while consolidating all business logic and error triggers inside service modules.
  4. *Deployment Configuration*: Scaffolding production `Dockerfile`, `render.yaml`, and `.env.example`.
- **Where AI Output Was Overridden**:
  - *Atomic Stock Reservation vs. In-Memory Checks*: AI initially generated straightforward Python in-memory decrements (`if book.stock >= qty: book.stock -= qty`). While this passed initial sequential unit tests, I overrode it with database-level atomic conditional updates (`UPDATE books SET stock = stock - :qty WHERE id = :id AND stock >= :qty`) with `res.rowcount == 0` check. This prevents race conditions and overselling when multiple concurrent users checkout or borrow the last copy simultaneously.
  - *Order of Checks in Order Creation*: An initial code suggestion checked member permissions before verifying whether all requested books existed. [SPEC.md](SPEC.md) strictly requires 404 (member not found / book not found) to be raised before 403 (tier clearance). I restructured the service to validate all book IDs first so missing items return 404 before evaluating restricted tier access.
