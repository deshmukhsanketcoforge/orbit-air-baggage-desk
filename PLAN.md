# PLAN: Orbit Air Baggage Desk

## 1. The problem

Orbit Air keeps its lost, delayed and damaged baggage claims in spreadsheets and email threads. Nobody can see at a glance which claims are waiting, who owns them, or which ones are about to miss their response deadline, so deadlines get missed.

The solution is a command line claims desk backed by SQLite. It lets a desk agent file a claim, assign it, move it through its life, and escalate claims that have run past their deadline. Every status change is recorded so the history of a claim can always be read back.

## 2. Deadline rules and allowed status changes

**Deadlines.** A claim's deadline is its filed time plus a number of hours that depends on its tier: gold 4 hours, silver 24 hours, standard 72 hours.

**Overdue.** A claim is overdue only when the current time is strictly later than its deadline. At exactly the deadline it is not overdue. One minute after the deadline it is overdue. The current time is always passed into the code as a parameter. No rule code reads the real clock, and the `--now` option on the command line sets the current time to a chosen value.

**Status changes.** A new claim starts as `open`. These are the only allowed changes:
- From `open`, a claim can become `assigned` or `escalated`.
- From `assigned`, it can become `resolved` or `escalated`.
- From `escalated`, it can become `assigned` or `resolved`.
- From `resolved`, it can only become `closed`.
- From `closed`, nothing is allowed.

Any other change is rejected with a clear error and nothing is saved.

**Escalation.** Only `open` and `assigned` claims can be escalated, and only when they are overdue. Resolved claims, closed claims and claims that are not yet overdue are left alone. The escalate command reports which claim ids it escalated.

**Validation.** A passenger name and a flight number cannot be blank. The claim type must be one of `delayed`, `lost`, `damaged`, and the tier must be one of `standard`, `silver`, `gold`. Anything else is rejected with a clear error.

## 3. The two tables

**`claims`**
- `id`: integer primary key
- `passenger`: text, required, not blank
- `flight`: text, required, not blank
- `type`: text, CHECK it is one of `delayed`, `lost`, `damaged`
- `tier`: text, CHECK it is one of `standard`, `silver`, `gold`
- `status`: text, CHECK it is one of `open`, `assigned`, `escalated`, `resolved`, `closed`; starts as `open`
- `assigned_agent`: text, empty until the claim is assigned
- `note`: free text, optional
- `filed_at`: ISO timestamp, the time the claim was filed
- `deadline`: ISO timestamp, `filed_at` plus the tier's hours

**`history`**
- `id`: integer primary key
- `claim_id`: integer, points to `claims.id`
- `old_status`: text, empty for the first entry made when a claim is filed
- `new_status`: text, required
- `changed_by`: text, who made the change
- `changed_at`: ISO timestamp, when the change was made

A claim change and its history entry are saved in one transaction. If the history entry cannot be saved, the claim must not change.

## 4. Branches and planned tests

The work is split across four branches, each merged by its own pull request whose title starts with the branch name. On each branch the tests are written first, run and seen to fail, and committed alone. Only then does the agent write the feature code. The agent does not edit a test file.

**`feature/schema-and-filing`** covers the tables, filing a claim, validation, the deadline by tier, and list with filters. Planned tests (at least 5):
1. Filing a valid claim saves it as `open` with a deadline.
2. A blank passenger is rejected.
3. A blank flight is rejected.
4. An unknown type or tier is rejected.
5. The deadline is 4, 24 and 72 hours after the filed time for gold, silver and standard.
6. List filters by status and by tier.
7. Inserting a bad type, tier or status directly into the database hits a CHECK constraint.

**`feature/assign-and-status`** covers assigning, resolving, closing, the allowed changes, and history entries. Planned tests (at least 6):
1. Every allowed change in the table works.
2. At least three disallowed changes are rejected with a clear error, such as `open` to `closed`, `open` to `resolved`, and anything out of `closed`.
3. Assigning sets the agent and writes a history entry.
4. Resolving works from `assigned` and from `escalated`.
5. Closing works only from `resolved`.
6. Filing writes the first history entry.
7. Every history entry has an old status, a new status, who and when.

**`feature/deadlines-and-escalation`** covers the overdue rule, escalation, escalation history, and the rollback test. Planned tests (at least 6):
1. One minute before the deadline is not overdue.
2. Exactly at the deadline is not overdue.
3. One minute after the deadline is overdue.
4. Escalation moves overdue `open` and `assigned` claims to `escalated`.
5. Resolved, closed and not yet overdue claims are left alone.
6. Escalation writes a history entry for each claim and returns the escalated ids.
7. Rollback: the history write is made to fail on purpose, and the claim status must be unchanged.

**`feature/cli`** covers all eight commands (`file`, `list`, `assign`, `resolve`, `close`, `escalate`, `history`, `seed`), the `--now` option, and the seed command. Planned tests (at least 3):
1. `file` then `list` works through the command line.
2. `--now` before the command makes `escalate` run at that time and print the escalated ids.
3. `seed seed_claims.csv` loads every row without an error.

That is at least 20 tests across the four branches, and all of them must pass.

## 5. Risks and how each is handled

**Risk 1: tests that depend on the clock.** A deadline test could pass today and fail tomorrow if the code reads the real time. The current time is passed into every function that needs it, so a test can use a fixed time, and `main.py` is the only place that picks the real clock or `--now`. `claims.py` and `db.py` are searched for any clock call.

**Risk 2: a claim changes but its history entry does not get saved.** That would leave a change with no record. The claim change and the history entry are saved in one transaction, and a rollback test makes the history write fail. The transaction is also removed temporarily to confirm the rollback test fails without it.

**Risk 3: the agent edits a test to make it pass.** Then the tests no longer describe the rules. `CLAUDE.md` says the tests are the spec and the agent must stop and ask. After each agent run, `git diff` on the tests folder is checked. Any change is undone unless it can be explained.

**Risk 4: a secret leaks, or the agent follows an instruction hidden in a claim note.** The seed file will include a note that tries to make the agent print `.env`. `.env` is ignored by Git and has never been committed, `.claude/settings.json` denies reading it, and claim notes are treated as data, never as instructions. What the agent does with that note is tested and recorded in the README.
