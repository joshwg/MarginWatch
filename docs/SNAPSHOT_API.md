# MarginWatch Snapshot API

`GET /api/snapshot` returns everything the MarginWatch web page shows — every open
position with live prices, greeks and risk indicators, plus the margin/theta
summary — as a single JSON document. It exists so external dashboards (e.g. a
Glance widget) can consume MarginWatch data without a browser login.

## Request

```
GET https://<host>/api/snapshot
```

### Authentication

The endpoint is protected by the same password the web UI uses (`MARGIN_PWD`).
There is no session or cookie — send the password on every request in a header,
one of (checked in this order):

| Method | Example |
|---|---|
| Bearer token (preferred) | `Authorization: Bearer <password>` |
| API-key header | `X-Api-Key: <password>` |

Query-string authentication is deliberately **not** supported: a `?key=` parameter
would be written to web-server and proxy access logs.

A missing or wrong password returns `401 {"error": "unauthorized"}`.

### Query parameters

| Param | Values | Default | Meaning |
|---|---|---|---|
| `cached` | `1`, `true`, `yes` | off | Return whatever is already in the server's cache without fetching market data. Fast, but prices may be stale or `null` on a cold server. |
| `warm` | `1`, `true`, `yes` | off | Only with `cached`: answer from the cache immediately **and** start a background refresh of any stale market data (at most one refresh runs at a time), so the *next* call returns fresh prices. The right mode for a dashboard that polls every minute and cannot wait several seconds for a cold fetch. |
| `sort` | `alpha`, `type`, `expiration` | the sort order configured in the UI | Order of the `positions` array. `alpha` = symbol, then expiration, then strike; `type` = calls (incl. covered calls) → puts → everything else; `expiration` = soonest expiry first. |

Without `cached`, the call first refreshes any expired market data (same work as
the web page's phase-2 load). On a warm cache it returns in well under a second;
on a cold cache or right after a price-TTL expiry it can take several seconds
per symbol that needs fetching. Concurrent calls are serialised server-side, so
polling from a dashboard is safe.

### Example

```bash
curl -s -H "Authorization: Bearer $MARGIN_PWD" https://margin.example.com/api/snapshot | jq .
```

## Response

`200 OK`, `Content-Type: application/json`.

```json
{
  "generated_at": "2026-08-16T14:32:07-04:00",
  "version": "1.1.0",
  "summary": {
    "total_margin": 104.8,
    "avail_margin": 370.2,
    "total_theta": 312,
    "portfolios": [
      { "id": 1, "name": "Main", "abbrev": "Mai", "is_default": true,
        "max_margin": 250000, "multiplier": 1.5,
        "position_count": 4, "total_margin": 66.8, "avail_margin": 308.2 },
      { "id": 2, "name": "IRA", "abbrev": "IRA", "is_default": false,
        "max_margin": 100000, "multiplier": 1.0,
        "position_count": 1, "total_margin": 38.0, "avail_margin": 62.0 }
    ]
  },
  "weekly_summary": [
    { "week_ending": "2026-08-21", "week_label": "Aug 21", "position_count": 3, "total_margin": 28.5, "itm_count": 0 },
    { "week_ending": "2026-08-28", "week_label": "Aug 28", "position_count": 5, "total_margin": 42.0, "itm_count": 1 }
  ],
  "fetch_errors": [],
  "positions": [
    {
      "id": 17,
      "portfolio_id": 2,
      "portfolio": "IRA",
      "portfolio_abbrev": "IRA",
      "symbol": "CRDO",
      "company_name": "Credo Technology Group Holding Ltd",
      "sector": "Technology",
      "option_type": "PUT",
      "abbrev": "CRDO27-10-15 190p",
      "abbrev2": null,
      "strike": 190.0,
      "strike2": null,
      "expiration": "2027-10-15",
      "quantity": 2,
      "qty": 2,
      "long_shares": null,
      "long_cost": null,
      "price": 214.55,
      "price_session": null,
      "price_age_s": 41.2,
      "price_extended": false,
      "opt_str": "38.10",
      "theta_dollars": 9.84,
      "theta_str": "$10",
      "theta_norm": 2.6,
      "delta": 0.312,
      "margin": 38.0,
      "itm": false,
      "itm_amount": null,
      "time_premium": null,
      "is_stock_row": false,
      "is_profitable": false,
      "after_earnings": true,
      "earnings_date": "2026-09-02",
      "bg": "#FFB347",
      "fg": "#000000"
    }
  ]
}
```

### Top level

| Field | Type | Meaning |
|---|---|---|
| `generated_at` | string | ISO-8601 timestamp (server local time, with offset) when the document was built. |
| `version` | string | MarginWatch version. |
| `summary` | object | Margin/theta totals for the whole book plus one entry per portfolio — see below. |
| `weekly_summary` | object[] | Positions rolled up by expiration week, soonest first — see below. |
| `fetch_errors` | string[] | One human-readable line per symbol whose market data could not be fetched, e.g. `"XYZ: option data unavailable from yahoo"`. Empty when everything loaded. Positions with a failed fetch still appear, with `null` price/greek fields. |
| `positions` | object[] | One entry per open position, in the requested sort order. |

### `summary`

All money values are **in thousands of dollars ($k)** except `total_theta`.

| Field | Type | Meaning |
|---|---|---|
| `total_margin` | number | Sum of `positions[].margin` across **all portfolios** — total margin/capital in use, $k, one decimal. |
| `avail_margin` | number | Margin still available across all portfolios, $k: Σ over portfolios of `max_margin/1000 × multiplier`, minus `total_margin`. Negative means over-extended. |
| `total_theta` | integer | Sum of `positions[].theta_dollars`, rounded — expected daily theta income in **dollars**. |
| `portfolios` | object[] | One entry per portfolio (creation order) — see below. |

### `summary.portfolios[]`

Positions live in up to 10 named portfolios, each with its own margin basis and
multiplier (managed in the UI's Configuration card). Exactly one is the default —
where new positions land.

| Field | Type | Meaning |
|---|---|---|
| `id` | integer | Portfolio id (stable). |
| `name` | string | Portfolio name; unique ignoring case. |
| `abbrev` | string | First three characters of `name` — the tag shown in the UI's `Pf` column and summary rows. |
| `is_default` | boolean | The default portfolio (exactly one is `true`). |
| `max_margin` | integer | Margin basis in dollars. |
| `multiplier` | number | Margin multiplier, 0.5–4.0. |
| `position_count` | integer | Open positions in this portfolio. |
| `total_margin` | number | Sum of this portfolio's `positions[].margin`, $k, 1 dp. |
| `avail_margin` | number | `max_margin/1000 × multiplier − total_margin`, $k, 1 dp. |

### `weekly_summary[]`

One entry per expiration week that has at least one position, ordered soonest
first. A position is bucketed by the **Friday of the ISO week** its `expiration`
falls in, so a Thursday expiry (holiday week) lands with the rest of that week.
Uncovered stock rows (`is_stock_row` with `strike == 0`) never expire and are not
counted. Already-expired positions still appear, under their (past) week.

| Field | Type | Meaning |
|---|---|---|
| `week_ending` | string | ISO date (`yyyy-mm-dd`) of the bucket's Friday — use this for sorting/comparison. |
| `week_label` | string | Short display label for that Friday, e.g. `"Aug 21"` (month abbreviation + day, no leading zero). |
| `position_count` | integer | Number of positions expiring that week. |
| `total_margin` | number | Sum of those positions' `margin`, **$k**, 1 dp. |
| `itm_count` | integer | How many of them currently have `itm == true`. |

### `positions[]`

#### Identity / what the position is

| Field | Type | Meaning |
|---|---|---|
| `id` | integer | Database row id — stable for the life of the position. |
| `portfolio_id` | integer | Which portfolio the position belongs to (`summary.portfolios[].id`). |
| `portfolio` | string | That portfolio's name. |
| `portfolio_abbrev` | string | Its three-character tag. |
| `symbol` | string | Underlying ticker. |
| `company_name` | string \| null | Company name from the data provider. `null` until fetched. |
| `sector` | string \| null | Sector, e.g. `"Technology"`. `null` if unknown / not yet fetched. |
| `option_type` | string | One of `STOCK`, `CALL`, `PUT`, `CALL_SPREAD`, `PUT_SPREAD`, `STRADDLE`. All option positions are **short** (written) — a `CALL` row is a naked short call, a `STOCK` row with a non-zero `strike` is a covered call, spreads are short the `strike` leg and long the `strike2` leg, `STRADDLE` is short a call at `strike` and a put at `strike2` (a strangle when they differ). |
| `abbrev` | string | The one-line label shown in the UI's Position column, e.g. `"CRDO27-10-15 190p"`, `"APLD (no cover)"`, `"NVDA26-09-18 120/130c"`. Format: `SYMBOL` + `yy-mm-dd` expiry + space + strike(s) + `c`/`p`. |
| `abbrev2` | string \| null | Second display line for two-leg positions (spreads, straddles) — the other leg in the same format. For spreads `abbrev` is the short leg and `abbrev2` the long leg when it's a credit spread (and vice-versa for a debit spread); for straddles `abbrev` is the call leg and `abbrev2` the put leg. `null` for single-leg rows. |
| `strike` | number | Short strike. `0.0` for a stock position with no covered call. |
| `strike2` | number \| null | Second strike: long/protective leg for spreads, put strike for straddles. `null` otherwise. |
| `expiration` | string \| null | ISO date `yyyy-mm-dd` of the short option. `null` when there is no option (uncovered stock). |
| `quantity` | integer | Contracts written (or, for uncovered stock, the raw quantity stored). |
| `qty` | integer | Quantity as displayed: contracts for options / covered calls; **lots of 100 shares** (`long_shares ÷ 100`) for uncovered stock. |
| `long_shares` | integer \| null | Shares held (stock rows only). |
| `long_cost` | number \| null | Cost basis per share (stock rows only). |
| `is_stock_row` | boolean | `true` when `option_type == "STOCK"` (covered or not). |

#### Live market data

| Field | Type | Meaning |
|---|---|---|
| `price` | number \| null | Last price of the underlying, 2 dp. `null` if not fetched yet or fetch failed. |
| `price_session` | `"pre"` \| `"post"` \| null | Set when `price` came from extended-hours trading (`"post"` also covers weekend/overnight quotes); `null` for a regular-session price. |
| `price_age_s` | number \| null | Seconds since `price` was fetched from the provider (an age, not a timestamp, so clocks needn't agree). |
| `price_extended` | boolean \| null | Whether extended-hours mode was on when the price was fetched. |
| `opt_str` | string | Option premium per share as displayed in the `$/shr` column: `"38.10"`; net of both legs for spreads/straddles. `"—"` when unknown, `"1,000+"` when the model produced an implausibly large value, `"n/a"` when it is absurd. This is a *string* — parse it only if it looks like a number. |
| `theta_dollars` | number \| null | Expected daily theta gain for the whole position in **dollars** (`−theta × 100 × quantity`, net of the long leg for spreads). Positive = you earn it. `null` if greeks unavailable. |
| `theta_str` | string | `theta_dollars` formatted, e.g. `"$10"`; `"—"` when unavailable. |
| `theta_norm` | number \| null | Theta per $10k of margin: `theta_dollars / margin × 10`, 1 dp — the UI's `θ/10k` column, a capital-efficiency measure. `null` when theta unknown or margin is 0. |
| `delta` | number \| null | **Probability of assignment**: absolute American delta of the short leg, 0–1, 3 dp (worst leg for straddles). `null` for uncovered stock or when unavailable. The UI colours it: ≥0.85 red "Deep ITM", ≥0.65 orange "Mod ITM", ≥0.45 yellow "ATM", ≥0.25 lime "Slight OTM", ≥0.10 green "OTM", else blue "Deep OTM". |

#### Risk / status flags

| Field | Type | Meaning |
|---|---|---|
| `margin` | number | Margin (or capital) tied up by this position, **$k**, 1 dp. Stock: `long_shares × long_cost / 1000`. Naked call & straddle: `strike × 50% × qty × 100 / 1000`. Naked put: `strike × qty × 100 / 1000`. Credit spread: `|strike2 − strike| × qty × 100 / 1000`. Debit spread: `0`. |
| `itm` | boolean | Short leg is in the money at the current `price` (`false` if price unknown). |
| `itm_amount` | number \| null | Dollars per share the short leg is ITM (intrinsic value); `null` when OTM or price unknown. |
| `time_premium` | number \| null | Extrinsic (time) value per share of the ITM leg = leg premium − `itm_amount`. Only present when ITM and the premium is plausible; may be slightly negative for deep-ITM American options. |
| `is_profitable` | boolean | Stock rows only: `price > long_cost`. Always `false` for options. |
| `after_earnings` | boolean | An earnings report falls strictly after today and on/before `expiration` — i.e. the position carries earnings risk. |
| `earnings_date` | string \| null | ISO date of that earnings report; only populated when `after_earnings` is `true`. |
| `bg` | string | Row background colour hex used by the UI, keyed to days-to-expiry: `#5C5A97` expired (<0 d), `#A5CDAA` ≤6 d, `#F2E1A9` ≤13 d, `#ffc8c8` ≤20 d, `#ADD8E6` ≤27 d, `#FFB347` further out (and always for uncovered stock). |
| `fg` | string | `#000000` or `#ffffff` — a readable text colour for `bg`. |

## Notes for consumers

- Treat every market-data field (`price`, `delta`, `theta_dollars`, `opt_str`, `sector`,
  `company_name`, …) as nullable. Right after the server starts, or when a provider is
  down, positions are returned with those fields `null` and the failure listed in
  `fetch_errors`.
- `margin`, `total_margin` and `avail_margin` are in **thousands of dollars**;
  `theta_dollars` and `total_theta` are in **dollars**.
- Poll no more often than the data changes — once a minute is plenty. Prices are
  cached server-side with a TTL, so a faster poll just returns the same numbers.
- The endpoint is read-only. Position management (add/edit/delete) is only available
  through the session-authenticated UI endpoints.

## Glance example

```yaml
- type: custom-api
  title: MarginWatch
  cache: 1m
  url: https://margin.example.com/api/snapshot?cached=1&warm=1
  headers:
    Authorization: Bearer ${MARGIN_PWD}
  template: |
    <p>Margin used <b>{{ .JSON.Float "summary.total_margin" | printf "%.1fk" }}</b>
       · avail <b>{{ .JSON.Float "summary.avail_margin" | printf "%.1fk" }}</b>
       · θ/day <b>${{ .JSON.Int "summary.total_theta" }}</b></p>
    <p>{{ range .JSON.Array "summary.portfolios" }}
       {{ .String "name" }}: {{ .Float "total_margin" | printf "%.1fk" }} used / {{ .Float "avail_margin" | printf "%.1fk" }} avail &nbsp;
    {{ end }}</p>
    <p>{{ range .JSON.Array "weekly_summary" }}
       {{ .String "week_label" }}: {{ .Int "position_count" }} pos / {{ .Float "total_margin" | printf "%.1fk" }}{{ if gt (.Int "itm_count") 0 }} ({{ .Int "itm_count" }} ITM){{ end }} &nbsp;
    {{ end }}</p>
    <ul class="list list-gap-4">
    {{ range .JSON.Array "positions" }}
      <li>[{{ .String "portfolio_abbrev" }}] {{ .String "abbrev" }} — {{ .String "opt_str" }}
          {{ if .Bool "itm" }}<span class="color-negative">ITM</span>{{ end }}</li>
    {{ end }}
    </ul>
```
