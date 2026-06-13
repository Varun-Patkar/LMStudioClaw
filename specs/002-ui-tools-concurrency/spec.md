# Feature Specification: Professional UI, Default Agent Toolset & Single-Run Concurrency

**Feature Branch**: `002-ui-tools-concurrency`

**Created**: 2026-06-13

**Status**: Draft

**Input**: User description: "1. Overhaul the UI to be responsive, auto-loading and generally better (currently it looks like a CS student's school project; needs to be professional, sleek, responsive). After clicking 'load model' I must physically reload the page to see status — no loader. Use 90vw for content (5vw left/right margins); max-width 1100px is outrageous. 2. The default tools should be: read, powershell, edit, write, grep, find, ls, multi_tool_use.parallel. read/write must support operating on a part of a file with replace; the agent should read before editing to place changes correctly; each tool must be easily usable in every scenario. 3. No two sessions/automations/hybrid may run at once. Add a top-right indicator showing what is currently running; clicking it opens detail. A new session/automation cannot start until the current one ends — they queue; show the queue below in a collapsible compartment only if present. 4. Each session and automation can be started (for sessions, on follow-up) with a config: model to use, tools config (enable/disable, independent of the global config — globally enabled may be disabled for this run), MCP config. Skills are globally defined (no per-run disable since they are only invoked when needed and don't consume tokens at idle)."

## Overview

This feature improves three pillars of the existing call-based LM Studio agent runtime (feature `001-agent-runtime`) without changing its core lifecycle (no model at idle; load-run-unload; single active run):

1. **A professional, responsive web UI** that updates live (no manual page reloads) and uses a fluid full-width layout instead of a narrow fixed column.
2. **A richer default agent toolset** (read, powershell, edit, write, grep, find, ls, parallel tool execution) with range-aware read and precise in-place edits, so the agent can work with files reliably.
3. **Enforced single-run concurrency with a visible run/queue surface**, so users always see what is running and what is waiting, and runs are serialized in a FIFO queue.
4. **Per-run configuration** (model, per-run tool enable/disable overrides, per-run MCP selection) for both sessions and automations, with skills always globally available.

Where a capability already exists in the codebase, this spec preserves it and only refines the gaps; where it is missing, it is added. This document describes WHAT the system does and WHY, independent of implementation technology.

## Clarifications

### Session 2026-06-13

- Q: The user wrote "if present don't change but if not present please add." How should existing behavior be treated? → A: Treat each item as a desired end-state. Preserve any already-correct behavior (e.g., the single-active FIFO queue already exists in the runtime) and only add or fix what is missing (UI surfacing of that queue, live status, the expanded toolset, per-run config overrides).
- Q: What does "auto-loading" mean for model loading? → A: After the user triggers a model load (or starts a session/run), the UI MUST reflect status changes live (loading → ready/generating → unloaded, plus errors) without the user manually refreshing the page. A non-blocking progress/loading indicator MUST be shown while the action is in flight.
- Q: How granular must per-run tool overrides be relative to the global tool configuration? → A: Per-run overrides are independent of the global enabled/disabled state. A run config may disable a globally enabled tool and may enable a tool that is globally disabled, scoped only to that run. The global configuration is unchanged by a per-run override. Skills are exempt — they are always globally available and cannot be toggled per run.
- Q: How does the powershell tool relate to the consent/path gate? → A: PowerShell uses the same consent model as the file tools. It starts in the workspace and may access folders already consented; the secrets store and app internals are always denied. If a command needs a path outside currently consented folders, the same consent prompt is raised (session/permanent), and a permanent grant is persisted as a user-consented path in Settings.
- Q: What is the "parallel" tool — native parallel tool-calling or an explicit meta-tool? → A: An explicit meta-tool. The agent calls a dedicated `parallel` tool that takes a list of two or more independent sub-tool-calls and runs them concurrently, returning combined results. It is only for independent operations; concurrent operations on the same target (e.g., two edits to the same file) are not supported and must not be parallelized.
- Q: What controls does the active-run surface offer when the top-right indicator is clicked? → A: It is a quick-navigate to whatever is running — clicking opens the running session view (an automation run also has its own session), where the full controls live (stop, steer, queue-message, transcript). For an automation run, the session view also shows the automation's definition alongside, with the ability to edit that automation (also editable from the Automations list).
- Q: How does the edit tool target the section to replace? → A: The edit tool is overloaded and supports both modes: (1) exact-string find-and-replace (provide the exact existing text and its replacement; must match exactly once or fail) and (2) line-range replace (provide start/end line numbers and the new content). Line-range is more token-efficient for whole-line/block replacements; exact-string handles sub-line or cross-line spans (e.g., from a mid-line position on one line to a mid-line position on another). The agent chooses whichever fits the edit.
- Q: How should the existing built-in tool names (read_file, list_dir, write_file) relate to the requested set (read, ls, write, …)? → A: Keep one consistent toolset that favors clear, descriptive names. Reuse the existing descriptive names where a tool already exists (read_file, list_dir, write_file) and add the missing tools (edit, grep, find, powershell, and the parallel meta-tool) with equally descriptive names. The requested short names (read/ls/write/find/…) describe the capabilities; tool identities may be the more descriptive forms. This is not about backward-compatibility aliases — there is a single set of well-named tools.
- Q: Does the run queue survive an app/PC restart? → A: Yes — the queue is fully persisted. Both manual sessions and automation runs that were queued (and an interrupted in-progress run) are saved and resumed on next startup, continuing where they left off, so no queued work is silently lost.
- Q: When per-run tool overrides and per-run MCP selection overlap, what wins? → A: The most granular (lowest-level) decision has the highest weight. MCP selection first decides which servers are active for the run, then per-tool enable/disable overrides apply on top of the resulting tool set (built-in + MCP). De-selecting an MCP for a run does not disable it globally — it is just not used for that run. By default a run uses the global settings; the user may change them per session, and those choices persist for that session until changed by a follow-up.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - A professional, live-updating, responsive UI (Priority: P1)

A user opens the control panel from the tray. The interface looks polished and modern (clear typography, spacing, consistent components, light/dark/system theme) and adapts to the window size — usable on a narrow window and on a wide monitor. Content spans the available width (≈90% of the viewport with small side gutters) rather than being confined to a narrow fixed column. When the user clicks "Load model" (or starts a session), the UI immediately shows a non-blocking progress indicator and then updates the model/run status live as it changes — the user never has to manually reload the page to see whether the model loaded.

**Why this priority**: The UI is the only way users operate the runtime. A confusing, static interface that requires manual refreshes blocks every other workflow and undermines trust in the product.

**Independent Test**: Open the UI, resize the window from narrow to wide and confirm the layout reflows without horizontal scrolling or clipping and content uses the full available width with small side gutters. Click "Load model"; confirm a progress indicator appears immediately and the status transitions to "ready" (or shows an error) live without a manual page reload.

**Acceptance Scenarios**:

1. **Given** the control panel is open at any window width from a small window to a wide monitor, **When** the user views any page, **Then** the content area uses approximately the full viewport width (about 90%) with small left/right gutters and never relies on a narrow fixed maximum width.
2. **Given** the user clicks "Load model", **When** the request is in flight, **Then** a non-blocking progress/loading indicator is shown and the control is disabled or marked busy until the result arrives.
3. **Given** a model load completes (or fails), **When** the status changes, **Then** the UI reflects the new status live (ready / loaded / error) without the user manually reloading the page.
4. **Given** the user resizes the window or opens it on a small screen, **When** the layout reflows, **Then** navigation and controls remain reachable and readable without horizontal scrolling.
5. **Given** the user switches theme (dark / light / system), **When** the theme changes, **Then** all pages render consistently in the selected theme.
6. **Given** any page in the app, **When** it is displayed, **Then** it uses the same modern, consistent visual system (typography, spacing, buttons, cards, tables) so no page looks unfinished relative to the others.

---

### User Story 2 - Default agent toolset with file-aware read/edit (Priority: P1)

When a session or automation runs, the agent has a standard set of tools available by default: **read** (read file contents, optionally a specific line/byte range), **powershell** (run PowerShell commands), **edit** (precise in-place edits that replace a specific existing section of a file), **write** (create or overwrite a file), **grep** (search file contents), **find** (find files by glob), **ls** (list directories), and **parallel** (run independent tool calls together). The agent reads the relevant part of a file before editing so replacements land in the correct place. All file tools remain bounded by the existing consent/path gate.

**Why this priority**: Reliable file manipulation and shell access are what make the agent useful for real tasks. Without precise read/edit, the agent cannot safely modify existing files; without the broader toolset it cannot search, navigate, or run commands.

**Independent Test**: Start a session in the workspace folder and ask the agent to (a) find files matching a glob, (b) grep for a string, (c) read a specific range of a file, (d) make a precise in-place edit to one section of that file leaving the rest unchanged, (e) write a new file, and (f) run a PowerShell command. Confirm each tool is invoked, the edit replaces only the targeted section, and every file operation respects the consent gate.

**Acceptance Scenarios**:

1. **Given** a running agent, **When** it needs file contents, **Then** a **read** tool returns the contents and can return only a requested line/byte range rather than the whole file.
2. **Given** a running agent, **When** it must change part of an existing file, **Then** an **edit** tool replaces a specified existing section in place while leaving the rest unchanged. The edit tool supports two targeting modes — exact-string find-and-replace (must match exactly once, else fail) and line-range replace (start/end lines) — and fails with a clear message if an exact-string target is not found or is not unique.
3. **Given** a running agent, **When** it creates or overwrites a file, **Then** a **write** tool produces the file (creating parent folders as needed).
4. **Given** a running agent, **When** it must locate files, **Then** a **find** tool returns paths matching a glob and a **grep** tool returns matches for a text/pattern search.
5. **Given** a running agent, **When** it inspects a folder, **Then** an **ls** tool lists directory entries.
6. **Given** a running agent, **When** it must run a shell command, **Then** a **powershell** tool runs the command (starting in the workspace) and returns its output (bounded), surfacing non-zero exit codes.
6a. **Given** a powershell command that targets a path outside currently consented folders, **When** it runs, **Then** the same consent prompt used by file tools is raised; granting permanently records the path as a user-consented folder in Settings, and the secrets/app-internal areas are always denied.
7. **Given** a running agent with several independent operations, **When** it issues them together, **Then** a dedicated **parallel** meta-tool accepts a list of two or more independent sub-tool-calls, runs them concurrently, and returns all results; it is used only for independent operations and never for concurrent operations on the same target.
8. **Given** any file tool, **When** the target path is outside an allowed/consented folder, **Then** the operation is blocked by the consent gate with a clear message (existing behavior preserved).
9. **Given** the agent intends to edit a file, **When** it proceeds, **Then** it reads the relevant section first so the edit is positioned correctly (guidance encoded in the agent's instructions/tool descriptions).
10. **Given** these default tools, **When** a run starts with no custom configuration, **Then** all of them are available unless disabled by global settings or a per-run override.

---

### User Story 3 - Single-run concurrency with a visible run & queue surface (Priority: P1)

At most one session or automation runs at a time. A persistent indicator in the top-right of the UI shows what is currently running (its type — session or automation — and a short label/status). Clicking the indicator opens details of the active run. While a run is active, starting another session or triggering another automation does not run immediately; instead the new item joins a FIFO queue and runs when the active run finishes. If the queue is non-empty, a collapsible panel beneath the indicator lists the waiting items in order; when the queue is empty, that panel is not shown.

**Why this priority**: The runtime's core constraint is one model loaded at a time. Users must be able to see and trust this, otherwise overlapping requests appear to silently fail or stall.

**Independent Test**: Start a session; confirm the top-right indicator shows it as running and clicking it reveals details. While it runs, start a second session and trigger an automation; confirm neither starts immediately, both appear in the collapsible queue panel in FIFO order, and the panel disappears once the queue drains. Confirm exactly one run is active at any moment.

**Acceptance Scenarios**:

1. **Given** a run is active, **When** the user views any page, **Then** a top-right indicator shows the active run's type and status.
2. **Given** the top-right indicator, **When** the user clicks it, **Then** the UI navigates to the running session view (an automation run also has its own session), which exposes the full run controls (stop, steer, queue-message, transcript); for an automation run, the automation's definition is shown alongside with an edit affordance.
3. **Given** an active run, **When** the user starts another session or an automation fires, **Then** the new item is queued (not started) and exactly one run remains active.
4. **Given** a non-empty queue, **When** the user opens the run surface, **Then** a collapsible panel lists queued items in FIFO order with their type/label.
5. **Given** an empty queue, **When** the user views the run surface, **Then** the queue panel is hidden (shown only when items are waiting).
6. **Given** the active run finishes, **When** the queue is non-empty, **Then** the next queued item starts automatically and the surfaces update live.
7. **Given** no run is active and the queue is empty, **When** the user views the UI, **Then** the indicator shows an idle state and no queue panel is shown.

---

### User Story 4 - Per-run configuration for sessions and automations (Priority: P2)

When starting a session (including a follow-up that begins a new run) or creating/running an automation, the user can attach a configuration: the **model** to use, a **per-run tool configuration** (enable/disable individual tools independently of the global tool settings, so a globally enabled tool can be disabled for just this run and vice versa), and a **per-run MCP configuration** (which MCP servers/tools are active for this run). Skills are not part of per-run config — they remain globally defined and are available whenever relevant, because they are only invoked on demand and do not consume the token budget at idle.

**Why this priority**: Per-run control lets users tailor capability and cost to the task (e.g., a fast model with a minimal toolset for a simple automation) without changing global defaults. It builds on the run/queue and toolset work.

**Independent Test**: Start a session with a non-default model and a per-run tool override that disables a globally enabled tool and enables a globally disabled one; confirm the run uses the chosen model and exactly the overridden tool set while the global configuration is unchanged afterward. Create an automation with its own model and MCP selection; confirm each run uses that configuration.

**Acceptance Scenarios**:

1. **Given** starting a session, **When** the user opens the run config, **Then** they can choose the model, per-run tool enable/disable overrides, and the MCP selection for that run.
2. **Given** a per-run tool override, **When** the run executes, **Then** the agent's available tools reflect the override (a globally enabled tool can be disabled for this run; a globally disabled tool can be enabled for this run) and the global configuration is not modified.
3. **Given** a per-run model selection, **When** the run executes, **Then** that model is loaded for the run instead of the default.
4. **Given** a per-run MCP selection, **When** the run executes, **Then** only the selected MCP servers/tools are active for that run.
5. **Given** an automation, **When** it is created or edited, **Then** the same per-run configuration (model, tools, MCP) can be saved and is applied on each scheduled run.
6. **Given** a follow-up that starts a new run, **When** the user begins it, **Then** they may supply a fresh run configuration for that run.
7. **Given** skills, **When** a run is configured, **Then** skills are not shown as per-run toggles and remain globally available to every run.
8. **Given** no per-run configuration is supplied, **When** a run starts, **Then** it uses the default model, the global tool configuration, and the global MCP configuration.

---

### Edge Cases

- **Connection loss during a run**: if the live update channel drops, the UI must recover the current run/model/queue status on reconnect (no stale "loading forever" state) and resume live updates.
- **Model load failure**: a failed load surfaces an error in the live status and the run is marked failed; no model remains loaded and the queue continues.
- **Edit target not found or ambiguous**: in exact-string mode, if the target text is missing or matches more than once, the edit fails with a clear message and the file is left unchanged; in line-range mode, an out-of-bounds range fails clearly without modifying the file.
- **PowerShell command that hangs or runs long**: the command is bounded by a timeout and its output is truncated so a run cannot stall indefinitely or flood the transcript.
- **Per-run override referencing a removed tool/MCP**: if a saved automation config references a tool or MCP server that no longer exists, the run proceeds with the still-valid capabilities and notes the missing reference rather than failing outright.
- **Queue item cancelled before it starts**: a user may remove a queued item; it is dropped without affecting the active run.
- **Very small viewport**: navigation collapses gracefully (e.g., to a compact menu) while keeping the run indicator and primary actions reachable.
- **Disabling all tools for a run**: the run still executes but the agent has no tools; this is allowed and not treated as an error.

## Requirements *(mandatory)*

### Functional Requirements

#### UI overhaul (responsive + live)

- **FR-001**: The web UI MUST present a consistent, modern visual system (typography, spacing, buttons, cards, tables, badges) across every page so no page appears unfinished relative to others.
- **FR-002**: The content area MUST use a fluid width of approximately 90% of the viewport with small (≈5%) left/right gutters, and MUST NOT constrain content to a narrow fixed maximum width (the prior ~1100px cap is removed).
- **FR-003**: The UI MUST be responsive, reflowing without horizontal scrolling or clipping from small windows up to wide monitors, keeping navigation and primary controls reachable at every size.
- **FR-004**: When the user triggers a model load or starts a run, the UI MUST immediately show a non-blocking progress/loading indicator and mark the triggering control as busy until a result returns.
- **FR-005**: The UI MUST update model status and run status live (loading → ready/loaded → generating → unloaded, plus error states) without requiring a manual page reload.
- **FR-006**: The UI MUST continue to support dark, light, and system themes, applied consistently across all pages.
- **FR-007**: If the live-update channel disconnects, the UI MUST recover and display the current run/model/queue status on reconnect rather than remaining in a stale in-flight state.

#### Default agent toolset

- **FR-008**: A run's default toolset MUST include capabilities for: reading files, running PowerShell, precise in-place editing, writing/overwriting files, content search (grep), file-glob search (find), directory listing, and a parallel meta-tool that runs two or more independent sub-tool-calls concurrently. The toolset MUST be a single, consistently-named set that favors clear, descriptive tool names; existing descriptive names (read_file, list_dir, write_file) are reused where the tool already exists, and the remaining capabilities (edit, grep, find, powershell, parallel) are added with equally descriptive names.
- **FR-009**: The **read** tool MUST be able to return a specific portion of a file (e.g., a line or byte range) in addition to the whole file.
- **FR-010**: The **edit** tool MUST perform precise in-place edits, supporting two overloaded targeting modes: (a) **exact-string** find-and-replace — given the exact existing text and its replacement, it MUST replace it in place and MUST fail with a clear message if the target is not found or is not unique; and (b) **line-range** replace — given start/end line numbers and new content, it MUST replace exactly that range. Both modes MUST leave the rest of the file unchanged.
- **FR-011**: The **write** tool MUST create or overwrite a file, creating parent folders as needed.
- **FR-012**: The **grep** tool MUST return matches for a text or pattern search across files, and the **find** tool MUST return file paths matching a glob.
- **FR-013**: The **ls** tool MUST list the entries of a directory.
- **FR-014**: The **powershell** tool MUST run a PowerShell command and return its output, starting execution in the workspace folder, bounding execution time and output size and surfacing non-zero exit codes.
- **FR-015**: All file-accessing tools (read, edit, write, grep, find, ls) MUST continue to route through the existing consent/path gate; access outside allowed/consented folders MUST be blocked with a clear message.
- **FR-015a**: The **powershell** tool MUST be bound by the same consent model as the file tools: it operates freely within the workspace and any already-consented folders; the secrets store and app-internal areas MUST always be denied; and a command needing a path outside currently consented folders MUST raise the same consent prompt (session/permanent), with permanent grants persisted as user-consented folders in Settings.
- **FR-016**: The agent's instructions and/or tool descriptions MUST direct the agent to read the relevant section of a file before editing it so edits are positioned correctly.
- **FR-017**: Each default tool MUST expose a description and parameters clear enough that the agent can select and use it correctly across common scenarios (read, search, navigate, edit, write, run).

#### Single-run concurrency & visible queue

- **FR-018**: The system MUST allow at most one active run (session or automation) at any time (preserving the existing single-active FIFO behavior).
- **FR-019**: When a run is active, a new session start or automation trigger MUST be enqueued in FIFO order rather than started immediately.
- **FR-020**: When the active run finishes, the next queued item MUST start automatically.
- **FR-021**: The UI MUST show a persistent top-right indicator of what is currently running (type — session/automation — and status), or an idle state when nothing is running.
- **FR-022**: Clicking the run indicator MUST navigate to the running session view (an automation run also has an associated session), which exposes the full run controls (stop, steer, queue-message, transcript). For an automation run, the running session view MUST also show the automation's definition with an affordance to edit it (and that automation MUST also be editable from the Automations list).
- **FR-023**: When the queue is non-empty, the UI MUST show a collapsible panel listing queued items in FIFO order with their type/label; when the queue is empty, the panel MUST be hidden.
- **FR-024**: The run indicator and queue panel MUST update live as runs start, finish, and queue contents change.
- **FR-025**: Users MUST be able to remove a queued item before it starts without affecting the active run.
- **FR-025a**: The run queue MUST be persisted: queued manual sessions and automation runs, plus an interrupted in-progress run, MUST be saved and resumed on next app startup (continuing where they left off) so no queued work is silently lost on an app or PC restart.

#### Per-run configuration

- **FR-026**: Starting a session (including a follow-up that begins a new run) MUST allow an optional run configuration specifying the model, per-run tool overrides, and per-run MCP selection.
- **FR-027**: Creating or editing an automation MUST allow saving the same run configuration (model, per-run tool overrides, per-run MCP selection) to be applied on each scheduled run.
- **FR-028**: Per-run tool overrides MUST be independent of the global tool configuration: a globally enabled tool MAY be disabled for a run, and a globally disabled tool MAY be enabled for a run, without changing the global configuration.
- **FR-029**: A per-run model selection MUST cause that model to be loaded for the run instead of the default model.
- **FR-030**: A per-run MCP selection MUST restrict the active MCP servers/tools for that run to the selected set. De-selecting an MCP for a run MUST only scope it out of that run and MUST NOT change its global enabled state.
- **FR-030a**: Per-run capability resolution MUST apply most-granular-wins precedence: the per-run MCP selection determines which servers are active, then per-tool enable/disable overrides apply on top of the resulting tool set (built-in + MCP tools), so a run may keep a server active while disabling one specific tool it provides.
- **FR-030b**: Per-run configuration choices MUST default to the global settings, MAY be changed for a session, and MUST persist for that session until changed by a follow-up that starts a new run.
- **FR-031**: Skills MUST remain globally defined and available to every run; they MUST NOT appear as per-run toggles.
- **FR-032**: When no run configuration is supplied, a run MUST use the default model, the global tool configuration, and the global MCP configuration.
- **FR-033**: If a saved run configuration references a tool or MCP server that no longer exists, the run MUST proceed with the still-valid capabilities and note the missing reference rather than failing outright.

### Key Entities *(include if feature involves data)*

- **Run**: A single unit of agent work — a session or an automation execution. Has a type, status (queued / loading / active / completed / failed), a trigger, and an associated run configuration. Exactly one run is active at a time.
- **Run Queue**: The ordered (FIFO) list of pending runs waiting for the active run to finish. Visible in the UI only when non-empty. Persisted across app/PC restarts and resumed on next startup.
- **Run Configuration**: The per-run settings attached to a session or automation: selected model, per-run tool enable/disable overrides, and per-run MCP selection. Optional; absent means "use global defaults".
- **Tool**: A capability available to the agent during a run (read, powershell, edit, write, grep, find, ls, parallel, plus any custom/MCP tools). Has an enabled/disabled state globally and an optional per-run override.
- **Global Tool Configuration**: The default enabled/disabled state of each tool, applied to runs that don't override it; unaffected by per-run overrides.
- **MCP Selection**: The set of MCP servers/tools active for a run; globally configured with an optional per-run restriction. A per-run restriction scopes a server out of that run only and does not change its global enabled state.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: After clicking "Load model" or starting a run, the user sees the resulting status update without any manual page reload in 100% of attempts.
- **SC-002**: On viewports from a narrow window to a wide monitor, the primary content area occupies approximately 90% of the viewport width (within a reasonable tolerance) and produces no horizontal scrollbar.
- **SC-003**: A new evaluator rates each page of the UI as "professional/consistent" (clear layout, consistent components, no broken or unstyled elements) across all pages.
- **SC-004**: In an agent run, all eight default tools (read, powershell, edit, write, grep, find, ls, parallel) can be invoked successfully, and a targeted edit changes only the intended section of a file in 100% of test cases.
- **SC-005**: Across concurrent start attempts, exactly one run is active at any moment and additional requests appear in the queue in FIFO order in 100% of observations.
- **SC-006**: The top-right run indicator reflects the true active-run state, and the queue panel appears only when items are waiting, in 100% of observed transitions.
- **SC-007**: A run started with a per-run tool override uses exactly the overridden tool set, and the global tool configuration is unchanged after the run completes, in 100% of test cases.
- **SC-008**: A run started with a per-run model and MCP selection uses that model and only the selected MCP servers/tools in 100% of test cases.

## Assumptions

- This feature extends the existing `001-agent-runtime` system; its lifecycle (no model at idle; load-run-unload; one model at a time), consent/path gate, secrets isolation, sessions store, and scheduler remain in force and are not redesigned.
- The single-active FIFO session queue already exists in the runtime; this feature primarily adds the UI surfacing (indicator + collapsible queue panel) and extends queuing/visibility to include automation triggers alongside manual sessions.
- "Auto-loading" refers to live UI updates (driven by the existing real-time channel) and a non-blocking progress indicator — not to automatically loading a model at idle, which is explicitly disallowed by the runtime's design.
- The existing built-in file tools (read_file, list_dir, write_file) are reused as the descriptive identities for read/ls/write; the remaining capabilities (edit, grep, find, powershell, parallel) are added. The result is one consistently-named toolset favoring clarity, not a backward-compatibility alias layer. Current consent-gating behavior is preserved for all file tools.
- "powershell" is the shell tool because the product is Windows-only by design; the shell tool runs PowerShell commands.
- The **parallel** tool is an explicit meta-tool: the agent passes it a list of two or more independent sub-tool-calls to run concurrently. It is intended only for independent operations; it must not be used to run concurrent operations against the same target (e.g., two edits to the same file).
- Approximately 90% content width with ≈5% side gutters is the intended layout target; exact responsive breakpoints and component styling are design details left to implementation within these bounds.
- Per-run MCP configuration selects from the globally configured MCP servers; it does not add new servers (adding MCP servers remains a global/Settings or agent-capability action from the prior feature).
