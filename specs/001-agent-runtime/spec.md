# Feature Specification: Call-Based LM Studio Agent Runtime

**Feature Branch**: `001-agent-runtime`

**Created**: 2026-06-12

**Status**: Draft

**Input**: User description: "Turn the current LM Studio model loader/unloader into a call-based, LM Studio-powered agent (OpenClaw-style) that does NOT run forever. It is invoked on demand or by automations; once a task is done it unloads the model and stops. Users can set up automations that load the model with a notification when running (PC must be on; if it was off, on next startup inform the user it failed at xyz time). The agent has read/write access to a single default folder and can request access to other folders, granted per-session or forever. A Sessions page manages all this. MCP servers are addable, and a skills folder in Documents lets users add custom skills (Markdown SKILL.md, optionally with referenced scripts). Skills and MCPs/tools are usable by both automations and normal tasks. The model is not loaded by default — it loads only when an automation or task runs. There is a default model with options to select others. The existing model-management functionality lives in a separate Settings section (e.g. context length per model). Custom Python tools are allowed (treated as arbitrary code with a warning; a future reviewer should detect prompt injection or malicious code). The UI is a local web UI opened from the tray icon. Comprehensive app settings (theme dark/light/system, etc.) are expected."

## Overview

The product evolves from a tray utility that loads/unloads LM Studio models into a **local, call-based agent runtime**. A lightweight controller stays resident while the PC is on (system tray + local web UI) **with no model loaded at idle**. When the user starts a task or an automation fires, the runtime loads the chosen model, executes the task using the agent's tools, skills, and MCP servers within a consent-bounded filesystem scope, then unloads the model and returns to idle. All management — sessions, automations, capabilities (skills/MCPs/tools), consent grants, and settings — happens from the web UI.

This specification describes WHAT the system does and WHY, independent of implementation technology.

## Clarifications

### Session 2026-06-12

- Q: Is an agent run a single-shot autonomous task or an interactive conversation? → A: Interactive session (B) — a chat with the agent supporting steering, message queuing, and stop-generating, plus automatic context compression when usage nears the context limit; "all the expected features of an agent."
  - Interaction model: while the agent is generating, pressing Enter **steers** (injects a course-correcting message into the active turn); **Alt+Enter queues** a message to be processed after the current turn; a **Stop generating** control halts the current turn while keeping the session open.
  - Lifecycle: the model stays loaded for the duration of an active interactive session (not just one turn) and is unloaded when the session ends or after a configurable idle timeout. Automations run the same interactive engine but unattended (single programmatic turn, no human steering), unloading on completion.
  - Context management: when conversation context reaches ~90% of the model's context length, the system automatically compresses (summarizes) earlier history to free space and continues the session.
- Q: When a session is already active, what happens to a new manual/automation request? → A: Queue (A) — exactly one active session at a time; new manual sessions and automation runs wait in a FIFO queue and run in order. Rationale: limited local resources, never two models loaded at once, and no silent dropping of scheduled work.
- Q: What schedule types do automations support, and how do automations relate to sessions? → A: Two schedule modes — (1) **Daily**: multi-select days of the week plus a time-of-day; (2) **Interval**: every X minutes/hours/days. Additionally, each automation chooses a **session mode**: start a **new session** each run, or **continue the same (persistent) session** across runs. Because long histories are auto-compressed, persistent sessions are affordable and let learnings carry forward. The agent may also persist **learnings** to files (a memory area) so insights survive across runs. Token budgeting MUST be careful: the system allocates the context window across system prompt, skills, memory/learnings, conversation, and tool output so that adding memory never starves core functionality.
- Q: How granular is filesystem consent? → A: Folder-level only (A), and **hierarchical** — granting a folder also covers all of its subfolders (no re-prompting beneath a granted folder). The agent MUST follow **least privilege**: request the narrowest folder that satisfies the need and never ask for more than required. No separate per-action (delete/shell/network) approval layer in this version.
- Q: How is the agent's persona/system prompt defined? → A: Ship a **default, user-editable persona** — a neutral agent that "gets things done." Users can create and manage **additional personas** from Settings (a persona library); a session or automation selects which persona to use (default if unspecified). Personas count against the token budget. Separately: any file reference/link in the UI (e.g. clicking a file the agent produced) MUST open that file in **VS Code**.
- Q: How are secrets/credentials stored, and who configures capabilities? → A: Plaintext local config is acceptable (single-user, their own machine) — no heavy encryption required. **Critical constraint: the agent MUST never be able to read secrets.** Secrets live in a separate secrets store/file that is excluded from all agent filesystem access and never injected into agent context, logs, or transcripts. **Entering secrets is a user-only action.** Otherwise, the **agent itself may add and configure capabilities** (MCP servers, custom tools, skills) — not only the user via Settings; only the secret values for those capabilities require the user to enter them.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Run an interactive agent session (Priority: P1)

A user opens the control panel from the tray, picks a model (or uses the default), and starts a session by sending a message (e.g. "summarize the notes in my workspace and write a summary.md"). The runtime loads the model and the agent works the task across turns, streaming progress and tool calls. The user can **steer** mid-generation (press Enter to inject a correction into the active turn), **queue** a follow-up (Alt+Enter to process after the current turn), and **Stop generating** to halt a turn without ending the session. When the conversation nears the model's context limit, history is automatically compressed so the session continues. The user ends the session (or it idle-times-out), at which point the model unloads and the session is recorded.

**Why this priority**: This is the core value — an interactive, OpenClaw-style agent that costs no resources at idle, keeps the model loaded only while the session is active, and cleans up afterward. Without it, nothing else matters.

**Independent Test**: With LM Studio running and no model loaded, start a session and send a message; verify the model loads, the agent streams a response and uses tools within the workspace folder, steering/queuing/stop controls behave as specified, ending the session unloads the model, and the session shows a completed status with a full transcript.

**Acceptance Scenarios**:

1. **Given** the runtime is idle with no model loaded, **When** the user starts a session and sends a message with the default model, **Then** the system loads the default model, the agent processes the turn (streaming output and tool calls), and results are written into the workspace folder.
2. **Given** the agent is generating a turn, **When** the user presses Enter with a message, **Then** the message **steers** the active turn (the agent incorporates the correction) without starting a separate turn.
3. **Given** the agent is generating a turn, **When** the user presses Alt+Enter with a message, **Then** the message is **queued** and processed as the next turn after the current one completes.
4. **Given** the agent is generating, **When** the user clicks **Stop generating**, **Then** the current turn halts with partial output preserved and the session remains open for further input.
5. **Given** an active session, **When** the user views the control panel, **Then** they see live status (model loading, generating, tool calls), current context usage, and the controls to steer/queue/stop.
6. **Given** an active session whose context usage reaches ~90% of the model's context length, **When** the next turn would exceed it, **Then** the system automatically compresses earlier history and continues without losing the session.
7. **Given** an active session, **When** the user ends the session (or the idle timeout elapses), **Then** the model is unloaded and the session is recorded as completed.
8. **Given** the selected model fails to load, **When** the error occurs, **Then** the session is marked failed with the reason and no model remains loaded.

---

### User Story 2 - Consent-bounded filesystem access (Priority: P1)

The agent can always read and write the default **workspace** folder. When a task needs another folder, the agent requests access; the user grants it **for this session only** or **permanently**, or denies it. Grants are **hierarchical** — granting a folder also covers everything beneath it — and the agent follows **least privilege**, requesting only the narrowest folder it actually needs. Grants are visible and revocable.

**Why this priority**: Security and user consent are core constitutional requirements. The agent must never silently touch files outside its allowed scope.

**Independent Test**: Start a task that attempts to read a file outside the workspace; verify the agent pauses and prompts for access; granting "this session" allows it only until the session ends; granting "forever" persists across runs; denying blocks the operation with a clear message.

**Acceptance Scenarios**:

1. **Given** a task needs a folder outside the workspace, **When** the agent attempts access, **Then** the run pauses and the user is prompted to grant (session/permanent) or deny.
2. **Given** the user grants session-only access, **When** the session ends, **Then** the grant is removed and a future run must request again.
3. **Given** the user grants permanent access, **When** a later run needs the same folder, **Then** access is allowed without prompting.
4. **Given** an active grant exists, **When** the user revokes it from the Sessions/consent view, **Then** subsequent access requires a new prompt.
5. **Given** the agent attempts to access a path outside any granted folder, **When** there is no matching grant, **Then** the operation is blocked and reported.
6. **Given** the default workspace folder, **When** any task runs, **Then** read/write is permitted without prompting.
7. **Given** a grant on a parent folder, **When** the agent accesses any subfolder beneath it, **Then** access is allowed without a new prompt (hierarchical grant).
8. **Given** a task that only needs one subfolder, **When** the agent requests access, **Then** it requests that narrowest folder rather than a broader parent (least privilege).

---

### User Story 3 - Manage sessions (Priority: P2)

A Sessions page lists past and active runs (manual and automation-triggered) with status, timestamps, model used, the task/automation that triggered it, the transcript/log, and the consent grants in effect. From here the user manages active grants and reviews failures.

**Why this priority**: Visibility and control over what the agent did and what access it holds. Required to trust and operate the system, but depends on runs existing (US1).

**Independent Test**: Run two tasks, then open the Sessions page; verify both appear with correct metadata, transcripts are viewable, and consent grants can be inspected and revoked.

**Acceptance Scenarios**:

1. **Given** prior runs exist, **When** the user opens the Sessions page, **Then** each run shows status, start/end time, trigger (manual/automation), model, and a viewable transcript.
2. **Given** a session with active permanent grants, **When** the user opens it, **Then** the grants are listed and individually revocable.
3. **Given** a running session, **When** the user opens the Sessions page, **Then** it is shown as active with live status and a Stop control.
4. **Given** a failed session, **When** the user opens it, **Then** the failure reason and the point of failure are shown.

---

### User Story 4 - Schedule automations (Priority: P2)

A user creates an automation: a saved task plus a schedule. The schedule is either **Daily** (multi-select days of the week + a time-of-day) or **Interval** (every X minutes/hours/days). Each automation also picks a **session mode** — run in a **new session** each time, or **continue a persistent session** so prior context and learnings carry forward (history is auto-compressed to stay within budget). When the schedule fires and the PC is on, the runtime notifies the user that the automation is running, loads the model, runs the task, and unloads. If the PC was off at the scheduled time, the next time the app starts it informs the user that the automation was missed at the scheduled time.

**Why this priority**: A major differentiator (unattended, scheduled agent work) but it builds on the on-demand runner and sessions.

**Independent Test**: Create a Daily automation (e.g. Mon/Wed/Fri at 09:00) and an Interval automation (every 30 minutes), one scheduled a minute out; with the app running, verify a notification appears, the run executes, and a session is recorded. Toggle session mode and verify a persistent-session automation reuses and compresses its prior conversation while a new-session automation starts fresh. Then simulate a missed run (schedule a time while the app is closed) and verify a "missed at xyz" notification on next startup.

**Acceptance Scenarios**:

1. **Given** a Daily automation with selected weekdays and a time, **When** a selected day's time arrives while the app is running, **Then** the user is notified and a session executes; on non-selected days it does not fire.
2. **Given** an Interval automation (every X minutes/hours/days), **When** each interval elapses, **Then** a run is triggered (subject to the single-session queue).
3. **Given** an automation's scheduled time passed while the app/PC was off, **When** the app next starts, **Then** the user is notified that the automation was missed, including the scheduled time.
4. **Given** an automation run completes, **When** it ends, **Then** the model is unloaded and a session is recorded with the automation as its trigger.
5. **Given** an automation set to **new session**, **When** it runs, **Then** it starts a fresh conversation with no prior history.
6. **Given** an automation set to **persistent session**, **When** it runs again, **Then** it resumes the same conversation (auto-compressed as needed) so prior context and logged learnings carry forward.
7. **Given** an automation needs a folder grant, **When** it runs unattended, **Then** it uses only permanent grants; if a needed grant is missing, the run fails with a clear "permission not granted" reason rather than blocking indefinitely.
8. **Given** multiple automations, **When** the user views the Automations page, **Then** each shows its schedule, session mode, last run result, next run time, and enable/disable toggle.
9. **Given** an automation, **When** the user disables it, **Then** it does not fire until re-enabled.

---

### User Story 5 - Add and use custom skills (Priority: P2)

A user drops a skill into the Documents skills folder: a folder containing a `SKILL.md` (Claude-style instructions) and optionally referenced scripts the skill can call on demand. The skill becomes available to the agent for both manual tasks and automations. A Settings → Skills section lists discovered skills with their metadata and an enable/disable toggle.

**Why this priority**: Extensibility (plug-and-play) is a core constitutional principle and a primary product goal, but it depends on the runner being able to use capabilities.

**Independent Test**: Add a skill folder with a valid `SKILL.md`; verify it appears in the Skills list; run a task whose description matches the skill's purpose and verify the agent uses the skill's instructions (and any referenced script when invoked).

**Acceptance Scenarios**:

1. **Given** a valid skill folder with `SKILL.md` in the skills directory, **When** the user opens Settings → Skills (or refreshes), **Then** the skill is listed with its name and description.
2. **Given** an enabled skill relevant to a task, **When** the agent runs the task, **Then** the skill's instructions are made available to the agent.
3. **Given** a skill that references a script, **When** the agent decides to use it, **Then** the script is invokable and its result returns to the agent.
4. **Given** a malformed or empty `SKILL.md`, **When** discovery runs, **Then** the skill is shown as invalid with a reason and is not offered to the agent.
5. **Given** a skill, **When** the user disables it, **Then** it is not offered to the agent in subsequent runs.

---

### User Story 6 - Add MCP servers and custom tools (Priority: P2)

In Settings → MCP Servers & Tools, the user adds MCP servers (via a config such as `mcp.json`) and custom Python tools (placed in the tools folder). Both become callable by the agent in manual tasks and automations. Adding a custom Python tool shows a clear warning that it is arbitrary code that runs with the app's privileges and should be reviewed.

**Why this priority**: Tool extensibility complements skills and is core to the plug-and-play goal, but depends on the runner.

**Independent Test**: Add a stdio MCP server config and verify its tools are discovered and callable in a run. Add a custom Python tool and verify the arbitrary-code warning is shown, the user must confirm trust, and the tool becomes callable.

**Acceptance Scenarios**:

1. **Given** a valid MCP server entry in the configuration, **When** the user adds/enables it, **Then** the server's tools are discovered and shown, and are callable by the agent.
2. **Given** an MCP server that fails to start or connect, **When** the user enables it, **Then** the failure is reported and its tools are not offered.
3. **Given** a custom Python tool, **When** the user adds it, **Then** a warning that it is arbitrary code is shown and explicit trust confirmation is required before it is enabled.
4. **Given** an enabled custom tool, **When** the agent runs a task, **Then** the tool is callable and its result returns to the agent.
5. **Given** an MCP server or tool, **When** the user disables or removes it, **Then** it is no longer offered to the agent.

---

### User Story 7 - Configure the application (Priority: P3)

A Settings area lets the user configure the app: default model and model selection, appearance (dark/light/system theme), startup behavior, notifications, the web UI, idle/unload behavior, data retention, and an **Advanced → Model Management** section that preserves today's functionality (per-model context-length preferences, manual load/unload, warmup).

**Why this priority**: Necessary for a polished app of this caliber, but the runtime can function on sensible defaults first.

**Independent Test**: Change the theme to each of dark/light/system and verify the UI reflects it; set a default model and verify new sessions use it; set per-model context length in Advanced and verify it is applied when that model loads.

**Acceptance Scenarios**:

1. **Given** the Settings page, **When** the user selects a theme (dark/light/system), **Then** the UI applies it immediately and persists the choice.
2. **Given** Settings, **When** the user sets a default model, **Then** new tasks/automations without an explicit model use it.
3. **Given** Advanced → Model Management, **When** the user sets a per-model context length, **Then** that value (clamped to the model's valid range) is used when the model loads.
4. **Given** Settings, **When** the user enables "launch on startup", **Then** the controller starts on next login minimized to the tray.
5. **Given** Settings, **When** the user changes notification or web-UI preferences, **Then** the changes persist and take effect.

---

### Edge Cases

- **LM Studio unavailable**: Starting a task when LM Studio is not reachable produces a clear error; the session is marked failed; no model state is changed.
- **Model load timeout / OOM**: If a model fails to load (timeout, insufficient memory), the run fails with the reason and the runtime returns to idle with nothing loaded.
- **Concurrent run requested**: If a session is active and another task/automation triggers, the new request is appended to the FIFO queue and runs when the active session ends; the user can see and cancel queued items.
- **Automation fires mid-run**: If an automation triggers while another session is active, it is queued (not dropped) and runs in order; if it becomes stale before it starts (see retention/schedule policy), it is recorded accordingly.
- **Stop during a tool call / model load**: Stopping mid-operation cleanly aborts, unloads any loaded model, and records a stopped status without corrupting the workspace.
- **Steering vs. queuing while idle**: If the user submits a steering (Enter) or queued (Alt+Enter) message when no turn is actively generating, it is treated as a normal next-turn message.
- **Context compression failure**: If automatic compression cannot reduce context below the limit (e.g. a single message exceeds capacity), the turn fails gracefully with a clear message and the session stays open.
- **Idle session timeout**: An interactive session left idle beyond the configured timeout ends automatically and unloads the model, recording the reason.
- **App crash / forced quit with a model loaded**: On next startup the controller detects an orphaned loaded model and offers to unload it; missed automations are reported.
- **Consent prompt during unattended automation**: With no matching permanent grant, the automation fails fast with "permission not granted" instead of waiting forever.
- **Revoking a grant mid-run**: Revocation applies to future access checks; an in-flight operation already permitted completes, but subsequent access to that folder is blocked.
- **Path traversal / symlink escape**: Attempts to escape a granted folder via `..`, absolute paths, or symlinks are blocked and reported.
- **Skill/tool/MCP name collision**: Two capabilities exposing the same tool name are disambiguated or flagged; the user is informed.
- **Malformed `mcp.json` or skill folder**: Invalid entries are surfaced as errors and skipped without crashing discovery.
- **Custom tool raises or hangs**: A failing custom tool returns an error to the agent; a hanging tool is subject to a timeout and reported.
- **Documents folder missing or not writable**: On first run the folder structure is created; if it cannot be created, the user is warned and given guidance.
- **Web UI port in use**: If the configured port is taken, the app falls back to another port (or reports the conflict) and the tray "open" action targets the actual port.
- **Multiple browser tabs open**: Concurrent control-panel tabs reflect consistent state (a single source of truth for runs/sessions).
- **Very long-running task**: A run exceeding a configurable maximum duration is stopped and reported, ensuring the model does not stay loaded indefinitely.

## Requirements *(mandatory)*

### Functional Requirements

**Runtime lifecycle & model management**

- **FR-001**: The system MUST remain resident while the PC is on as a controller with a system-tray presence and **no model loaded at idle**.
- **FR-002**: The system MUST load a model only when a session or automation begins, MUST keep it loaded for the duration of an active interactive session, and MUST unload it automatically when the session ends, is stopped, fails, or hits the idle timeout.
- **FR-003**: The system MUST allow the user to select the model for a session, and MUST use a configurable **default model** when none is specified.
- **FR-004**: The system MUST run agent work as interactive sessions composed of turns; at idle it MUST NOT keep a model loaded or maintain a perpetual agent loop. A session ends explicitly by the user or via a configurable idle timeout, after which the model unloads.
- **FR-005**: The system MUST provide a **Stop generating** control that halts the current turn while preserving partial output and keeping the session open (see FR-059), and MUST allow the user to end a session entirely (after which the model unloads and the session is recorded).
- **FR-006**: The system MUST detect, on startup, a model left loaded by a prior abnormal termination and offer to unload it.
- **FR-007**: The system MUST enforce a configurable maximum run duration, stopping and reporting runs that exceed it.
- **FR-008**: The system MUST process exactly one active session at a time and MUST place additional manual sessions and triggered automation runs into a FIFO queue, executing them in order; the queue MUST be visible to the user and queued items MUST be cancellable before they start.

**Interactive session control**

- **FR-056**: The system MUST support multi-turn interactive sessions where the user exchanges successive messages with the agent while the model remains loaded.
- **FR-057**: The system MUST support **steering**: while a turn is generating, an Enter-submitted message injects guidance into the active turn rather than starting a new turn.
- **FR-058**: The system MUST support **message queuing**: an Alt+Enter-submitted message is queued and processed as the next turn after the current turn completes.
- **FR-059**: The system MUST provide a **Stop generating** control that halts the current turn, preserves any partial output, and leaves the session open for further input.
- **FR-060**: The system MUST stream the agent's output and tool activity to the UI in real time during a turn.
- **FR-061**: The system MUST monitor context usage and, when it reaches a configurable threshold (default ~90% of the active model's context length), automatically compress/summarize earlier conversation history so the session can continue; compression events MUST be recorded in the session.
- **FR-062**: The system MUST end a session and unload the model on explicit user end or after a configurable idle timeout; automation runs MUST execute the session engine unattended (no human steering) and unload on completion.

**Agent execution, skills, tools, MCP**

- **FR-009**: The system MUST execute the user's task using the selected model, making available the enabled skills, custom tools, and MCP server tools.
- **FR-079**: In addition to the user adding capabilities via Settings, the **agent itself** MUST be able to add and configure capabilities (MCP servers, custom tools, skills) during a session — subject to the same validation, trust gates (FR-015), and secret-isolation rules (FR-026, FR-076–FR-078). Entering secret values for those capabilities is a **user-only** action the agent cannot perform.
- **FR-010**: The system MUST discover skills from the Documents skills folder, where each skill is a folder containing a `SKILL.md` and optional referenced scripts.
- **FR-011**: The system MUST present each discovered skill's metadata (name, description, validity) and allow enabling/disabling per skill.
- **FR-012**: The system MUST make an enabled skill's instructions available to the agent and MUST allow the agent to invoke scripts that the skill references.
- **FR-013**: The system MUST allow users to add, enable, disable, and remove MCP servers via configuration (e.g. `mcp.json`), and MUST expose connected servers' tools to the agent.
- **FR-014**: The system MUST allow users to add custom Python tools (from the tools folder) that the agent can call.
- **FR-015**: The system MUST display a clear warning that custom Python tools are arbitrary code running with the application's privileges, and MUST require explicit user trust confirmation before such a tool is enabled.
- **FR-016**: The system MUST make skills, custom tools, and MCP tools usable by both manual tasks and automations.
- **FR-017**: The system MUST report capability failures (invalid skill, MCP connection failure, tool error/timeout) without crashing and MUST exclude failed capabilities from what is offered to the agent.
- **FR-018**: The system SHOULD apply a configurable timeout to individual tool/MCP calls and report calls that exceed it.

**Filesystem consent & security**

- **FR-019**: The system MUST grant the agent read/write access to the default **workspace** folder without prompting.
- **FR-020**: The system MUST require explicit user consent before the agent accesses any path outside currently granted folders.
- **FR-021**: The system MUST let the user grant folder access **for the current session only** or **permanently**, or **deny** it.
- **FR-022**: The system MUST remove session-only grants when the session ends and MUST persist permanent grants across runs and restarts.
- **FR-023**: The system MUST let the user view and revoke active grants; revocation MUST apply to subsequent access checks.
- **FR-024**: The system MUST block and report attempts to escape granted folders (e.g. `..` traversal, absolute paths outside grants, symlink escapes).
- **FR-025**: During an unattended automation, the system MUST NOT block on an interactive consent prompt; if a required folder is not covered by a permanent grant, the run MUST fail fast with a "permission not granted" reason.
- **FR-026**: The system MUST never write secrets (API keys, tokens) into logs, transcripts, or user-facing artifacts, and MUST never expose secrets to the agent or its context (see FR-076–FR-078).
- **FR-027**: The system SHOULD provide a foundation for a future **safety reviewer** that inspects custom tools and skill content for prompt injection or malicious behavior; the architecture MUST keep capability content accessible for such review. (Detection logic itself is out of scope for this version — see Out of Scope.)
- **FR-069**: Folder grants MUST be **hierarchical**: a grant on a folder MUST cover all of its subfolders, so the agent does not re-prompt for paths beneath an already-granted folder.
- **FR-070**: The agent MUST follow **least privilege** when requesting access — it MUST request the narrowest folder that satisfies the task and MUST NOT request broader access than needed.

**Automations & scheduling**

- **FR-028**: The system MUST let users create, edit, enable, disable, and delete automations, where an automation is a saved task plus a schedule plus a session mode (see FR-063, FR-064).
- **FR-029**: The system MUST, while running, trigger automations at their scheduled times and notify the user when an automation begins running.
- **FR-030**: The system MUST detect automations whose scheduled time passed while the app/PC was off and, on next startup, notify the user that those runs were missed, including the scheduled time(s).
- **FR-031**: The system MUST record every automation run as a session with the automation as its trigger.
- **FR-032**: The system MUST show, per automation, its schedule, session mode, enabled state, last run result, and next scheduled run.

**Sessions & history**

- **FR-033**: The system MUST record each run as a session capturing trigger (manual/automation), model used, start/end times, status, and a transcript/log of the agent's activity.
- **FR-034**: The system MUST provide a Sessions view listing active and past sessions with their metadata and viewable transcripts.
- **FR-035**: The system MUST show active sessions with live status and a Stop control.
- **FR-036**: The system MUST show failure reasons and the point of failure for failed sessions.
- **FR-037**: The system MUST let the user manage (view/revoke) consent grants associated with sessions.
- **FR-038**: The system MUST retain session history according to a configurable retention policy.

**User interface & notifications**

- **FR-039**: The system MUST provide a local web-based control panel for all management (tasks, sessions, automations, capabilities, settings).
- **FR-040**: The system MUST provide a system-tray icon whose primary action opens the web control panel in the browser, plus quit and quick actions.
- **FR-041**: The system MUST direct the tray "open" action to the actual address/port the web UI is serving, including when a fallback port is used.
- **FR-042**: The system MUST deliver user notifications for automation start, missed automations, and run completion/failure.
- **FR-043**: Closing the control-panel window/tab MUST NOT quit the controller; the app MUST exit only via an explicit quit action.

**Settings**

- **FR-044**: The system MUST let the user choose an appearance theme of **dark**, **light**, or **system**, applied immediately and persisted.
- **FR-045**: The system MUST let the user set the default model and browse/select available models. The system MUST also provide a Settings section to manage personas (see FR-071–FR-073).
- **FR-046**: The system MUST provide an **Advanced → Model Management** section preserving existing functionality: per-model context-length preferences (clamped to the model's valid range), manual load/unload, and warmup.
- **FR-047**: The system MUST let the user configure startup behavior (e.g. launch on login, start minimized to tray).
- **FR-048**: The system MUST let the user configure notification preferences (which events notify, enable/disable).
- **FR-049**: The system MUST let the user configure the web UI (e.g. port) and connection settings to LM Studio (base URL, API key).
- **FR-050**: The system MUST let the user configure idle/unload behavior, the session idle timeout, the context-compression threshold, and the maximum run duration.
- **FR-051**: The system MUST let the user configure data retention for sessions/logs.
- **FR-052**: The system MUST persist all settings across restarts and apply sensible, safe defaults on first run (local-only endpoints, conservative permissions).
- **FR-053**: The system MUST create the Documents folder structure (`skills/`, `tools/`, `mcp.json`, `workspace/`, and a memory area for learnings) on first run if absent, and warn if it cannot be created. The isolated **secrets area** MUST live outside any agent-accessible path (e.g. under app data, not in the Documents workspace tree).

**Resilience**

- **FR-054**: External/shared file operations (settings, grants, session store, capability configs) MUST be fault-tolerant so a missing, locked, or malformed file does not crash the app.
- **FR-055**: The system MUST validate inputs from external systems (LM Studio responses, config files, user fields, tool outputs) at the boundary before use.

**Scheduling detail & agent memory**

- **FR-063**: The system MUST support two automation schedule types: **Daily** — one or more selected days of the week combined with a time-of-day; and **Interval** — every X minutes, hours, or days.
- **FR-064**: The system MUST let each automation run in either a **new session** (a fresh conversation each run) or a **persistent session** (resuming the same conversation across runs, relying on auto-compression to stay within budget) so prior context and learnings carry forward.
- **FR-065**: The system MUST allow the agent to persist **learnings** (durable notes/insights) to files in a dedicated memory area within the Documents folder, so insights survive across sessions and automation runs.
- **FR-066**: The system MUST load relevant persisted learnings into a session's context when appropriate (e.g. for persistent-session automations or when the user opts in) without manual copy-paste, subject to the token budget.
- **FR-067**: The system MUST budget the active model's context window across its consumers (system prompt/persona, enabled skills, memory/learnings, conversation history, and tool output) so that no single consumer starves core functionality; the allocation MUST adapt to the active model's context length.
- **FR-068**: The system MUST surface current context/token usage and budget allocation for an active session, and MUST trigger compression (per the configured threshold) before the budget is exceeded.

**Personas & file opening**

- **FR-071**: The system MUST ship a **default persona** — a neutral, task-focused agent — that is **user-editable**, and MUST apply it to sessions/automations that do not specify another persona.
- **FR-072**: The system MUST let users create, edit, rename, and delete **additional personas** from Settings (a persona library), each defining the agent's base instructions/system prompt.
- **FR-073**: The system MUST let a session or automation select which persona to use; the selected persona's instructions MUST be applied within the token budget.
- **FR-074**: The system MUST open any file reference/link in the UI (e.g. a file the agent created or cited) in **VS Code**, and MUST report a clear message if VS Code is not available.
- **FR-075**: The Settings persona section MUST let the user edit the default persona and create, edit, rename, and delete additional personas.

**Secrets & capability configuration**

- **FR-076**: Secrets (LM Studio API key, MCP server credentials, tool secrets) MAY be stored in local configuration without heavy encryption (single-user, local machine), but MUST be kept in a **separate secrets store/area** distinct from capability configs (`mcp.json`, tool/skill files) and general settings.
- **FR-077**: The secrets store MUST be **excluded from all agent filesystem access** (never within the workspace or any grantable scope) and its values MUST never be injected into the agent's context, prompts, logs, or transcripts; the agent references secrets only indirectly (e.g. the runtime injects them into outbound connections at call time).
- **FR-078**: Entering or editing secret **values** MUST be a **user-only** action; the agent MUST NOT be able to read, set, or exfiltrate secret values, even though it may otherwise add/configure the capability that uses them (FR-079).

### Key Entities *(include if feature involves data)*

- **Session / Run**: A single agent execution — an interactive, multi-turn conversation (manual) or an unattended automation run. Attributes: id, trigger (manual or automation reference), model used, conversation turns (user messages, agent responses, tool calls/results, steering and queued messages), status (queued, loading, active, completed, failed, stopped), start/end timestamps, transcript/log, context-usage and compression events, failure reason, associated consent grants.
- **Automation**: A saved task plus schedule plus session mode. Attributes: id, name, task definition, schedule type (**Daily** with selected weekdays + time-of-day, or **Interval** every X minutes/hours/days), session mode (new or persistent), persistent-session reference (if persistent), persona selection (optional; default if unset), enabled state, last-run result, next-run time, model override (optional).
- **Skill**: A discovered capability from the skills folder. Attributes: folder path, name, description, validity status, enabled state, referenced scripts.
- **Custom Tool**: A user-provided Python tool. Attributes: identifier, description, source location, trust-confirmed flag, enabled state.
- **MCP Server**: An external tool-provider connection. Attributes: identifier, connection/config details, status, discovered tools, enabled state.
- **Folder Grant (Consent)**: A permission for the agent to access a folder and everything beneath it (hierarchical). Attributes: folder path, scope (session or permanent), associated session (if session-scoped), access level (read/write), granted timestamp, active/revoked state.
- **Model Configuration**: Per-model settings. Attributes: model key, preferred context length (within valid range), default-model flag.
- **Settings / Preferences**: App-wide configuration. Attributes: theme, default model, startup behavior, notification preferences, web UI port, LM Studio connection, idle/unload behavior, session idle timeout, context-compression threshold, max run duration, data retention.
- **Learning / Memory Note**: A durable insight the agent persists to the memory area. Attributes: file/location, content, source session/automation, created/updated timestamps, scope (which automation or global).
- **Persona**: A named base-instruction set defining the agent's behavior. Attributes: id, name, instructions/system prompt, default flag, editable (the built-in default is editable), created/updated timestamps.
- **Secret**: A credential value (API key, token) used by a capability or the model backend. Attributes: identifier/reference name, owning capability (MCP/tool/backend), value (stored in the isolated secrets area, never agent-accessible), created/updated timestamps. The agent can reference a secret by name but never read its value.
- **Notification**: A user-facing alert. Attributes: type (automation-running, automation-missed, run-completed, run-failed, system), message, timestamp, related session/automation.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: At idle (no active run), no model is loaded in LM Studio — verifiable in 100% of idle checks after any run completes.
- **SC-002**: After every run (success, failure, or stop), the model is unloaded within a short, bounded time (e.g. seconds), confirmed for 100% of runs in testing.
- **SC-003**: A user can start a task from the control panel and see it begin (model loading/progress) within a few seconds of submission under normal conditions.
- **SC-004**: The agent never reads or writes outside the workspace or an explicitly granted folder (or its subfolders) — 0 unauthorized accesses across the consent test suite; the agent requests the narrowest folder needed (no over-broad requests in the test scenarios).
- **SC-005**: Session-only grants are gone after the session ends and permanent grants survive restart — verified across a restart cycle.
- **SC-006**: When the app starts after a scheduled automation was missed (app/PC off), the user is informed of the miss including the original scheduled time — 100% of missed runs reported.
- **SC-007**: A user can add a valid skill (folder + `SKILL.md`) and have it appear and be usable by a task without restarting the app.
- **SC-008**: A user can add an MCP server and a custom Python tool and have their tools become callable, with the arbitrary-code warning and trust confirmation shown for the custom tool 100% of the time before enablement.
- **SC-009**: A user can complete first-run setup and run their first task without manual file editing (folder structure auto-created, sensible defaults applied).
- **SC-010**: Switching theme (dark/light/system) and setting a default model take effect immediately and persist across restart.
- **SC-011**: The Sessions page accurately reflects every run's trigger, model, status, and transcript for 100% of runs.
- **SC-012**: No secret values appear in any session transcript, log, or exported artifact — verified by scanning test outputs.
- **SC-013**: During an active session, steering injects into the current turn, Alt+Enter queues the next turn, and Stop generating halts within a bounded time while keeping the session open — each verified across the interaction test suite.
- **SC-014**: When context usage reaches the configured threshold, the session automatically compresses history and continues without data loss to the active task — verified by driving a session past the threshold.
- **SC-015**: Daily automations fire only on selected weekdays at the set time, and Interval automations fire each interval — verified across a multi-day/interval test schedule.
- **SC-016**: A persistent-session automation resumes its prior conversation (with compression) across runs while a new-session automation starts fresh — verified across consecutive runs.
- **SC-017**: Agent-logged learnings persist to the memory area and are available to a later session/run without manual copy-paste — verified across a restart and a subsequent run.
- **SC-018**: The context budget keeps every consumer (persona, skills, memory, conversation, tool output) within its allocation so adding memory never removes core functionality — verified by loading large memory and confirming the agent still completes a baseline task.
- **SC-019**: A session/automation uses the selected persona (or the editable default when unspecified), and edits to a persona take effect on the next run — verified by editing the default and creating a second persona.
- **SC-020**: Clicking a file reference in the UI opens that file in VS Code — verified for an agent-produced file; a clear message appears if VS Code is unavailable.
- **SC-021**: The agent cannot read any secret value — across the secret-isolation test suite, 0 secret values appear in agent context, prompts, tool inputs the agent controls, logs, or transcripts; secrets are injected only by the runtime into outbound connections.
- **SC-022**: The agent can add/configure an MCP server or tool during a session, but the corresponding secret must still be entered by the user before that capability connects — verified end to end.

## Assumptions

- **App identity**: The product is renamed conceptually to an "LM Studio Agent Runtime"; the Documents working directory is `Documents\LMStudioClaw\` with subfolders `skills/`, `tools/`, `workspace/`, a memory area for agent learnings (e.g. `memory/`), and a file `mcp.json`. The `workspace/` folder is the agent's default read/write scope.
- **Platform**: Windows-only, consistent with the current app (system tray, `%APPDATA%`, Windows notifications).
- **Model backend**: LM Studio remains the only model backend, reached over its local API; connection defaults to localhost and is overridable in settings.
- **Concurrency**: Exactly one active session at a time (limited local resources; never two models loaded at once). Additional manual sessions and triggered automations enter a FIFO queue and run in order; the queue is visible and queued items can be cancelled before they start.
- **Session interaction model**: Sessions are interactive and multi-turn. While generating, Enter steers the active turn and Alt+Enter queues the next turn; a Stop-generating control halts a turn without ending the session. The model stays loaded for the whole session and unloads on explicit end or idle timeout. Automations reuse the same engine unattended.
- **Context compression**: When context usage reaches ~90% of the active model's context length (threshold configurable), earlier history is automatically summarized/compressed to continue the session.
- **Scheduling & continuity**: Automations use either Daily (multi-weekday + time) or Interval (every X min/hr/day) schedules, and each chooses new-session or persistent-session mode. Persistent sessions rely on auto-compression to remain affordable. The agent can log learnings to a Documents memory area to carry insights forward, and a token budget allocates the context window across persona, skills, memory, conversation, and tool output so memory never starves functionality.
- **Personas & file opening**: A built-in **default persona** (neutral, task-focused) is user-editable; users can add more personas in Settings and select one per session/automation (default if unspecified). The app assumes VS Code is the user's editor — file links/references open in VS Code.
- **Secrets & capability authoring**: Secrets may be stored as plaintext locally (single-user machine) but in a **separate area outside any agent-accessible path**, never injected into agent context/logs. The **agent may add/configure MCPs, tools, and skills** itself; only entering secret values is reserved to the user.
- **Scheduling**: An in-app scheduler is sufficient because the controller is assumed running whenever the PC is on; there is no OS-level scheduler dependency. Missed-run detection compares each automation's expected fire time against its last recorded run on startup.
- **Notifications**: Native Windows toast notifications are the default channel.
- **Skill format**: A skill is a Markdown `SKILL.md` (Claude/OpenClaw-style) optionally accompanied by scripts in its folder that the skill instructs the agent to call; skills are not MCP servers.
- **Tool trust**: Custom Python tools run in-process with the app's privileges; the warning + explicit trust gate is the v1 safeguard. Sandboxing/isolation is noted as a future enhancement.
- **Safety reviewer**: Automated detection of prompt injection or malicious code in tools/skills is explicitly deferred; v1 only ensures the content is structured and accessible so a reviewer can be added later.
- **UI stack**: The control panel is a local web UI served by the controller and opened in the user's default browser from the tray; the prior desktop (tkinter) window is replaced.
- **Single user, local**: The app serves a single local user on `localhost`; no multi-user, authentication, or remote-access requirements in this version.
- **Existing functionality preserved**: Today's model discovery, load/unload, warmup, and per-model context-length preferences are retained under Settings → Advanced → Model Management.

## Out of Scope (this version)

- Automated prompt-injection / malware detection for custom tools and skills (foundation only; detection deferred).
- Sandboxing or OS-level isolation of custom Python tools.
- Non-Windows platforms.
- Model backends other than LM Studio.
- Multi-user, authentication, or remote/networked access to the control panel.
- Cloud sync of sessions, skills, or settings.
- Headless/CLI-only operation of the agent runtime.

## Dependencies

- A reachable LM Studio instance exposing its local model-management and OpenAI-compatible APIs.
- A writable user Documents directory for the skills/tools/workspace structure.
- Windows notification and system-tray facilities.
- VS Code installed and launchable from the system, for opening file references/links.
