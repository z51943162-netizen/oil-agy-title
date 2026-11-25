"""Build a Windows-native plugin package without installing or calling agy."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil


def prepare(output: Path, launcher: str = "py") -> Path:
    if launcher not in ("py", "python"):
        raise ValueError("launcher must be py or python")
    source = Path(__file__).resolve().parents[1] / "plugin"
    output = output.resolve()
    if output == source or source in output.parents or output in source.parents:
        raise ValueError("output must be outside the source plugin")
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite {output}")
    shutil.copytree(source, output, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "tests"))
    hooks_path = output / "hooks.json"
    hooks = json.loads(hooks_path.read_text(encoding="utf-8"))
    command = "py -3" if launcher == "py" else "python"
    hooks["oil-agy-title"]["Stop"][0]["command"] = command + ' "./scripts/oil_agy_title.py" hook'
    hooks_path.write_text(json.dumps(hooks, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--launcher", choices=("py", "python"), default="py")
    args = parser.parse_args()
    print(prepare(args.output, args.launcher))
