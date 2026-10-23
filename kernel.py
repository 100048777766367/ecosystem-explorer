
import hashlib
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OWNER = os.environ.get("REPO_OWNER", "100048777766367")
IDENTITY = os.environ.get(
    "GITHUB_REPOSITORY",
    f"{OWNER}/ecosystem-explorer",
)
CONTRACT = "ecosystem-explorer.v1"

STATE_PATH = ROOT / "synapses" / "state.json"
OUTPUT_PATH = ROOT / "outputs" / "synapse_forward.json"
HISTORY_PATH = ROOT / "memory" / "events.jsonl"


def read_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def fetch_repositories():
    repositories = []
    page = 1

    while True:
        url = (
            f"https://api.github.com/users/{OWNER}/repos"
            f"?per_page=100&page={page}&type=owner"
        )
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "ecosystem-explorer",
            },
        )

        with urllib.request.urlopen(request, timeout=15) as response:
            batch = json.loads(response.read().decode("utf-8"))

        if not isinstance(batch, list):
            raise ValueError("GitHub API returned an invalid repository list")

        repositories.extend(
            {
                "name": repo["name"],
                "url": repo["html_url"],
                "branch": repo.get("default_branch"),
                "updated_at": repo.get("updated_at"),
            }
            for repo in batch
        )

        if len(batch) < 100:
            break

        page += 1

    return sorted(repositories, key=lambda repo: repo["name"].lower())


def previous_state():
    state = read_json(STATE_PATH, {})
    if not isinstance(state, dict):
        state = {}

    epoch = state.get("epoch", 0)
    if not isinstance(epoch, int) or isinstance(epoch, bool) or epoch < 0:
        epoch = 0

    return state, epoch


def append_event(event):
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with HISTORY_PATH.open("a", encoding="utf-8", newline="\n") as history:
        history.write(
            json.dumps(event, sort_keys=True, ensure_ascii=False) + "\n"
        )


def run():
    old_state, old_epoch = previous_state()
    epoch = old_epoch + 1
    timestamp = datetime.now(timezone.utc).isoformat()

    previous_cid = old_state.get("quantum_cid")
    observation_error = None
    repositories = []

    try:
        repositories = fetch_repositories()
        scan_status = "success"
    except (
        OSError,
        ValueError,
        KeyError,
        urllib.error.URLError,
    ) as error:
        scan_status = "failed"
        observation_error = f"{type(error).__name__}: {error}"

    observation = {
        "contract": CONTRACT,
        "identity": IDENTITY,
        "epoch": epoch,
        "timestamp": timestamp,
        "scan_status": scan_status,
        "repositories_observed": len(repositories),
        "repositories": repositories,
        "previous_quantum_cid": previous_cid,
        "error": observation_error,
    }

    canonical = json.dumps(
        observation,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")

    cid = hashlib.sha256(canonical).hexdigest()

    packet = {
        **observation,
        "quantum_cid": cid,
    }

    state = {
        "identity": IDENTITY,
        "epoch": epoch,
        "last_run": timestamp,
        "scan_status": scan_status,
        "repositories_observed": len(repositories),
        "quantum_cid": cid,
        "previous_quantum_cid": previous_cid,
    }

    # Persist the observation even when the remote scan fails.
    write_json(OUTPUT_PATH, packet)
    append_event(packet)
    write_json(STATE_PATH, state)

    print(json.dumps(state, indent=2, ensure_ascii=False))

    if scan_status != "success":
        print(f"Observation failed: {observation_error}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(run())
