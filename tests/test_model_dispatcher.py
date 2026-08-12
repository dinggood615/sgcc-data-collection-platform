import ast
from pathlib import Path


def test_dispatcher_script_is_valid_python():
    path = Path(__file__).parents[1] / "scripts" / "local-model-dispatcher.py"
    ast.parse(path.read_text(encoding="utf-8"))


def test_installer_enables_shared_dispatcher():
    installer = (Path(__file__).parents[1] / "scripts" / "install-local-model.sh").read_text(encoding="utf-8")
    assert "local-model-dispatcher.service" in installer
    assert "127.0.0.1" in (Path(__file__).parents[1] / "scripts" / "local-model-dispatcher.py").read_text(encoding="utf-8")
