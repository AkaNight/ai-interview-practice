"""Build a source-only release from an explicit file allowlist."""
import argparse
import hashlib
import re
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
VERSION = "0.1.1"
ROOT_FILES = {".env.example", ".gitignore", "README.md", "RELEASE_NOTES.md",
              "USAGE_RELEASE_ACCEPTANCE.md", "pytest.ini", "requirements.txt", "requirements.lock.txt", "requirements-build.txt", "run.py", "run.bat"}
ROOT_FILES.update(path.name for path in ROOT.glob("*_ACCEPTANCE.md"))
PATTERNS = {"app": {".py", ".html", ".js", ".css"}, "tests": {".py"}, "scripts": {".py", ".cjs"}, "docs": {".md"}}
SECRET = re.compile(rb"(?:sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{30,})")


def release_files():
    files = [ROOT / name for name in sorted(ROOT_FILES)]
    for folder, extensions in PATTERNS.items():
        files.extend(path for path in (ROOT / folder).rglob("*")
                     if path.is_file() and path.suffix in extensions and "__pycache__" not in path.parts)
    files.append(ROOT / "examples" / "sample_resume.docx")
    for path in files:
        if path.is_symlink() or not path.resolve().is_relative_to(ROOT) or not path.is_file():
            raise ValueError("Release file is missing or outside the project allowlist")
        if SECRET.search(path.read_bytes()):
            raise ValueError(f"Credential-like text detected in {path.relative_to(ROOT)}; no archive produced")
    # Include only the known fictional resume fixture; never arbitrary uploads.
    with ZipFile(ROOT / "examples" / "sample_resume.docx") as sample:
        if "虚构" not in sample.read("word/document.xml").decode("utf-8"):
            raise ValueError("Example resume is not marked fictional")
    return sorted(files)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    files = release_files()  # Finish validation before creating the archive.
    args.output_dir.mkdir(parents=True, exist_ok=True)
    archive = args.output_dir / f"ai-interview-practice-v{VERSION}.zip"
    prefix = f"ai-interview-practice-v{VERSION}"
    with ZipFile(archive, "w", ZIP_DEFLATED) as zipped:
        for path in files:
            zipped.write(path, f"{prefix}/{path.relative_to(ROOT).as_posix()}")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    checksum = archive.with_suffix(".sha256")
    checksum.write_text(f"{digest}  {archive.name}\n", encoding="utf-8")
    print(f"Built {archive.name}: {len(files)} allowed files; SHA256 {digest}")


if __name__ == "__main__":
    main()
