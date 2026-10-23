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
API_URL = f"https://api.github.com/users/{OWNER}/repos"

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
        url = f"{API_URL}?per_page=100&page={page}&type=owner"
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
            raise ValueError("GitHub API returned an invalid response")

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

    return sorted(repositories, key=lambda item: item["name"].lower())


def get_previous_state():
    state = read_json(STATE_PATH, {})
    if not isinstance(state, dict):
        state = {}

    epoch = state.get("epoch", 0)
    if (
        not isinstance(epoch, int)
        or isinstance(epoch, bool)
        or epoch < 0
    ):
        epoch = 0

    return state, epoch


def append_event(event):
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with HISTORY_PATH.open("a", encoding="utf-8") as file:
        file.write(
            json.dumps(
                event,
                sort_keys=True,
                ensure_ascii=False,
            )
            + "\n"
        )


def run():
    previous, previous_epoch = get_previous_state()
    timestamp = datetime.now(timezone.utc).isoformat()
    epoch = previous_epoch + 1

    try:
        repositories = fetch_repositories()
        scan_status = "success"
        error = None
    except (
        OSError,
        ValueError,
        KeyError,
        urllib.error.URLError,
    ) as exc:
        repositories = []
        scan_status = "failed"
        error = f"{type(exc).__name__}: {exc}"

    # A failed scan is not allowed to overwrite the last good state.
    if scan_status == "failed":
        event = {
            "contract": CONTRACT,
            "identity": IDENTITY,
            "epoch": epoch,
            "timestamp": timestamp,
            "status": "observation_failed",
            "previous_quantum_cid": previous.get("quantum_cid"),
            "error": error,
        }
        append_event(event)
        print(json.dumps(event, indent=2, ensure_ascii=False))
        print(error, file=sys.stderr)
        return 1

    observation = {
        "contract": CONTRACT,
        "identity": IDENTITY,
        "epoch": epoch,
        "timestamp": timestamp,
        "status": "observed",
        "repositories_observed": len(repositories),
        "repositories": repositories,
        "previous_quantum_cid": previous.get("quantum_cid"),
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
        "scan_status": "success",
        "repositories_observed": len(repositories),
        "quantum_cid": cid,
    }

    # Write the event first, then publish the successful state.
    append_event(packet)
    write_json(OUTPUT_PATH, packet)
    write_json(STATE_PATH, state)

    print(json.dumps(state, indent=2, ensure_ascii=False))
    print("KERNEL_STATUS=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
