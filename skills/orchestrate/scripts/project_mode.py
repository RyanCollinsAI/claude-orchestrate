"""Print how the repo that owns a path ships: `<mode> <yolo>`.

  py project_mode.py <path>

mode is no-mistakes | direct-PR | local-only. yolo is `on` or `off`: on means the agent may merge green
work itself. The answer comes from ../projects.json. The idea is First Mate's fm-project-mode.sh (MIT);
the code is ours.

The owning repo is the nearest ancestor with a `.git`. A git worktree has a `.git` FILE that points at
the main repo, so it resolves to the main repo and uses that repo's entry. A repo missing from
projects.json gets the file's `default` (local-only off, what every repo does today) and a note on
stderr. A path outside any repo exits 2.
"""
import json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
REGISTRY = os.path.join(HERE, "..", "projects.json")
MODES = ("no-mistakes", "direct-PR", "local-only")


def norm(p):
    return os.path.normcase(os.path.realpath(os.path.expanduser(p)))


def repo_root(path):
    """Main repo root that owns `path`, or None. A worktree resolves to the repo it was cut from."""
    d = os.path.abspath(os.path.expanduser(path))
    if not os.path.isdir(d):
        d = os.path.dirname(d)
    while True:
        dot = os.path.join(d, ".git")
        if os.path.isdir(dot):
            return d
        if os.path.isfile(dot):
            return _from_git_file(d, dot)
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent


def _from_git_file(d, dot):
    with open(dot, encoding="utf-8") as f:
        line = f.readline().strip()
    if not line.startswith("gitdir:"):
        return d
    gitdir = os.path.join(d, line[len("gitdir:"):].strip())
    commondir = os.path.join(gitdir, "commondir")
    if not os.path.isfile(commondir):
        return d  # separate git dir or submodule: this tree is the repo
    with open(commondir, encoding="utf-8") as f:
        common = os.path.normpath(os.path.join(gitdir, f.readline().strip()))
    if os.path.basename(common) == ".git":
        return os.path.dirname(common)
    return d


def load(registry=REGISTRY):
    with open(registry, encoding="utf-8") as f:
        data = json.load(f)
    for p in data["projects"]:
        if p["mode"] not in MODES:
            raise ValueError(f"{p['name']}: unknown mode {p['mode']!r}, want one of {MODES}")
    if data["default"]["mode"] not in MODES:
        raise ValueError(f"default: unknown mode {data['default']['mode']!r}")
    return data


def resolve(path, registry=REGISTRY):
    """(mode, yolo, project_name_or_None, repo_root_or_None) for the repo that owns `path`."""
    root = repo_root(path)
    data = load(registry)
    if root is None:
        return data["default"]["mode"], bool(data["default"]["yolo"]), None, None
    for p in data["projects"]:
        if norm(p["path"]) == norm(root):
            return p["mode"], bool(p["yolo"]), p["name"], root
    return data["default"]["mode"], bool(data["default"]["yolo"]), None, root


def main(argv):
    if len(argv) != 2 or argv[1] in ("-h", "--help"):
        print(__doc__)
        return 0 if len(argv) == 2 else 2
    mode, yolo, name, root = resolve(argv[1])
    if root is None:
        print(f"project_mode: {argv[1]} is not inside a git repo", file=sys.stderr)
        return 2
    if name is None:
        print(f"project_mode: {root} is not in projects.json, using the default", file=sys.stderr)
    print(f"{mode} {'on' if yolo else 'off'}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
