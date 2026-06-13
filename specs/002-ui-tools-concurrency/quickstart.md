# Quickstart: Validating 002 — UI, Tools & Single-Run Concurrency

**Feature**: 002-ui-tools-concurrency | **Date**: 2026-06-13 | **Spec**: [spec.md](spec.md)

Manual + automated validation for each user story. Prereqs: LM Studio running locally with at least one
model available; the controller launched via the tray.

```powershell
python -m venv venv
venv\Scripts\Activate.ps1            # activate before ANY terminal command (repo rule)
pip install -e ".[dev]"
lmstudio                             # launch controller; tray opens the web UI
pytest                               # run the full suite (unit + integration + contract)
```

---

## US1 — Professional, live-updating, responsive UI (P1)

1. Open the control panel from the tray.
2. **Width**: confirm the content area spans ≈90vw with small side gutters at several window widths
   (narrow window → wide monitor); there is no narrow fixed column and no horizontal scrollbar
   (SC-002, FR-002/FR-003).
3. **Responsiveness**: shrink the window; confirm the top nav collapses to a compact menu and the run
   indicator + primary actions stay reachable (FR-003, Edge Case "very small viewport").
4. **Live load**: click "Load model". Confirm a non-blocking progress indicator appears immediately and
   the control is marked busy (FR-004); then the status transitions to "ready" (or shows an error)
   **without manually reloading the page** (FR-005, SC-001).
5. **Theme**: switch dark / light / system; confirm every page renders consistently (FR-006).
6. **Recovery**: stop and restart LM Studio (or briefly drop the network) to break `/ws/status`;
   confirm the UI reconnects and shows the true current status, not a stuck "loading" (FR-007).
7. **Consistency**: visit every page (Sessions, Automations, Skills & Tools, Settings); confirm a
   uniform visual system — no unstyled/broken page (SC-003).

## US2 — Default toolset with file-aware read/edit (P1)

Start a session in the workspace and prompt the agent to exercise each tool. Verify via the transcript:

1. **find**: locate files by glob (e.g., `**/*.py`) — returns paths (FR-012).
2. **grep**: search for a string across files — returns matches (FR-012).
3. **read_file (range)**: read only lines N–M of a file, not the whole file (FR-009).
4. **edit (line-range)**: replace lines N–M with new content; rest of file unchanged.
5. **edit (exact-string)**: replace a unique snippet; confirm an ambiguous/missing target **fails** and
   leaves the file unchanged (FR-010, Edge Cases, SC-004).
6. **write_file**: create a new nested file; parent folders are created (FR-011).
7. **list_dir**: list a directory's entries (FR-013).
8. **powershell**: run e.g. `Get-ChildItem`; output is returned and bounded; a long/hanging command is
   timed out and truncated (FR-014, Edge Cases).
9. **powershell consent**: have the agent target a path outside the workspace/consented folders;
   confirm the same consent prompt appears and a permanent grant is recorded in Settings (FR-015a).
10. **parallel**: ask for two independent operations at once (e.g., grep + list_dir); confirm the
    `parallel` meta-tool runs them concurrently and returns both results; confirm it is not used for two
    edits to the same file (clarification Q2).
11. **Consent gate**: any file tool targeting an unconsented path is blocked with a clear message
    (FR-015).
12. **Read-before-edit**: confirm the agent reads the relevant section before editing (FR-016).

Automated: `pytest tests/unit/test_file_tools.py tests/unit/test_shell_tool.py
tests/unit/test_parallel_tool.py`.

## US3 — Single-run concurrency with visible run & queue surface (P1)

1. Start a session; confirm the **top-right indicator** shows it as the active run with its
   type/status (FR-021).
2. Click the indicator; confirm it **navigates to the running session view** with full controls (stop,
   steer, queue-message, transcript) (FR-022).
3. While it runs, start a second session and trigger an automation; confirm neither starts immediately —
   exactly one run stays active (FR-018/FR-019, SC-005).
4. Open the run surface; confirm a **collapsible queue panel** lists the waiting items in FIFO order with
   type/label (FR-023, SC-006).
5. Let the active run finish; confirm the next queued item starts automatically and the surfaces update
   live (FR-020, FR-024).
6. Drain the queue; confirm the queue panel disappears and the indicator shows idle (FR-023, FR-007).
7. **Cancel**: queue an item and remove it before it starts; confirm it drops without affecting the
   active run (FR-025).
8. **Persistence**: with items queued, quit and relaunch the app; confirm the queue is restored and
   resumes (FR-025a). For an automation run that is an automation, confirm clicking the indicator opens
   its session and shows the automation definition with an edit affordance (FR-022).

Automated: `pytest tests/unit/test_queue.py` (persist/restore/resume).

## US4 — Per-run configuration for sessions and automations (P2)

1. **Session run config**: start a session and open the run config; choose a non-default model, disable a
   globally-enabled tool, and enable a globally-disabled tool (FR-026/FR-028).
2. Confirm the run uses the chosen model and exactly the overridden tool set (SC-007/SC-008).
3. After the run, confirm the **global tool configuration is unchanged** (FR-028, SC-007).
4. **MCP selection**: select a subset of MCP servers for the run; confirm only those are active, and a
   de-selected server is unaffected globally (FR-030).
5. **Precedence**: keep an MCP server active but disable one tool it provides via a per-tool override;
   confirm the server's other tools remain available and only that tool is off (FR-030a, most-granular-
   wins).
6. **Session persistence**: confirm per-session choices persist for the session until a follow-up that
   starts a new run supplies a new config (FR-030b).
7. **Automation run config**: create an automation with its own model + tool overrides + MCP selection;
   confirm each scheduled run applies it (FR-027, SC-008).
8. **Defaults**: start a run with no config; confirm default model + global tools + global MCP (FR-032).
9. **Skills**: confirm skills are **not** shown as per-run toggles and remain available to every run
   (FR-031).
10. **Stale reference**: point a saved automation config at a removed tool/MCP; confirm the run proceeds
    with valid capabilities and notes the missing reference (FR-033, Edge Cases).

Automated: `pytest tests/unit/test_run_config.py tests/integration/test_run_config_flow.py
tests/contract/test_sessions_contract.py`.

---

## Definition of done

- All four user stories validate per the steps above.
- `pytest` passes (existing 45 tests + new unit/integration/contract tests for tools, run config, and
  persisted queue).
- ARCHITECTURE.md + AGENTS.md updated for the new tools, the run/queue surface, the persisted queue, and
  per-run config (Constitution VI).
- No idle polling introduced; UI updates are push-driven over `/ws/status` (Constitution V).
