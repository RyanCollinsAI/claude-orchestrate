"""Patch lavish-axi so ~/.lavish-axi/state.json cannot be corrupted, and cannot grow without end.

Root cause (2026-10-01 14:08): SessionStore.writeState did a plain writeFile (truncate, then write).
Two writers overlapped (server + CLI both use SessionStore), the shorter write landed on top of the
longer one, and 72 stray chars stayed after the closing brace. JSON.parse then threw on every call,
so every lavish-axi command failed and the live pages returned 500.

Checked against 0.1.84 (2026-10-08): upstream now serializes every SessionStore read-modify-write
inside the server under an AsyncMutex, so two requests no longer erase each other. That does NOT cover
this bug: writeState is still a plain writeFile (a reader or a crash mid-write sees a truncated file),
readState is still a bare JSON.parse, and nothing caps ended sessions. All three sub-patches stay.

This patches the installed cli.mjs:

  1. writeState   writes a temp file in the same dir, then renames it over state.json (atomic).
                  Rename is retried a few times because Windows can refuse it while a reader has the file open.
                  The stock method is kept under the name writeStateStock (unused).
  2. readState    if JSON.parse fails, it reads the first complete JSON value and ignores what follows.
  3. prune        ended sessions are capped at 30 (newest by updated_at); open and feedback sessions are never dropped.

The anchors name no esbuild import alias (readFile3, writeFile2, ...) because those renumber whenever
upstream adds an import; the patch brings its own fs/promises import instead.

Not covered: two processes that each read, change, and write can still lose one update. That is a
different bug and it does not corrupt the file.

cli.mjs is loaded once per server process. A running Bridge keeps the old code until it restarts.
CLI calls (lavish-axi ...) start a new node process each time, so they pick up the patch at once.

    py patch_lavish_atomic_state.py            # patch, or say "already patched"
    py patch_lavish_atomic_state.py --check    # exit 0 if patched, 1 if not
"""
import os
import sys

CLI = os.environ.get("LAVISH_CLI_MJS") or os.path.expanduser(
    r"~\AppData\Roaming\npm\node_modules\lavish-axi\dist\cli.mjs")
MARK = "/* patched: atomic state.json write, tolerant read, ended-session cap */"

IMPORT_ANCHOR = "var SessionStore = class {\n"
IMPORT_NEW = (
    'import { writeFile as writeFileAtomic, rename as renameAtomic, unlink as unlinkAtomic } from "node:fs/promises";\n'
    + MARK + "\n"
    "var MAX_ENDED_SESSIONS = 30;\n"
    "var stateTmpCounter = 0;\n"
    "function firstJsonValue(raw) {\n"
    "  const start = raw.search(/\\S/);\n"
    "  if (start < 0) throw new SyntaxError(\"state.json is empty\");\n"
    "  let depth = 0, inString = false, escaped = false;\n"
    "  for (let i = start; i < raw.length; i++) {\n"
    "    const c = raw[i];\n"
    "    if (inString) {\n"
    "      if (escaped) escaped = false;\n"
    "      else if (c === \"\\\\\") escaped = true;\n"
    "      else if (c === '\"') inString = false;\n"
    "    } else if (c === '\"') inString = true;\n"
    "    else if (c === \"{\" || c === \"[\") depth++;\n"
    "    else if (c === \"}\" || c === \"]\") {\n"
    "      depth--;\n"
    "      if (depth === 0) return JSON.parse(raw.slice(start, i + 1));\n"
    "    }\n"
    "  }\n"
    "  throw new SyntaxError(\"state.json has no complete JSON value\");\n"
    "}\n"
    "function pruneEnded(sessions) {\n"
    "  const ended = Object.entries(sessions).filter(([, s]) => s && s.status === \"ended\");\n"
    "  if (ended.length <= MAX_ENDED_SESSIONS) return sessions;\n"
    "  ended.sort((a, b) => String(b[1].updated_at || \"\").localeCompare(String(a[1].updated_at || \"\")));\n"
    "  for (const [key] of ended.slice(MAX_ENDED_SESSIONS)) delete sessions[key];\n"
    "  return sessions;\n"
    "}\n"
    + IMPORT_ANCHOR
)

READ_ANCHOR = (
    "      const parsed = JSON.parse(raw);\n"
    "      const state = { sessions: parsed.sessions || {} };\n"
)
READ_NEW = (
    "      let parsed;\n"
    "      try {\n"
    "        parsed = JSON.parse(raw);\n"
    "      } catch (parseError) {\n"
    "        parsed = firstJsonValue(raw);\n"
    "      }\n"
    "      const state = { sessions: parsed.sessions || {} };\n"
)

WRITE_ANCHOR = "  async writeState(state) {\n"
WRITE_NEW = (
    "  async writeState(state) {\n"
    "    pruneEnded(state.sessions);\n"
    "    const tmp = `${this.file}.${process.pid}.${++stateTmpCounter}.tmp`;\n"
    "    await writeFileAtomic(tmp, `${JSON.stringify(state, null, 2)}\n"
    "`);\n"
    "    for (let attempt = 0; ; attempt++) {\n"
    "      try {\n"
    "        await renameAtomic(tmp, this.file);\n"
    "        return;\n"
    "      } catch (error) {\n"
    "        const busy = error && (error.code === \"EPERM\" || error.code === \"EBUSY\" || error.code === \"EACCES\");\n"
    "        if (!busy || attempt >= 20) {\n"
    "          await unlinkAtomic(tmp).catch(() => {});\n"
    "          throw error;\n"
    "        }\n"
    "        await new Promise((resolve) => setTimeout(resolve, 10 + attempt * 10));\n"
    "      }\n"
    "    }\n"
    "  }\n"
    "  async writeStateStock(state) {\n"
)

EDITS = [
    (IMPORT_ANCHOR, IMPORT_NEW),
    (READ_ANCHOR, READ_NEW),
    (WRITE_ANCHOR, WRITE_NEW),
]


def main():
    check = "--check" in sys.argv
    if not os.path.exists(CLI):
        print(f"not found: {CLI}")
        return 1
    src = open(CLI, encoding="utf-8").read()
    patched = MARK in src
    if check:
        print("cli.mjs atomic-state: " + ("patched" if patched else "not patched"))
        return 0 if patched else 1
    if patched:
        print("cli.mjs atomic-state: already patched")
        return 0
    for anchor, _ in EDITS:
        n = src.count(anchor)
        if n != 1:
            print(f"anchor count {n} != 1 for {anchor[:70]!r} (this lavish-axi version moved the code)")
            return 1
    for anchor, new in EDITS:
        src = src.replace(anchor, new)
    if MARK not in src:
        print("marker did not land; refusing to write")
        return 1
    open(CLI, "w", encoding="utf-8", newline="\n").write(src)
    print(f"patched atomic-state in {CLI}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
