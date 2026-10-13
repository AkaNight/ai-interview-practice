"""Build a Windows x64 single-file executable from validated public source."""
import hashlib
import platform
import subprocess
import sys
from zipfile import ZIP_DEFLATED, ZipFile

from build_release import ROOT, VERSION, release_files


def main():
    if sys.platform != "win32" or platform.architecture()[0] != "64bit":
        raise SystemExit("Build this release using 64-bit Python on Windows.")
    release_files()
    output = ROOT / "dist" / "windows"
    command = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--onefile", "--console", "--noupx",
               "--name", "AI-Interview-Practice", "--distpath", str(output),
               "--workpath", str(ROOT / "build" / "windows"), "--specpath", str(ROOT / "build"),
               "--add-data", f"{ROOT / 'app' / 'static'};app/static",
               "--collect-all", "ddgs", "--collect-all", "primp",
               "--collect-submodules", "sqlalchemy.dialects.sqlite",
               "--hidden-import", "uvicorn.logging", "--hidden-import", "uvicorn.loops.asyncio",
               "--hidden-import", "uvicorn.protocols.http.h11_impl", "--hidden-import", "uvicorn.lifespan.on"]
    for module in ("pytest", "tkinter", "numpy", "pandas", "scipy", "matplotlib", "IPython", "torch", "sympy"):
        command.extend(["--exclude-module", module])
    command.append(str(ROOT / "run.py"))
    subprocess.run(command, cwd=ROOT, check=True)
    executable = output / "AI-Interview-Practice.exe"
    archive = ROOT / "dist" / f"ai-interview-practice-v{VERSION}-windows-x64.zip"
    folder = f"ai-interview-practice-v{VERSION}-windows-x64"
    with ZipFile(archive, "w", ZIP_DEFLATED) as package:
        package.write(executable, f"{folder}/{executable.name}")
        for name in ("README.md", "docs/WINDOWS_BUILD.md", "docs/USAGE.md", "RELEASE_NOTES.md", "examples/sample_resume.docx"):
            package.write(ROOT / name, f"{folder}/{name}")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_suffix(".sha256").write_text(f"{digest}  {archive.name}\n", encoding="utf-8")
    print(f"Built {archive.name}: {archive.stat().st_size / 1024 / 1024:.1f} MB; SHA256 {digest}")


if __name__ == "__main__":
    main()
