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

---

## cv-tailor v2 — Pnina sends directly

**What:** Allow Pnina (`whatsapp:972502506169@s.whatsapp.net`, already in `users`) to send messages directly into the CV-Bot group. v1 is single-sender (Sapir only — the spec at `groups/cv-tailor/CLAUDE.local.md:50` says "any message in this group is from the owner").

**Why:** v1 makes Sapir the relay between Pnina and the bot. v2 closes the actual user loop — Pnina forwards a job link, gets a tailored CV, applies, no Sapir bottleneck.

**When to build:** After v1 has run cleanly for ~1 week and Pnina has confirmed the CV-Bot output quality is right.

**How (likely scope, may shift):**
- Flip `unknown_sender_policy` on the CV-Bot messaging group from `strict` to `dropped_messages_log` so any third-party messages are logged (not silently dropped) before Pnina is officially added.
- Add Pnina as a member: `ncl members add --agent-group-id <cv-tailor-id> --user-id whatsapp:972502506169@s.whatsapp.net`.
- Introduce a `sender_role` distinction in `CLAUDE.local.md` (owner = Sapir, candidate = Pnina). Owner-only commands (`עדכן קו"ח`, `עדכן סטטוס`, `מה הגשתי השבוע`) gate on `sender_role = owner`.
- Decide who can confirm a brief: candidate-only? owner-only? either? The spec's two-round flow assumes the same person submits + confirms.
- **Schema risk (flagged by outside voice during eng review):** v2 likely needs per-sender session state in `messaging_group_agents`. `session_mode = shared` in v1 collapses both senders into one session, which is fine when one sender owns the flow but breaks when both can submit. Probably needs a new `session_mode = per_sender` value + agent-runner support. Not just a `sender_role` field.

**Depends on:** v1 shipped and stable for ~1 week. Sapir's confirmation that the CV-Bot output quality is good enough that Pnina would actually use it.

**Source:** /office-hours design doc `~/.gstack/projects/sapirgolan-nanoclaw/nanoclaw-main-design-20260524-170753.md` + eng review outside voice (Claude subagent) finding #5.

---

## cv-tailor — Retroactive application history backfill

**What:** Write a one-shot Python script that reads `/home/nanoclaw/resumes/cv_pnina_*.docx.md` (the ~30 prior application files) and synthesizes tracker entries at `groups/cv-tailor/workspace/applications/`. Each entry needs: company name (guess from filename), role (guess from filename + first H1), original job description (extract from file), tailored CV content (extract), submission date (file mtime), status (default `submitted`, owner can update later).

**Why:** Without backfill, the spec's duplicate-detection feature ("`כבר הגשנו ל-<company>`") returns "no" for every pre-v1 application. The bot looks broken to the user until enough new submissions accumulate.

**When to build:** After v1 is wired up and the tracker format is confirmed working. Low priority — duplicate-detection is a nice-to-have, not a v1-blocker.

**How:**
- Script: `groups/cv-tailor/backfill-from-resumes.py`
- Glob `/home/nanoclaw/resumes/cv_pnina_*.docx.md`
- For each file, parse filename (`cv_pnina_<company>_<role>.docx.md`) to derive `company:` and `role:` YAML fields.
- Use file mtime as `created:` and `last_updated:`.
- Default `status: submitted` for everything.
- Extract the tailored CV content into `## Tailored CV` section.
- `source_url: ""` (unknown for backfill entries).
- Write one tracker file per source.

**Failure modes:** Some filenames may be ambiguous (no clear `company` segment). For those, write the entry with `company: "UNKNOWN_<filename>"` and let owner fix manually.

**Depends on:** v1 shipped, tracker format confirmed in production by at least one real submission.

**Source:** Eng review TODO scan; design doc open question #2 (Application tracker retroactive population).

---

## cv-tailor — Post-DOCX email draft (job-application-email integration)

**What:** After cv-tailor sends the tailored `.docx`, offer to draft the application email using the existing `~/.claude/skills/job-application-email` skill. The skill already exists with three Hebrew templates (short/punchy, detailed/professional, personal/energetic).

**Why:** The full "apply to a job" workflow is: see job → tailor CV → write application email → send. cv-tailor v1 covers steps 1-2. Step 3 still requires Sapir to open Claude Code and run the skill manually. Closing this loop makes the bot do the full job, not half.

**When to build:** Start with a fresh `/office-hours` session — the design decision "new agent group vs new behavior inside cv-tailor" is consequential and should be a deliberate scope choice, not improvised.

**How (likely scope, may shift):**
- Option A: extend `groups/cv-tailor/CLAUDE.local.md` with a third round after DOCX send: "want me to draft the email? short / detailed / personal?". Cv-tailor remains a single agent with broader scope.
- Option B: spawn a new agent group `groups/email-drafter/` that watches for `.docx` files dropped into a shared destination by cv-tailor, then drafts the email. Cleaner separation but introduces inter-agent state.
- Migrate the email-drafting skill's templates into either agent's workspace (NanoClaw containers can't reach `~/.claude/skills/`).

**Depends on:** v1 cv-tailor shipped and stable. /office-hours scope decision.

**Source:** Eng review design-doc retrospective; cv-tailor's "What I noticed" section flagged this as a natural follow-on.
