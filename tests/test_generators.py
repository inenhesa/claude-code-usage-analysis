import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GEN = ROOT / "benchmarks" / "generators"


def _load(name: str):
    path = GEN / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_ledger_is_deterministic_and_solve_flips_the_checks(tmp_path: Path):
    module = _load("gen_ledger_migration")
    left = tmp_path / "a"
    right = tmp_path / "b"
    module.generate(left, seed=1337, scale="S", solve=False)
    module.generate(right, seed=1337, scale="S", solve=False)
    assert (left / "MANIFEST.json").read_bytes() == (right / "MANIFEST.json").read_bytes()
    sys.path.insert(0, str(ROOT))
    from benchmarks.checkers import run_checkers

    unsolved = run_checkers(left, ["INV-01", "INV-02"])
    assert unsolved["INV-01"] > 0
    assert unsolved["INV-02"] > 0
    solved_dir = tmp_path / "solved"
    module.generate(solved_dir, seed=1337, scale="S", solve=True)
    solved = run_checkers(solved_dir, ["INV-01", "INV-02"])
    assert solved == {"INV-01": 0, "INV-02": 0}
    _pytest(left, ROOT / "benchmarks" / "tasks" / "ledger_migration" / "hidden", expect_fail=True)
    _pytest(solved_dir, ROOT / "benchmarks" / "tasks" / "ledger_migration" / "hidden", expect_fail=False)
    visible = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "tests/visible"],
        cwd=solved_dir,
        capture_output=True,
        text=True,
    )
    assert visible.returncode == 0


def test_noisy_and_steered_hidden_tests(tmp_path: Path):
    noisy = _load("gen_noisy_bughunt")
    steered = _load("gen_steered_feature")
    raw = tmp_path / "noisy"
    fixed = tmp_path / "noisy-ok"
    noisy.generate(raw, solve=False)
    noisy.generate(fixed, solve=True)
    hidden = ROOT / "benchmarks" / "tasks" / "noisy_bughunt" / "hidden"
    _pytest(raw, hidden, expect_fail=True)
    _pytest(fixed, hidden, expect_fail=False)
    visible = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "tests/visible"],
        cwd=fixed,
        capture_output=True,
        text=True,
    )
    assert visible.returncode == 0
    bad = tmp_path / "steer"
    good = tmp_path / "steer-ok"
    steered.generate(bad, solve=False)
    steered.generate(good, solve=True)
    hidden_s = ROOT / "benchmarks" / "tasks" / "steered_feature" / "hidden"
    _pytest(bad, hidden_s, expect_fail=True)
    _pytest(good, hidden_s, expect_fail=False)
    sys.path.insert(0, str(ROOT))
    from benchmarks.checkers import run_checkers

    assert run_checkers(bad, ["D"])["D1"] == 1
    assert run_checkers(good, ["D"])["D1"] == 0


def _pytest(workdir: Path, hidden: Path, *, expect_fail: bool) -> None:
    destination = workdir / "hidden"
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(hidden, destination)
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "hidden"],
        cwd=workdir,
        capture_output=True,
        text=True,
    )
    if expect_fail:
        assert completed.returncode != 0
    else:
        assert completed.returncode == 0, completed.stdout + completed.stderr
