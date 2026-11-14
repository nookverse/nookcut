# Design: offline mode, optional online research, and `auto_edit`

Status: **approved and implemented** (2026-11-14). The module is `copywriting.py` (not `copy.py`,
which would clash with the standard library) and packs live in `copydata/`.

## Goal

NookCut makes a finished, publish-ready short with **no internet** and driven by **any MCP agent**,
including small local models. Going online is **optional and opt-in**: when the user allows it and
the agent has a web-search tool, the agent can research trends to improve hooks, titles and hashtags.
Nothing ever depends on it.

## Principles

1. **Offline is the default and always complete.** Every step, including captions, titles,
   description, hashtags and posting times, has a built-in local answer.
2. **Online is an upgrade the user turns on.** It's off unless the user says yes. NookCut itself
   still makes no network calls; the *agent* does the research with its own tool.
3. **A weak model can't get it wrong.** There's one call that does everything, inputs are forgiving,
   output is short, and every result says what to do next.
4. **A strong agent keeps full control.** All existing step-by-step tools stay as they are.

## What is online-dependent today

| Step | Today | Offline answer |
|---|---|---|
| Analyze, plan, music, render, Resolve, posting times | local | unchanged |
| Hook / segment captions | template defaults in `story.py`, then rewritten from web research | copy library (new) |
| Titles, description, hashtags | agent must research and invent them | copy library (new) |
| `write_publish_kit` | titles, description and hashtags are required | optional; filled from the copy library |
| Playbook step 2 "Research" | required | optional, only when `online` is on |

## Changes

### 1. Brief: `online` switch

```python
online: bool = False   # user allowed the agent to research on the web
```

- Set by `create_project(..., online=False)` and `nookcut new/make --online`.
- The playbook asks the user **once**, and only if the agent has a search tool: "Want me to check
  current trends online to sharpen the titles and hashtags? (optional)". If the answer is no, or
  the agent has no search tool, everything stays offline. This is never re-asked in the same project.
- `publish_kit.md` records the source of the copy: "built-in library (offline)" or "web research
  (date)".

### 2. New module `copywriting.py`: the offline copy library

Data lives in `src/nookcut/copydata/<lang>.json` (stdlib `json`, shipped in the package):

```json
{
  "hooks": {
    "comeback":   ["I DIED {n} TIMES|TO GET THIS", "{n} DEATHS.|ONE CHANCE.", "IT TOOK ME|{n} TRIES"],
    "victory":    ["NO HIT?|WATCH THIS", "FIRST TRY|(NO WAY)"],
    "highlights": ["WAIT FOR|THE END", "THIS GOT|INSANE"],
    "talking":    ["NOBODY TALKS|ABOUT THIS", "STOP DOING|THIS"],
    "tutorial":   ["DO THIS|IN {secs} SECONDS", "THE TRICK|PROS USE"],
    "sports":     ["WATCH THE|LAST SECOND", "HOW DID|THAT GO IN"]
  },
  "kinds":   {"death": ["DEATH {i}"], "card": ["ATTEMPT #{n1}"], "end": ["COULD YOU|DO IT?", "..."]},
  "titles":  ["{game}: {outcome} after {n} attempts", "I finally beat {title} ({game})", "..."],
  "description": "{hook_line}\n\n{title} - {game}.\n{cta}",
  "cta":     ["Comment your attempts.", "Which part was best?"],
  "hashtags": {"base": ["#shorts"], "gameplay": ["#gaming", "#gameplay"], "tiktok": ["#fyp"], "...": []}
}
```

- **Slots** come from the brief and the analysis: `{n}` deaths, `{i}`, `{n1}`, `{game}`, `{title}`,
  `{outcome}`, `{secs}`, `{label}` (the user's key-moment label). A pattern whose slots can't be
  filled is skipped.
- **Game and topic hashtags** are built from the brief: `game="Elden Ring"` becomes `#eldenring`,
  `#eldenringclips`. These are labelled *generic, not trending*.
- **Deterministic choice**: picks are seeded by the project path, so re-planning gives the same
  copy, and `suggest_copy` returns alternatives to switch to.
- **Fits on screen**: every caption is wrapped and checked against the line limit for the
  orientation (16 characters for vertical), using the existing `story.wrap`.
- **Languages**: `en.json` first. If no pack exists for the brief's `language`, NookCut falls back
  to English with a warning, and the agent may translate (local models can translate too).
- `story.py` takes its default captions from the copy library instead of hard-coded strings, so
  plans vary and every caption has one source.

### 3. MCP tools

**New: `suggest_copy(project_dir, count=5)`** (read-only, instant, offline)

```json
{"source": "built-in library (offline)",
 "captions": [{"i": 0, "kind": "hook", "current": "I DIED 7 TIMES|TO GET THIS",
               "options": ["7 DEATHS.|ONE CHANCE.", "IT TOOK ME|7 TRIES"]}],
 "titles": ["...5 options..."], "description": "...", "hashtags": {"youtube_shorts": ["..."], "tiktok": ["..."]},
 "file_name": "elden-ring-malenia-7-deaths",
 "next": "Pick options and apply them with update_edit, or call render_video to keep the current captions."}
```

**New: `auto_edit(source, title, project_dir="", video_type="", game="", orientation="vertical",
max_duration=60, region="", music="ai", wait=True)`** runs the whole pipeline in one call:

create project → analyze → plan → offline copy → music → render → Resolve export → publish kit
(posting times included when a region is given).

- `project_dir` defaults to `<source folder>/<slug(title)>-nookcut`. `video_type` is
  auto-detected when empty: `gameplay` if `game` is set, otherwise `generic`.
- It runs as a single tracked, cancellable job (`job_status` shows each stage). With `wait=false`
  it returns a `job_id`.
- The result is short: `{"video", "publish_kit", "preview", "resolve_script", "length_s",
  "summary": "45 s vertical short, 7 deaths → victory, original taiko score",
  "next": "Show the user the video and publish_kit.md. To change captions: suggest_copy / update_edit, then render_video."}`
- CLI `nookcut make` and `auto_edit` share one function, `pipeline.make_all()`, so the CLI and the
  MCP tool can't drift apart.

**Changed: `write_publish_kit`**: `titles`, `description` and `hashtags` become optional. Anything
left empty is filled from `suggest_copy`. Agents that did research pass their own values.

**Changed: `create_project`**: adds `online: bool = False`.

**For small models, on every tool:**
- **Forgiving inputs** through one normaliser:
  - orientation: `"Vertical"`, `"9:16"`, `"portrait"` → `vertical`
  - platforms: `"shorts"`, `"reels"`, `"tt"` → canonical names
  - durations: `"60s"`, `"1m"`, `"1:00"` → 60
  - video type: `"game"` → `gameplay`

  Unknown values raise an `InputError` that lists the valid choices.
- **A `next` field** in every result, one plain-English sentence.
- **Shorter docstrings**: under ~3 lines each; the details live in the skill. This lowers prompt
  tokens for local models with small context windows.

### 4. Playbook (`SKILL.md`)

- A new top section, **"Quick path (any agent, offline)"**: call `auto_edit`, show the user the
  video and the kit, offer changes. This is the recommended path for local or small models.
- Step 2 becomes **"Research (optional)"**: only when `online` is true *and* the agent can search.
  Otherwise call `suggest_copy` and choose from its options.
- Drop the "requires web research" wording from the description, and keep a line saying research
  improves results when available.
- Sync all copies as AGENTS.md describes.

### 5. Environment check

`check_environment` adds `"offline_ready": true` when everything needed is local (always true once
ffmpeg passes), and `"online_optional": ["trend research (by your agent, if you allow it)"]`.

## Tests

- **Unit (`test_core.py`)**:
  - every template and kind has at least one pattern
  - slot filling skips patterns with missing slots
  - every generated caption fits the vertical line limit
  - copy is deterministic per project
  - hashtags are well-formed (`#` + lowercase alphanumerics, no duplicates)
  - an unknown language falls back to English with a warning
  - the normaliser maps aliases correctly
  - `write_publish_kit` works with only `video_path`
- **MCP smoke**: `suggest_copy` and `auto_edit` (stdio and HTTP), with a full `auto_edit` run on the
  test clip.
- **Offline proof**: run the smoke test with networking blocked (for example
  `sandbox-exec` on macOS denying network) and assert success.

## Rollout order

1. `copy.py` + `en.json` + `story.py` refactor + tests
2. `suggest_copy`, the optional `write_publish_kit` fields, the normaliser, `next` fields
3. `pipeline.make_all()` + `auto_edit` + switching the CLI `make` over to it
4. `online` in the brief, playbook rewrite, skill copies, environment check
5. Offline smoke test, then a real clip end to end with `nookcut service restart`

## Owner decisions

1. `online` defaults off; the agent asks once per project, and only if it can search.
2. English ships built in; users choose other languages and add packs themselves
   (`nookcut copy init <lang>` writes `~/.nookcut/copy/<lang>.json` to translate; partial packs fall
   back to English).
3. A user-run `nookcut models install` is approved as NookCut's only network action. It will be
   built together with the Whisper/Piper features that need it.
4. `auto_edit` waits for the finished video by default (`wait=false` returns a job).
