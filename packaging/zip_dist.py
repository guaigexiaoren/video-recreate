"""把便携版目录打成 zip（UTF-8 文件名，兼容中文）。"""
import os
import sys
import time
import zipfile
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 3:
        print("用法：zip_dist.py <目录> <zip>")
        return 2

    root = Path(sys.argv[1]).resolve()
    out = Path(sys.argv[2]).resolve()
    if not (root / "VideoRecreate.exe").is_file():
        print(f"目录不完整：{root}")
        return 1
    out.parent.mkdir(parents=True, exist_ok=True)
    out.unlink(missing_ok=True)

    start = time.time()
    count = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, allowZip64=True, compresslevel=6) as archive:
        for dirpath, _dirnames, filenames in os.walk(root):
            current = Path(dirpath)
            rel_dir = current.relative_to(root.parent)
            archive.write(current, rel_dir.as_posix() + "/")
            for name in filenames:
                archive.write(current / name, (rel_dir / name).as_posix())
                count += 1
    print(f"已打包 {count} 个文件，用时 {time.time() - start:.1f}s：{out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
