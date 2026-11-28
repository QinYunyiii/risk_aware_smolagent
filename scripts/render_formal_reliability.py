#!/usr/bin/env python3
"""Render already-computed aggregate bins in an isolated local plotting environment."""
from pathlib import Path
import json
import sys


def deny_network(event, args):
    if event in ("socket.connect", "socket.getaddrinfo", "socket.sendto", "socket.bind"):
        raise RuntimeError("Plot rendering is offline-only")


sys.addaudithook(deny_network)


def main():
    import importlib.metadata as metadata
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    directory = Path(sys.argv[1]).resolve()
    evaluation = json.loads((directory / "evaluation.json").read_text())
    fig, axes = plt.subplots(1, 2, figsize=(9, 4), sharex=True, sharey=True)
    for ax, split in zip(axes, ("train_oof", "validation")):
        ax.plot([0, 1], [0, 1], linestyle="--", color="gray", label="Perfect calibration")
        for model, bins in evaluation["calibration"][split].items():
            nonempty = [b for b in bins if b["n"]]
            ax.plot([b["mean_predicted"] for b in nonempty], [b["observed_failure_rate"] for b in nonempty],
                    marker="o", label=model.replace("_", " "))
        ax.set(title=split.replace("_", " "), xlabel="Mean predicted failure probability", xlim=(0, 1), ylim=(0, 1))
        ax.grid(alpha=.2)
    axes[0].set_ylabel("Observed failure fraction")
    axes[1].legend(fontsize=7, loc="upper left")
    val = evaluation["data"]["validation"]
    fig.suptitle(f"Fixed 5-bin reliability diagram; Validation n={val['n']}, failures={val['failures']}")
    fig.tight_layout()
    fig.savefig(directory / "reliability_diagram.svg")
    fig.savefig(directory / "reliability_diagram.png", dpi=180)
    plt.close(fig)
    (directory / "plot_versions.json").write_text(json.dumps({name: metadata.version(name)
        for name in ("matplotlib", "numpy")}, indent=2) + "\n")


if __name__ == "__main__":
    main()
