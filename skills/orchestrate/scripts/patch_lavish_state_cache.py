"""Patch lavish-axi so SessionStore keeps state.json in memory instead of read+parse+rewrite per op.

Stock SessionStore.readState reads and JSON.parses the whole state.json on every operation (every page
load, SSE connect, poll start and finish, prompt, reply and end), and writeState pretty-prints and
rewrites all of it. With ~30 pollers and a few open tabs that is hundreds of 0.5 MB parses and writes a
minute. This keeps one parsed copy in memory, flushes it to disk at most every 250 ms, and flushes
synchronously on shutdown and on process exit. A change to state.json made from outside (a stat
mtime:size stamp that differs from the one this process last wrote or read) is re-read.

Layers on top of whatever readState/writeState exist: it renames them to readStateFromDisk /
writeStateToDisk and wraps them, so it keeps the atomic write, tolerant read and ended-session cap from
patch_lavish_atomic_state.py. Run it AFTER that script.

On 0.1.84 the in-process write serialization comes from upstream's AsyncMutex around every SessionStore
read-modify-write (patch_lavish_server_leak.py is retired), so the cache sees one operation at a time.

cli.mjs is loaded once per server process. A running Bridge keeps the old code until it restarts.
Sessions reachable only through podium_home.py / podium_watch.py (they read state.json from disk) see
a change up to 250 ms late.

    py patch_lavish_state_cache.py            # patch, or say "already patched"
    py patch_lavish_state_cache.py --check    # exit 0 if patched, 1 if not
"""
import os
import sys

CLI = os.environ.get("LAVISH_CLI_MJS") or os.path.expanduser(
    r"~\AppData\Roaming\npm\node_modules\lavish-axi\dist\cli.mjs")
MARK = "/* patched: state stays in memory, flushed to disk at most every 250 ms (was read+parse+rewrite per op) */"
ATOMIC_MARK = "/* patched: atomic state.json write, tolerant read, ended-session cap */"

EDITS = [
    (
        "var SessionStore = class {\n",
        'import { stat as statFile } from "node:fs/promises";\n'
        'import { writeFileSync as writeFileSyncNow } from "node:fs";\n'
        "var SessionStore = class {\n",
    ),
    # Rename first: edits apply in list order, and the wrapper inserted below has its own
    # "async writeState(state) {" that this rename must not touch.
    (
        "  async writeState(state) {\n",
        "  async writeStateToDisk(state) {\n",
    ),
    (
        "  async readState() {\n",
        "  " + MARK + "\n"
        "  async readState() {\n"
        "    if (this.cache && this.cacheDirty) return this.cache;\n"
        "    let stamp = null;\n"
        "    try {\n"
        "      const st = await statFile(this.file);\n"
        "      stamp = `${st.mtimeMs}:${st.size}`;\n"
        "    } catch (error) {\n"
        "      if (!error || error.code !== \"ENOENT\") throw error;\n"
        "    }\n"
        "    if (this.cache && (this.cacheDirty || stamp === this.cacheStamp)) return this.cache;\n"
        "    if (!this.loading) {\n"
        "      const load = this.readStateFromDisk();\n"
        "      this.loading = load;\n"
        "      const settle = () => {\n"
        "        if (this.loading === load) this.loading = null;\n"
        "      };\n"
        "      load.then((state) => {\n"
        "        settle();\n"
        "        if (!this.cacheDirty) {\n"
        "          this.cache = state;\n"
        "          this.cacheStamp = stamp;\n"
        "        }\n"
        "      }, settle);\n"
        "    }\n"
        "    return this.loading;\n"
        "  }\n"
        "  async writeState(state) {\n"
        "    this.cache = state;\n"
        "    this.cacheDirty = true;\n"
        "    if (!this.flushTimer) {\n"
        "      this.flushTimer = setTimeout(() => {\n"
        "        this.flushTimer = null;\n"
        "        this.flush().catch(() => {\n"
        "        });\n"
        "      }, 250);\n"
        "      this.flushTimer.unref?.();\n"
        "      if (!this.exitHook) {\n"
        "        this.exitHook = () => this.flushSync();\n"
        "        process.on(\"exit\", this.exitHook);\n"
        "      }\n"
        "    }\n"
        "  }\n"
        "  flush() {\n"
        "    this.flushing = (this.flushing || Promise.resolve()).then(async () => {\n"
        "      if (!this.cacheDirty) return;\n"
        "      this.cacheDirty = false;\n"
        "      try {\n"
        "        await this.writeStateToDisk(this.cache);\n"
        "        const st = await statFile(this.file);\n"
        "        if (!this.cacheDirty) this.cacheStamp = `${st.mtimeMs}:${st.size}`;\n"
        "      } catch (error) {\n"
        "        this.cacheDirty = true;\n"
        "        throw error;\n"
        "      }\n"
        "    });\n"
        "    return this.flushing;\n"
        "  }\n"
        "  flushSync() {\n"
        "    if (!this.cacheDirty || !this.cache) return;\n"
        "    this.cacheDirty = false;\n"
        "    writeFileSyncNow(this.file, `${JSON.stringify(this.cache, null, 2)}\n"
        "`);\n"
        "    this.cacheStamp = null;\n"
        "  }\n"
        "  async readStateFromDisk() {\n",
    ),
    (
        "    if (shuttingDown) return;\n"
        "    shuttingDown = true;\n",
        "    if (shuttingDown) return;\n"
        "    shuttingDown = true;\n"
        "    store.flushSync();\n",
    ),
]


def main():
    check = "--check" in sys.argv
    if not os.path.exists(CLI):
        print(f"not found: {CLI}")
        return 1
    src = open(CLI, encoding="utf-8").read()
    patched = MARK in src
    if check:
        print("cli.mjs state-cache: " + ("patched" if patched else "not patched"))
        return 0 if patched else 1
    if patched:
        print("cli.mjs state-cache: already patched")
        return 0
    if ATOMIC_MARK not in src:
        print("cli.mjs state-cache: run patch_lavish_atomic_state.py first (this layers on its readState/writeState)")
        return 1
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
    print(f"patched state-cache in {CLI}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
