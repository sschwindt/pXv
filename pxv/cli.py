"""Command line interface.

Everything the GUI can do is reachable here, so runs stay reproducible and
testable. A project YAML file is the system of record.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np


def _cmd_info(args) -> int:
    from pxv.io import metadata as meta_io
    from pxv.io import video as video_io

    info = video_io.probe(args.video)
    print(info.describe())
    tel = meta_io.read_telemetry(args.video)
    print(tel.describe())
    if not len(tel):
        print(
            "\nNo DJI SRT sidecar next to the video. A Mavic 3 only writes it when\n"
            "Video Captions are enabled in DJI Fly; without it, flight height and\n"
            "heading have to be entered manually in the project file."
        )
    return 0


def _cmd_init(args) -> int:
    from pxv.io import video as video_io
    from pxv.project import Project, save_project

    info = video_io.probe(args.video)
    p = Project(name=Path(args.video).stem)
    p.video.path = str(Path(args.video).resolve())
    p.video.fps = info.fps
    p.video.stop = min(info.n_frames, args.frames) if args.frames > 0 else -1
    p.export.outdir = str(Path(args.output).parent / (Path(args.video).stem + "-out"))

    if args.lab:
        # a flume fills the frame, so there is nothing static to register against
        p.stabilize.enabled = False
        p.stabilize.model = "none"
        p.preprocess.background = "static"
        p.ptv.enabled = True
        p.piv.enabled = True
        p.grid.cell_px = 24
    save_project(p, args.output)
    print(f"wrote {args.output}")
    print(info.describe())
    print("\nNext: set geometry (mode + scale) and run 'pxv run " + args.output + "'")
    return 0


def _cmd_advise(args) -> int:
    from pxv.calibrate.objective import quarter_rule_window
    from pxv.geometry.world import scale_from_height

    gsd = args.gsd
    if gsd <= 0:
        if args.height > 0 and args.hfov > 0 and args.width > 0:
            gsd = scale_from_height(args.height, args.hfov, args.width)
        else:
            print("supply --gsd, or --height --hfov --width", file=sys.stderr)
            return 2
    print(f"ground sampling distance: {gsd * 1000:.3f} mm/px")
    for step in (1, 2, 3, 5):
        dt = step / args.fps
        disp = args.speed * dt / gsd
        w = quarter_rule_window(args.speed, dt, gsd)
        flag = "  <- good" if 4 <= disp <= 24 else ""
        print(f"  step {step}: dt {dt * 1000:6.1f} ms, displacement {disp:6.2f} px, "
              f"suggested window {w:3d} px{flag}")
    print("\nAim for a displacement of a few pixels up to about a quarter of the window.")
    return 0


def _run_and_export(project, verbose: bool):
    from pxv.export import datasets, streamlines
    from pxv.io import video as video_io
    from pxv.preprocess.enhance import crop
    from pxv.pipeline import load_frames, run

    result = run(project, verbose=verbose)
    outdir = Path(project.export.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    written = []

    frames, _, _ = load_frames(project, verbose=False)
    mid = project.video.start + (len(frames) // 2) * project.video.step
    try:
        backdrop = video_io.read_frame(project.video.path, mid)
        backdrop = crop(backdrop, project.video.roi)
    except Exception:
        backdrop = frames[len(frames) // 2]

    ecfg = project.export
    if ecfg.streamlines:
        p = streamlines.plot_streamlines(
            result, outdir / f"{project.name}-streamlines.jpg", backdrop=backdrop,
            cmap=ecfg.cmap, backdrop_alpha=ecfg.backdrop_alpha, dpi=ecfg.dpi,
            title=f"{project.name}  |  surface velocity",
        )
        written.append(p)
        written.append(streamlines.plot_quicklook(
            result, outdir / f"{project.name}-quicklook.png", backdrop=backdrop, cmap=ecfg.cmap))
    if ecfg.geotiff:
        if (p := datasets.write_geotiff(result, outdir / f"{project.name}-velocity.tif")):
            written.append(p)
    if ecfg.gpkg:
        if (p := datasets.write_gpkg(result, outdir / f"{project.name}-velocity.gpkg")):
            written.append(p)
    if ecfg.ugrid:
        if (p := datasets.write_ugrid(result, outdir / f"{project.name}-mesh.nc")):
            written.append(p)

    check = None
    if ecfg.discharge_m3s > 0:
        check = datasets.discharge_check(
            result, depth_m=ecfg.check_depth_m, alpha=ecfg.alpha,
            known_q_m3s=ecfg.discharge_m3s)

    report = {
        "project": project.name,
        "summary": result.summary(),
        "log": result.log,
        "timings_s": {k: round(v, 2) for k, v in result.timings.items()},
        "discharge_check": check,
        "outputs": [str(p) for p in written],
    }
    (outdir / f"{project.name}-report.json").write_text(json.dumps(report, indent=2, default=str))
    return result, written, check


def _cmd_run(args) -> int:
    from pxv.project import load_project

    project = load_project(args.project)
    if args.frames > 0:
        project.video.stop = project.video.start + args.frames * project.video.step
    result, written, check = _run_and_export(project, verbose=not args.quiet)

    print("\n" + "=" * 72)
    print(result.summary())
    if check and check.get("ok"):
        print("-" * 72)
        print("discharge check:")
        for k, v in check.items():
            if k in ("ok", "row"):
                continue
            print(f"  {k}: {v:.5g}" if isinstance(v, float) else f"  {k}: {v}")
    print("-" * 72)
    for p in written:
        print(f"wrote {p}")
    return 0


def _cmd_calibrate(args) -> int:
    from pxv.calibrate.optimize import calibrate
    from pxv.io.groundtruth import read_groundtruth
    from pxv.project import load_project, save_project

    project = load_project(args.project)
    gt = read_groundtruth(project.groundtruth)
    print(gt.describe())
    res = calibrate(project, gt, verbose=not args.quiet)
    print("\n" + "=" * 72)
    for line in res.log:
        print(line)
    print("-" * 72)
    print(res.describe())
    if res.best is not None and args.write:
        project.geometry.scale_correction *= res.scale
        for key, value in res.best.params.items():
            project.set(key, value)
        save_project(project, args.project)
        print(f"\nwrote calibrated parameters back to {args.project}")
    return 0


def _cmd_gui(args) -> int:
    from pxv.gui.app import main as gui_main

    return gui_main(args.project)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="pxv", description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("info", help="probe a video and its DJI telemetry")
    s.add_argument("video")
    s.set_defaults(func=_cmd_info)

    s = sub.add_parser("init", help="write a starter project file for a video")
    s.add_argument("video")
    s.add_argument("-o", "--output", default="project.yaml")
    s.add_argument("--frames", type=int, default=0, help="limit to the first N frames")
    s.add_argument("--lab", action="store_true",
                   help="flume preset: stabilisation off, PIV and PTV both on")
    s.set_defaults(func=_cmd_init)

    s = sub.add_parser("advise", help="suggest a frame step and window from the quarter rule")
    s.add_argument("--speed", type=float, required=True, help="expected surface speed, m/s")
    s.add_argument("--fps", type=float, required=True)
    s.add_argument("--gsd", type=float, default=0.0, help="metres per pixel")
    s.add_argument("--height", type=float, default=0.0, help="flight height above water, m")
    s.add_argument("--hfov", type=float, default=0.0, help="horizontal field of view, degrees")
    s.add_argument("--width", type=int, default=0, help="image width, px")
    s.set_defaults(func=_cmd_advise)

    s = sub.add_parser("run", help="run a project and write the exports")
    s.add_argument("project")
    s.add_argument("--frames", type=int, default=0, help="limit to N processed frames")
    s.add_argument("-q", "--quiet", action="store_true")
    s.set_defaults(func=_cmd_run)

    s = sub.add_parser("calibrate", help="fit the scale, then search Tier 1 against ground truth")
    s.add_argument("project")
    s.add_argument("--write", action="store_true", help="write the best parameters back")
    s.add_argument("-q", "--quiet", action="store_true")
    s.set_defaults(func=_cmd_calibrate)

    s = sub.add_parser("gui", help="open the graphical interface")
    s.add_argument("project", nargs="?", default=None)
    s.set_defaults(func=_cmd_gui)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
