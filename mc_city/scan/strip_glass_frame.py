"""Remove scanned glass marker frames from component .npy files.

The scanner's --mark command draws a glass box on the 12 outer edges.
This tool removes only glass blocks on those edges, then trims empty air.
It keeps glass windows on faces or inside the model.
"""
from __future__ import annotations

import argparse
import os
import shutil
from datetime import datetime

import numpy as np

from ..config import COMPONENT_ROOT, GLASS_FRAME_BACKUP_DIR
from .building_scan import trim_to_solid

_AIR = {"id": "minecraft:air"}
_DEFAULT_GLASS = {"minecraft:glass"}


def _block_id(cell) -> str:
    if isinstance(cell, dict):
        return str(cell.get("id", "minecraft:air"))
    return str(cell)


def _base_id(cell) -> str:
    return _block_id(cell).split("[", 1)[0]


def _glass_ids(include_stained: bool) -> set[str]:
    if not include_stained:
        return set(_DEFAULT_GLASS)
    return {
        "minecraft:glass",
        "minecraft:tinted_glass",
        "minecraft:white_stained_glass",
        "minecraft:orange_stained_glass",
        "minecraft:magenta_stained_glass",
        "minecraft:light_blue_stained_glass",
        "minecraft:yellow_stained_glass",
        "minecraft:lime_stained_glass",
        "minecraft:pink_stained_glass",
        "minecraft:gray_stained_glass",
        "minecraft:light_gray_stained_glass",
        "minecraft:cyan_stained_glass",
        "minecraft:purple_stained_glass",
        "minecraft:blue_stained_glass",
        "minecraft:brown_stained_glass",
        "minecraft:green_stained_glass",
        "minecraft:red_stained_glass",
        "minecraft:black_stained_glass",
    }


def _edge_glass_positions(volume: np.ndarray, glass_ids: set[str]) -> list[tuple[int, int, int]]:
    ny, nz, nx = volume.shape
    found: list[tuple[int, int, int]] = []
    for y in range(ny):
        for z in range(nz):
            for x in range(nx):
                boundary_axes = (
                    (x in (0, nx - 1))
                    + (y in (0, ny - 1))
                    + (z in (0, nz - 1))
                )
                if boundary_axes >= 2 and _base_id(volume[y, z, x]) in glass_ids:
                    found.append((y, z, x))
    return found


def strip_file(path: str, *, apply: bool, min_edge: int,
               include_stained: bool, backup_root: str) -> tuple[int, tuple[int, ...], tuple[int, ...]]:
    volume = np.load(path, allow_pickle=True)
    before_shape = tuple(volume.shape)
    positions = _edge_glass_positions(volume, _glass_ids(include_stained))
    if len(positions) < min_edge:
        return 0, before_shape, before_shape
    if not apply:
        return len(positions), before_shape, before_shape

    rel = os.path.relpath(path, COMPONENT_ROOT)
    backup_path = os.path.join(backup_root, rel)
    os.makedirs(os.path.dirname(backup_path), exist_ok=True)
    if not os.path.exists(backup_path):
        shutil.copy2(path, backup_path)

    for y, z, x in positions:
        volume[y, z, x] = dict(_AIR)
    volume = trim_to_solid(volume)
    np.save(path, volume, allow_pickle=True)
    return len(positions), before_shape, tuple(volume.shape)


def iter_npy_files(root: str):
    for dirpath, _dirnames, filenames in os.walk(root):
        for filename in sorted(filenames):
            if filename.endswith(".npy"):
                yield os.path.join(dirpath, filename)


def _parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="mc_city.scan.strip_glass_frame",
        description="Remove scanner glass marker frames from component npy files.",
    )
    parser.add_argument("--root", default=COMPONENT_ROOT,
                        help="Folder to scan. Defaults to components/.")
    parser.add_argument("--apply", action="store_true",
                        help="Write changes. Without this, only prints candidates.")
    parser.add_argument("--min-edge", type=int, default=16,
                        help="Minimum edge glass count before treating a file as framed.")
    parser.add_argument("--include-stained", action="store_true",
                        help="Also remove stained/tinted full glass blocks on outer edges.")
    return parser.parse_args(argv)


def main(argv=None) -> None:
    args = _parse_args(argv)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_root = os.path.join(GLASS_FRAME_BACKUP_DIR, stamp)
    changed = 0
    total_blocks = 0
    for path in iter_npy_files(args.root):
        try:
            count, before, after = strip_file(
                path,
                apply=args.apply,
                min_edge=max(1, int(args.min_edge)),
                include_stained=bool(args.include_stained),
                backup_root=backup_root,
            )
        except Exception as exc:
            print(f"skip {path}: {exc!r}")
            continue
        if count <= 0:
            continue
        changed += 1
        total_blocks += count
        rel = os.path.relpath(path, args.root)
        action = "cleaned" if args.apply else "candidate"
        print(f"{action}: {rel} edge_glass={count} shape={before}->{after}")
    mode = "applied" if args.apply else "dry-run"
    print(f"{mode}: files={changed}, edge_glass={total_blocks}")
    if args.apply and changed:
        print(f"backup: {backup_root}")


if __name__ == "__main__":
    main()
