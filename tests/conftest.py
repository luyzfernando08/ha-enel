"""Bootstrap dos testes: carrega api.py/const.py sem importar o __init__.py
do pacote, que puxa o Home Assistant (não é uma dependência dos testes aqui).
"""
import importlib.util
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT_DIR = ROOT / "custom_components" / "enel_sp"


def _register_stub_package(name: str, path: Path) -> types.ModuleType:
    if name in sys.modules:
        return sys.modules[name]
    module = types.ModuleType(name)
    module.__path__ = [str(path)]
    sys.modules[name] = module
    return module


def _load(name: str, filename: str) -> types.ModuleType:
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, COMPONENT_DIR / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_register_stub_package("custom_components", ROOT / "custom_components")
_register_stub_package("custom_components.enel_sp", COMPONENT_DIR)
_load("custom_components.enel_sp.const", "const.py")
_load("custom_components.enel_sp.api", "api.py")
