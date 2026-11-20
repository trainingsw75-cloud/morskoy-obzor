
"""Download and verify the exact Piper voice used by the preview."""
from pathlib import Path
import shutil
import sys
import tempfile
from urllib.request import urlopen

from pipeline import MODEL_HASHES, digest

BASE = ("https://huggingface.co/rhasspy/piper-voices/resolve/"
        "2f358b6a560e3a913ebf509b2f3344f20b367f94/ru/ru_RU/dmitri/medium/")


def main(folder):
    folder.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=folder) as temp:
        for name, expected in MODEL_HASHES.items():
            target = folder / name
            if target.is_file() and digest(target) == expected:
                continue
            download = Path(temp) / name
            with urlopen(BASE + name, timeout=45) as response, download.open("wb") as output:
                shutil.copyfileobj(response, output)
            if digest(download) != expected:
                raise SystemExit(f"SHA-256 не совпал: {name}")
            download.replace(target)


if __name__ == "__main__":
    main(Path(sys.argv[1]))
