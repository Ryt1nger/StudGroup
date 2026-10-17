"""Merge the stored results of package runs into one deduplicated view. Pure, offline.

Neighbouring packages overlap by their read-only edge context, and Pass 2 extracts from every
message it is given, so the same item can be found by two packages. Items are the dicts a package
run stores (``accepted`` / ``candidates`` with ``evidence``). Two items are the same when the
messages they cite overlap enough; the copy found where most of its sources are targets wins.
A card beats a candidate that cites the same messages.
"""

OVERLAP = 0.5


def _ids(item):
    return {ref["telegram_message_id"] for ref in item["evidence"]}


def _same(left, right):
    a, b = _ids(left), _ids(right)
    return bool(a and b) and len(a & b) / min(len(a), len(b)) >= OVERLAP


def pick_packages(result_files):
    """One outcome per package sequence: a completed one beats a failed one, newest wins."""
    best = {}
    for report in result_files:  # oldest first
        for package in report["packages"]:
            current = best.get(package["sequence"])
            if (
                current is None
                or package["state"] == "completed"
                or current["state"] != "completed"
            ):
                best[package["sequence"]] = package | {"_label": report.get("label")}
    return [best[key] for key in sorted(best)]


def _score(item, package):
    targets = set(package["target_ids"])
    ids = _ids(item)
    return (len(ids & targets) / len(ids), item.get("confidence", 0), len(ids))


def merge(packages):
    """Return (items, per_target). ``items`` are unique, each with ``status`` card/candidate,
    ``package`` and ``seen_in``; ``per_target`` maps a target id to card/candidate/empty/
    not_analysed over the final unique items."""
    pool = []
    for package in packages:
        if package["state"] != "completed":
            continue
        for item in package.get("accepted", []):
            pool.append((item | {"status": "card"}, package))
        for item in package.get("candidates", []):
            if item.get("source_status") == "defaulted_to_target":
                continue  # sources not confirmed: attributes nothing
            pool.append((item | {"status": "candidate"}, package))
    clusters = []
    for item, package in pool:
        for cluster in clusters:
            if any(_same(item, other) for other, _ in cluster):
                cluster.append((item, package))
                break
        else:
            clusters.append([(item, package)])
    items = []
    for cluster in clusters:
        cards = [pair for pair in cluster if pair[0]["status"] == "card"]
        chosen = max(cards or cluster, key=lambda pair: _score(*pair))
        item, package = chosen
        items.append(
            item
            | {
                "package": package["sequence"],
                "seen_in": sorted({pkg["sequence"] for _, pkg in cluster}),
            }
        )
    # A candidate that cites the same messages as a card is the same finding.
    cards = [row for row in items if row["status"] == "card"]
    items = [
        row
        for row in items
        if row["status"] == "card" or not any(_same(row, card) for card in cards)
    ]
    items.sort(key=lambda row: (min(_ids(row)), row["status"]))
    per_target = {}
    for package in packages:
        for target in package["target_ids"]:
            per_target[target] = "empty" if package["state"] == "completed" else "not_analysed"
    for row in items:
        for target in _ids(row) & per_target.keys():
            if row["status"] == "card" or per_target[target] == "empty":
                per_target[target] = row["status"]
    return items, per_target
