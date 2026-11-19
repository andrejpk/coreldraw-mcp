# Experiment protocol (CorelDRAW MCP improvement program)

Every experiment agent follows this file. The orchestrating session owns `docs/experiments/backlog.md` and all merges.

## Goals

Agents must use this MCP server reliably and quickly, and get useful feedback, while they clean up complex vector art, build screen-print separations, inspect, analyze, repair, and change colors. The Friday Threads skills are the reference workload: `~/.claude/plugins/cache/ft-tools/friday-threads-production/1.1.0/skills/friday-threads-films/SKILL.md` and its `references/separation-check.md`. The server must stay a general-purpose CorelDRAW MCP. Keep shop rules, such as mesh, choke widths, and ink names, out of the tools.

Simplify where it makes sense. Merge narrow tools into fewer, more flexible ones, and remove tools that are broken or redundant. Do not remove a general capability only because the Friday Threads skills do not use it. Keep `get_coreldraw_status`, `inspect_shapes`, and `export_page_proof_pdf` under their current names, because the skills call them. Other renames are fine when they make the tool set smaller or clearer.

## Hard safety rules

- **Never open, modify, save, or delete anything on `Z:`.** Use only the copies under `C:\CorelMcpLab`.
- Work only on your own fresh copies in `C:\CorelMcpLab\runs\<exp-id>\`. Copy them from `C:\CorelMcpLab\templates\` and `C:\CorelMcpLab\samples\` when you start. Never edit the master copies.
- CorelDRAW is shared and may have other documents open, including a live customer document. Never close, save, or modify a document you did not open from your run folder. Before any mutating call, confirm that the target document's `FullFileName` is under your run folder. Close only your own documents (match by path), and set `Dirty = False` first.
- Reset `app.Optimization`, `EventsEnabled`, and similar settings in `finally`.
- **Shared-instance gate (added after E13 crashed CorelDRAW twice, once with a live customer document open).** Before starting CorelDRAW work, and before every in-process probe session, list the open documents. If any document is open that is not under `C:\CorelMcpLab` (for example anything on `Z:`, or another user's file), do not run probes or mutations: stop and report to the orchestrator. Prefer MCP calls over ad-hoc in-process probes, and avoid untested in-memory geometry APIs (`Curve.WeldWith` crashed `VGCore.dll`). Never relaunch or close CorelDRAW yourself after a crash; report it.
- Only one CorelDRAW COM client may run at a time. Run one MCP server or in-process script at a time. Stop your server before you report.

## Environment

- Your git worktree is `C:\CorelMcpLab\wt\<exp-id>`, on branch `exp/<exp-id>`. Work and commit only there. Do not touch other worktrees or branches, and do not merge.
- Python: `C:\repos\gh\coreldraw-mcp\.venv\Scripts\python.exe` (64-bit; worktrees have no venv of their own).
- Start the server from `<worktree>\server` with `C:\repos\gh\coreldraw-mcp\.venv\Scripts\python.exe server.py --local-http` in the background (port 8765). First check that nothing else listens on 8765; if a server you did not start is running, stop and report rather than killing it. The MCP client is `scripts/validation/mc.py` (`call(name, **args)`).
- In-process scripts run from `server/` with `sys.path.insert(0, ".")` and `core.connection.get_connection().safe_call(fn)`.
- The bridge cancels any single COM operation after 60 s. Chunk long work.
- Batched reads: `Shape.Evaluate("array(@com.X, ...)")` runs many reads in one call; see `server/core/structure.py` (validated page-structure cache: `page_layers`, `read_entries`, `resolve_shapes`, `walk_layer`, `invalidate`) and `server/tools/inspection.py` (`_query`, `_record_query`). Reuse the cache; never walk a page yourself. Pure reads go through `conn.read_call` (interruptible at its deadline between COM calls); edits use `conn.safe_call`. Direct dynamic-dispatch reads cost about 1–25 ms each. pywin32 ignores keyword args to `FindShape(s)`: pass them positionally.
- Document targeting: `server/core/targets.py` (`resolve_document(app, path, activate=True)` for edits, because `Document.Undo` acts on the active document) and `server/core/units.py`.
- Findings from earlier work: `docs/requirements/2026-11-16-screen-separation-tools.md`, section "Fixture validation, 2026-11-17". Fixture catalog: `C:\CorelMcpLab\templates\MANIFEST.md`.

## Conventions

- Follow `CLAUDE.md`/`AGENTS.md`. Tools are bare functions registered in `server/server.py`, return `ToolResult`, and use their docstring as the tool description. No `noqa` and no lint suppression in server code.
- Remove or replace tools you supersede; do not leave old and new versions side by side. Update `README.md` tool tables if they list the tools you change.
- Prefer exact targeting (document path, page, StaticIDs) and verified read-back over trusting a COM call.
- Mutating tools should be one undo step (`BeginCommandGroup`/`EndCommandGroup`), should verify their result, and should return enough state that the calling agent can confirm the change without another call.

## Done criteria for each experiment

1. `ruff check server/` must add no errors beyond the baseline of 31 errors on `claude/experiments-integration`. Compare against the baseline.
2. `python server.py --check` passes.
3. Keep the existing validation passing: `scripts/validation/build_accept.py` plus `accept_mcp.py` (37 checks; it renders layer proofs with Ghostscript at `C:\Program Files\gs\gs10.08.0\bin\gswin64c.exe`, and `server/render_separation_proof.py` renders per-plate PNGs plus a manifest) and `accept_failure.py`. Update them when you rename or merge tools, and add checks for new behavior. Run them against fixtures in your run folder.
4. Measure before and after: timings, tool counts, round trips, or success on the real sample copies, as relevant. Always report the catalog size from `scripts/validation/catalog_size.py` (tools, chars, approximate tokens). The E01 audit scripts (`scripts/validation/e01/`, `audit_lib.py`, `audit_manifest.py`) and their logged numbers in `docs/experiments/results/e01.md` are the benchmark for agent-facing improvements.
5. Commit on `exp/<exp-id>` with a clear message. Write `docs/experiments/results/<exp-id>.md` with: hypothesis, what changed (tools added/removed/renamed), measurements, validation results, outcome (success / partial / failure), risks, and follow-up ideas.
6. Close your documents, stop your server and every background task or monitor you started (do not leave anything running), and report back in under 300 words: outcome, branch, commit, tool count before and after, key measurements, and anything the orchestrator must know before merging.
