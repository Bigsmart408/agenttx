#!/usr/bin/env python3
"""Plot the audited three-way process-state experiment in the paper style."""
from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
import shutil
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from motivation.plot_motivation_four import CM, STYLES, configure_style

DATA = ROOT / "review/process-lifecycle-fixed-20261110-r1"
STEM = "FIG-Process-State-Lifecycle"
ARMS = ("retain_all", "clear_all", "clean")
LABELS = ("Retain all", "Clear all", "Clean")
LINE_STYLES = {
    "retain_all": dict(STYLES["ideal_oracle"], markersize=4, linewidth=1.35),
    "clear_all": dict(STYLES["temporal_step"], markersize=4, linewidth=1.35),
    "clean": dict(STYLES["root_only"], marker="^", linestyle="--",
                  markerfacecolor="none", markersize=4, linewidth=1.35),
}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_data() -> dict:
    report = json.loads((DATA / "audit.json").read_text())
    with (DATA / "runs.csv").open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == report["audited"] == 630
    assert report["matched_triples"] == 210
    assert report["model_calls"] == report["model_api_requests"] == 0
    baseline = sum(int(row["warm_items"]) for row in rows if row["arm"] == "clean")
    arm_data = []
    for arm, label in zip(ARMS, LABELS):
        group = [row for row in rows if row["arm"] == arm]
        summary = next(item for item in report["arms"] if item["arm"] == arm)
        correct = sum(row["correct"] == "True" for row in group)
        constructed = sum(int(row["warm_items"]) for row in group)
        assert len(group) == summary["n"] == 210
        assert correct == summary["correct"]
        assert constructed - baseline == summary["extra_warm_items"]
        arm_data.append(dict(arm=arm, label=label, correct=correct, n=len(group),
                             cache_entries=constructed, cache_ratio=constructed / baseline))

    # Reconstruct every continuation point from the same hashed raw observations
    # used by the independent audit; no interpolation or new experiment is used.
    cohort = Path(report["cohort"])
    resident = np.zeros(17, dtype=np.int64)
    cache_construction = {arm: np.zeros(17, dtype=np.int64) for arm in ARMS}
    arm_counts = dict.fromkeys(ARMS, 0)
    sources = []
    family_resident = {}
    for evidence in report["evidence"]:
        path = Path(evidence["path"])
        arm = path.parent.name
        assert arm in ARMS
        folder = cohort / path.parent
        cut_path = folder / "at-cut.json"
        after_path = folder / "after-policy.json"
        continuation_path = folder / "continuation.json"
        assert digest(cut_path) == evidence["cut_sha256"]
        assert digest(after_path) == evidence["after_sha256"]
        assert digest(continuation_path) == evidence["continuation_sha256"]
        cut = json.loads(cut_path.read_text())
        after = json.loads(after_path.read_text())
        continuation = json.loads(continuation_path.read_text())
        assert [item["step"] for item in continuation] == list(range(1, 17))
        # Count work already done by terminated instances as well as live ones.
        prefix_work = sum(item["warm_items"] for item in cut) if arm == "clear_all" else 0
        archived_work = 0
        cache_curve = [prefix_work + sum(item["warm_items"] for item in after)]
        for item in continuation:
            cache_curve.append(prefix_work + archived_work +
                               sum(response["warm_items"] for response in item["responses"]))
            if item.get("natural_exit_after_response"):
                archived_work += item["responses"][0]["warm_items"]
        assert all(a <= b for a, b in zip(cache_curve, cache_curve[1:]))
        cache_construction[arm] += cache_curve
        arm_counts[arm] += 1
        sources.append(dict(path=str(path.parent), cut_sha256=evidence["cut_sha256"],
                            after_sha256=evidence["after_sha256"],
                            continuation_sha256=evidence["continuation_sha256"]))
        if arm != "retain_all":
            continue
        curve = [cut[0]["obsolete"]["resident"]]
        curve += [0 if item.get("natural_exit_after_response")
                  else item["responses"][0]["obsolete"]["resident"]
                  for item in continuation]
        assert all(a >= b for a, b in zip(curve, curve[1:]))
        resident += curve
        family = path.parts[1]
        family_resident.setdefault(family, np.zeros(17, dtype=np.int64))
        family_resident[family] += curve
    assert len(sources) == 630 and set(arm_counts.values()) == {210}
    assert np.all(cache_construction["clean"] == baseline)
    for arm in arm_data:
        assert int(cache_construction[arm["arm"]][-1]) == arm["cache_entries"]
    assert int(resident[0]) == report["obsolete_at_recovery"]
    released = resident[0] - resident
    assert int(released[-1]) == report["naturally_released_after_recovery"]
    assert int(resident[-1]) == report["resident_at_end"]
    return dict(
        arms=arm_data, continuation_calls=list(range(17)),
        denominator=int(resident[0]), resident_objects=resident.tolist(),
        naturally_released_objects=released.tolist(),
        naturally_released_pct=(100 * released / resident[0]).tolist(),
        cache_construction_objects={arm: values.tolist() for arm, values in cache_construction.items()},
        cache_construction_relative_to_clean={arm: (values / baseline).tolist()
                                              for arm, values in cache_construction.items()},
        family_resident_objects={key: value.tolist() for key, value in family_resident.items()},
        sources=dict(audit_sha256=digest(DATA / "audit.json"),
                     runs_sha256=digest(DATA / "runs.csv"), observations=sources),
        scope="Seven constructed lifecycle patterns; object-weighted release within 16 continuation calls.",
        release_unit="Application payload objects no longer retained; not physical RSS returned to the OS.",
    )


def frame(ax) -> None:
    ax.tick_params(direction="in", top=True, right=True, labelsize=7,
                   length=2.4, pad=2)
    ax.grid(axis="y", color="#dddddd", linewidth=0.45)
    ax.set_axisbelow(True)


def main() -> None:
    data = load_data()
    configure_style()
    fig, axes = plt.subplots(1, 2, figsize=(8.5 * CM, 5.7 * CM))
    ax = axes[0]
    x = data["continuation_calls"]
    # Stagger marks so the equal clean and retention curves remain identifiable.
    for arm, marks in (("clean", (1, 4)), ("clear_all", (0, 4)), ("retain_all", (3, 4))):
        ax.plot(x, data["cache_construction_relative_to_clean"][arm],
                **LINE_STYLES[arm], markevery=marks)
    ax.set_ylim(0, 2.2)
    ax.set_yticks([0, 1, 2], ["0", "1×", "2×"])
    ax.set_ylabel("Cache construction\n(relative to clean)", fontsize=8, labelpad=3)
    ax.set_title("(a) Cache construction", fontsize=8, pad=6)

    ax = axes[1]
    y = data["naturally_released_pct"]
    ax.plot(x, y, **LINE_STYLES["retain_all"], markevery=[0, 1, 4, 8, 12, 16])
    ax.set_ylim(-3, 103)
    ax.set_yticks([0, 50, 100])
    ax.set_ylabel("Naturally released\nobjects (%)", fontsize=8, labelpad=3)
    ax.set_title("(b) Natural release", fontsize=8, pad=6)
    for ax in axes:
        ax.set_xlim(-0.4, 16.5)
        ax.set_xticks([0, 8, 16])
        ax.set_xlabel("Continuation\ncalls", fontsize=8, labelpad=3)
        frame(ax)
    handles = [Line2D([], [], label=label, **LINE_STYLES[arm])
               for arm, label in zip(ARMS, LABELS)]
    fig.legend(handles=handles, loc="upper center", bbox_to_anchor=(0.52, 1.0),
               ncol=2, fontsize=8, columnspacing=0.8, handlelength=1.7,
               handletextpad=0.4, borderpad=0.1)
    fig.subplots_adjust(left=0.15, right=0.98, bottom=0.20, top=0.76, wspace=0.90)

    for suffix in ("pdf", "png"):
        output = ROOT / "motivation" / f"{STEM}.{suffix}"
        fig.savefig(output, dpi=300, bbox_inches="tight", pad_inches=0.025)
        shutil.copyfile(output, ROOT / "paper/img" / output.name)
    plt.close(fig)
    (DATA / "figure-data.json").write_text(json.dumps(data, indent=2) + "\n")
    print(json.dumps({"figure": STEM, "arms": data["arms"],
                      "release_denominator": data["denominator"],
                      "released_pct": data["naturally_released_pct"][-1]}, indent=2))


if __name__ == "__main__":
    main()
