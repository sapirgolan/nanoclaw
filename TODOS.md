# TODOS

Deferred work that was considered during design/review but explicitly not built yet.

---

## Weekly Trend Report (community-monitor)

**What:** Add a Friday 8pm cron to the community-monitor agent that reads the last 5 daily archives and synthesizes week-level trends — which topics are rising, which tools are gaining momentum, what changed this week vs last week.

**Why:** The daily digest gives signal per day; the weekly report gives signal across the week. Community trends (e.g. a new tool gaining traction, a recurring debate) only become visible across multiple days.

**When to build:** After the daily digest cadence runs cleanly for 1–2 weeks. Validate the archive files are being written correctly before building on top of them.

**How:**
- Add `schedule_task` with id `weekly-trend` and recurrence `0 20 * * 5` (Friday 8pm Jerusalem time)
- Prompt: `"WEEKLY: Generate the weekly trend report now."`
- Add `## When you receive a WEEKLY message` section to `groups/community-monitor/CLAUDE.md`
- Agent reads `workspace/archive/*.md` for the last 5 days, synthesizes trends, sends to `ai-briefs`

**Depends on:** Daily digest running for at least 1 week (need archive files to read).
