# frontend

Mobile-first web app for OnYourTips: React 19 + Vite + TypeScript, no other runtime
dependencies. It's served by the FastAPI backend in production (`oyt serve`) and can be added to
your phone's home screen.

## Screens

| Tab | What it shows | API |
|---|---|---|
| Today | Recency-ranked feed, type and topic filters, refresh sources | `GET /api/feed`, `/api/topics`, `POST /api/ingest` |
| Week | Best of the last 7 days, grouped by type | `GET /api/digest` |
| Saved | Your reading list | `GET /api/saved` |
| You | Interest profile, collection stats, failing sources | `GET /api/interests`, `/api/status` |
| Article (overlay) | Summary first, topics, every source + discussion, rate, save, hide | `GET /api/articles/{id}`, `POST …/interactions` |

## Commands

```bash
npm install
npm run dev        # http://localhost:5173 and http://<your-ip>:5173, proxies /api to :8000
npm run build      # type-check + build to dist/ (served by `oyt serve`)
npm run typecheck
```

## Structure

```
src/
  api/          types.ts (mirrors backend models) · client.ts (typed fetch wrapper)
  lib/          AppContext (source names, topic labels, optimistic interaction state, toasts)
                router.ts (hash routes: #/, #/week, #/saved, #/you, #/a/<id>) · format.ts · useAsync.ts
  components/   ItemRow (feed row with heat strip) · ArticleView · RefreshButton · common
  pages/        TodayPage · WeekPage · SavedPage · YouPage
  styles.css    design tokens + all styles (light and dark)
```

## Design notes

**Palette** (from the design brief): pastel violet `#C4C3E3` (call-to-action strips, research),
dark violet `#504E76` (secondary text, toasts), light beige `#FDF8E2` (highlight cards),
calm green `#A3B565` (tab bar, success, projects), light orange `#FCDD9D` (selected states,
primary buttons), saturated orange `#F1642E` (Hot badge, stars). Text and outlines use a deep ink
derived from the dark violet. Dark mode keeps the pastels on a deep-violet background.
All colours are tokens at the top of `styles.css`.

**Style:** Poppins throughout; outlined cards with large radii and soft shadows; bottom sheets
with a grab handle; a floating green tab bar with a yellow pill on the active tab.

**Making it intuitive:**
- Every action is labelled: *Read summary*, *Save*, *Hide*, *Read article*.
- Search on Today; quick type chips; a **Filters** sheet (time range + topics) that applies only
  when you tap *Show stories*, with a badge counting active filters.
- A three-step **getting-started** card that ticks off as you go (collect stories, save one,
  open Week) and can be dismissed.
- Sources appear as coloured avatars (HN orange, GitHub violet, …). A story picked up by several
  sources shows them stacked.
- **Hot** badge for the most-discussed stories on screen; saved cards get an orange outline.
- Week opens with a **Top 3 podium**; each tab remembers its own scroll position.
- Ratings are five stars with a word for each ("Useful", "Loved it").
- Interactions update the UI immediately and roll back if the server rejects them.
- Next: generate `api/types.ts` from `/openapi.json` (openapi-typescript); offline cache via a
  service worker once the app is served over HTTPS.
