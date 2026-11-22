"""Plot gas choices from the piecewise boundaries in regions.csv."""

import csv
import sys
from math import isclose
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch, Polygon

plt.rcParams.update({
    "font.family": "DejaVu Sans", "font.size": 7.4,
    "axes.labelsize": 7.4, "axes.titlesize": 8.1, "axes.spines.top": False,
    "axes.spines.right": False, "axes.linewidth": .55, "axes.edgecolor": ".23",
    "xtick.labelsize": 6.9, "ytick.labelsize": 6.9,
    "xtick.major.width": .52, "ytick.major.width": .52,
    "xtick.major.size": 2.4, "ytick.major.size": 2.4,
    "xtick.major.pad": 2.2, "ytick.major.pad": 2.2,
    "hatch.linewidth": .38, "pdf.fonttype": 42, "savefig.dpi": 360,
})


def boundary(rows, family):
    vertices = []
    for row in rows:
        if row["family"] != family or not row["intercept"]:
            continue
        intercept, slope = float(row["intercept"]), float(row["slope"])
        low, high = float(row["s_low"]), float(row["s_high"])
        start = (intercept + slope * low, low)
        if vertices:
            if not all(isclose(x, y, abs_tol=1e-8) for x, y in zip(vertices[-1], start)):
                raise ValueError(f"Disconnected boundary for {family}")
        else:
            vertices.append(start)
        vertices.append((intercept + slope * high, high))
    if not vertices or not isclose(2 * vertices[-1][0], vertices[-1][1], abs_tol=1e-8):
        raise ValueError(f"Boundary for {family} does not end on the feasible edge")
    return vertices


def plot(input_path, output_path):
    with input_path.open() as source:
        rows = list(csv.DictReader(source))
    mode = boundary(rows, "settlement")
    replay = boundary(rows, "HH")
    triangle = [(0, 0), (1, 0), (.5, 1)]
    fig, axes = plt.subplots(1, 2, figsize=(5.08, 2.48), sharex=True, sharey=True)
    fig.subplots_adjust(left=.106, right=.983, bottom=.263, top=.843, wspace=.17)
    face = ".91"
    ink = ".18"
    for ax, title in zip(axes, ("(a)  No named handovers", "(b)  Named switches allowed")):
        ax.set_title(title, loc="left", pad=5.8, fontweight="normal")
        ax.add_patch(Polygon(triangle, closed=True, facecolor=face, edgecolor="none"))
        ax.set(xlim=(0, 1), ylim=(0, 1.02), xticks=[0,.25,.5,.75,1],
               yticks=[0,.25,.5,.75,1], xticklabels=["0","25","50","75","100"],
               yticklabels=["0","25","50","75","100"])
        ax.plot([0,.5,1,0],[0,1,0,0], color=ink, lw=.65, clip_on=False, zorder=4)
    axes[0].set_ylabel("Same-wallet switch rate, $s$ (%)")
    axes[1].tick_params(axis="y", length=0)
    axes[1].spines["left"].set_visible(False)
    a,b = axes
    a.text(.5,.39,"HH",ha="center",va="center",size=10,weight="bold")
    a.text(.5,.265,"Replay both paths",ha="center",va="center",size=7)
    b.add_patch(Polygon([(0,0), *mode], closed=True, facecolor=".67", edgecolor="none"))
    strip = [*mode, *reversed(replay)]
    b.add_patch(Polygon(strip, closed=True, facecolor="white", edgecolor=".29",
                        lw=.55, hatch="//////"))
    b.text(.32,.23,"MH or MR",ha="center",va="center",weight="bold",size=8.7)
    b.text(.70,.23,"HH",ha="center",va="center",weight="bold",size=8.7)
    b.annotate(f"$s$ ≈ {100*replay[-1][1]:.2f}%",xy=replay[-1],xytext=(.026,.865),
               va="center", ha="left", size=7.2,
               arrowprops=dict(arrowstyle="-",color=".18",lw=.55,shrinkA=2,shrinkB=0))
    b.plot(0,0,marker="o",ms=3.2,markeredgewidth=.65,mec="black",mfc="white",
           clip_on=False,zorder=5)
    fig.text(.545,.124,"Private share of approved payments, $q$ (%)",ha="center",size=7.6)
    fig.legend(handles=[
        Patch(facecolor=".67",edgecolor=ink,linewidth=.5,label="Mode settlement"),
        Patch(facecolor="white",edgecolor=ink,linewidth=.5,hatch="//////",label="Unresolved"),
        Patch(facecolor=face,edgecolor=ink,linewidth=.5,label="Per-payment check")],
        loc="lower center",bbox_to_anchor=(.54,.008),ncol=3, frameon=False,
        handlelength=1.25, handleheight=.8, handletextpad=.42, columnspacing=1.4,
        borderaxespad=0,fontsize=7)
    fig.savefig(output_path, metadata={"CreationDate": None, "ModDate": None})
    plt.close(fig)


if __name__ == "__main__":
    plot(Path(sys.argv[1]), Path(sys.argv[2]))
