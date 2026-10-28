"""Exercise first-run setup without network downloads or system installations."""

import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest


@unittest.skipUnless(sys.platform == "darwin", "Finder launcher is macOS-only")
class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.checkout = self.root / "checkout with spaces"
        self.checkout.mkdir()
        source = Path(__file__).resolve().parents[1]
        shutil.copy2(source / "run.command", self.checkout / "run.command")
        shutil.copy2(source / "pyproject.toml", self.checkout / "pyproject.toml")
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.log = self.root / "setup.log"
        self.env = dict(os.environ)
        # Do not inherit an activated env or a user's app/uv configuration.
        for key in list(self.env):
            if key.startswith(("UV_", "PROMPT_ENHANCER_", "PYTHON")) or key == "VIRTUAL_ENV":
                del self.env[key]
        self.env.update(PATH=f"{self.bin}:/usr/bin:/bin", SETUP_LOG=str(self.log))
        self.write_executable(self.bin / "ollama", "#!/bin/sh\nexit 0\n")

        # Model uv's external effects using a real, isolated Python venv. The
        # installed stub app reports which environment and arguments were used.
        uv_script = self.root / "fake_uv.py"
        uv_script.write_text('''
import os, pathlib, subprocess, sys
with open(os.environ["SETUP_LOG"], "a") as log:
    log.write(sys.argv[1] + "\\n")
if sys.argv[1] == "venv":
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", sys.argv[-1]], check=True)
else:
    if os.environ.get("FAIL_INSTALL"):
        sys.exit(9)
    python = sys.argv[sys.argv.index("--python") + 1]
    purelib = subprocess.check_output([python, "-c", "import sysconfig; print(sysconfig.get_path('purelib'))"], text=True).strip()
    package = pathlib.Path(purelib) / "prompt_enhancer"
    package.mkdir(exist_ok=True)
    (package / "__init__.py").touch()
    (package / "cli.py").touch()
    (package / "__main__.py").write_text("import json, os, sys; print(json.dumps(dict(prefix=sys.prefix, args=sys.argv[1:], ollama=os.environ.get('PROMPT_ENHANCER_OLLAMA_BIN'))))")
''')
        uv = self.root / "uv"
        self.write_executable(
            uv, f"#!/bin/sh\nexec {shlex.quote(sys.executable)} {shlex.quote(str(uv_script))} \"$@\"\n"
        )
        self.env["FIXTURE_UV"] = str(uv)
        self.write_executable(self.bin / "curl", '''#!/bin/sh
case "$*" in
    *Ollama-darwin.zip*)
        printf 'download-ollama\n' >> "$SETUP_LOG"
        while [ "$1" != -o ]; do shift; done
        shift
        touch "$1"
        exit 0
        ;;
esac
printf 'download\n' >> "$SETUP_LOG"
if [ -n "${FAIL_DOWNLOAD:-}" ]; then exit 22; fi
while [ "$1" != -o ]; do shift; done
shift
cat > "$1" <<'INSTALLER'
mkdir -p "$UV_UNMANAGED_INSTALL"
cp "$FIXTURE_UV" "$UV_UNMANAGED_INSTALL/uv"
INSTALLER
''')
        self.write_executable(self.bin / "ditto", '''#!/bin/sh
mkdir -p "$4/Ollama.app/Contents/Resources"
printf '#!/bin/sh\nexit 0\n' > "$4/Ollama.app/Contents/Resources/ollama"
chmod +x "$4/Ollama.app/Contents/Resources/ollama"
''')

    @staticmethod
    def write_executable(path, content):
        path.write_text(content)
        path.chmod(0o755)

    def launch(self, *args, hide_system_ollama=False, **env):
        # Finder need not start in the checkout directory.
        command = ["/bin/bash", str(self.checkout / "run.command"), *args]
        if hide_system_ollama:
            # Hide system Ollama discovery while running the real launcher.
            # This keeps the test independent of installations on the host Mac.
            wrapper = '''
command() {
    if [[ "$*" == "-v ollama" ]]; then return 1; fi
    builtin command "$@"
}
export -f command
exec /bin/bash "$@"
'''
            command = ["/bin/bash", "-c", wrapper, "launcher", *command[1:]]
        return subprocess.run(
            command,
            cwd=self.root,
            env={**self.env, **env},
            text=True,
            capture_output=True,
            timeout=30,
        )

    def assert_launched(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout.splitlines()[-1])

    def test_fresh_checkout_then_offline_relaunch(self):
        app = self.assert_launched(self.launch("--model", "model with spaces"))
        self.assertEqual(Path(app["prefix"]), self.checkout / ".venv")
        self.assertEqual(app["args"], ["--model", "model with spaces"])
        self.assertEqual(app["ollama"], str(self.bin / "ollama"))
        self.assertEqual(self.log.read_text().splitlines(), ["download", "venv", "pip"])
        self.assert_launched(self.launch(FAIL_DOWNLOAD="1", FAIL_INSTALL="1"))
        self.assertEqual(self.log.read_text().splitlines(), ["download", "venv", "pip"])

    def test_dependency_change_reinstalls_without_recreating_environment(self):
        self.assert_launched(self.launch())
        with (self.checkout / "pyproject.toml").open("a") as config:
            config.write("\n# changed dependency declarations\n")
        self.assert_launched(self.launch())
        self.assertEqual(self.log.read_text().splitlines(), ["download", "venv", "pip", "pip"])

    def test_incomplete_install_can_retry(self):
        failed = self.launch(FAIL_INSTALL="1")
        self.assertEqual(failed.returncode, 9)
        self.assertIn("double-click run.command to retry", failed.stderr)
        self.assertNotIn("Starting Prompt Enhancer", failed.stdout)
        self.assertFalse((self.checkout / ".venv/.launcher-requirements").exists())
        self.assert_launched(self.launch())
        self.assertEqual(self.log.read_text().splitlines(), ["download", "venv", "pip", "pip"])

    def test_unusable_environment_is_preserved_and_replaced(self):
        broken = self.checkout / ".venv"
        broken.mkdir()
        (broken / "keep.txt").write_text("previous environment")
        self.assert_launched(self.launch())
        backups = list(self.checkout.glob(".venv-backup.*/environment/keep.txt"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(), "previous environment")

    def test_download_failure_stops_setup(self):
        failed = self.launch(FAIL_DOWNLOAD="1")
        self.assertEqual(failed.returncode, 22)
        self.assertIn("double-click run.command to retry", failed.stderr)
        self.assertFalse((self.checkout / ".venv").exists())
        self.assertEqual(self.log.read_text().splitlines(), ["download"])

    def test_missing_ollama_downloaded_and_reused(self):
        # The production launcher also checks /Applications and ~/Applications.
        # Skip if either exists; those installations should take precedence.
        if any((path / "Contents/Resources/ollama").is_file() for path in (
            Path("/Applications/Ollama.app"), Path.home() / "Applications/Ollama.app"
        )):
            self.skipTest("A system Ollama app takes precedence over downloading")
        app = self.assert_launched(self.launch(hide_system_ollama=True))
        self.assertEqual(app["ollama"], str(
            self.checkout / ".bootstrap/ollama/Ollama.app/Contents/Resources/ollama"
        ))
        self.assert_launched(self.launch(hide_system_ollama=True))
        self.assertEqual(self.log.read_text().splitlines(), ["download", "venv", "pip", "download-ollama"])


if __name__ == "__main__":
    unittest.main()
