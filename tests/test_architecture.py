import ast
import unittest
from pathlib import Path

PACKAGE = Path(__file__).parents[1] / "src" / "psychology_evidence_agent"


def imported_modules(directory: Path) -> set[str]:
    modules: set[str] = set()
    for path in directory.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                modules.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.add(node.module)
    return modules


class ArchitectureDependencyTests(unittest.TestCase):
    def test_domain_has_no_infrastructure_dependencies(self):
        modules = imported_modules(PACKAGE / "domain")
        forbidden = {"fastapi", "httpx", "pypdf", "docx", "subprocess"}
        self.assertFalse(any(module.split(".")[0] in forbidden for module in modules))

    def test_services_do_not_import_concrete_adapters_or_transport_libraries(self):
        modules = imported_modules(PACKAGE / "services")
        forbidden = {"httpx", "urllib", "subprocess", "pypdf"}
        self.assertFalse(any(module.split(".")[0] in forbidden for module in modules))
        self.assertFalse(any("adapters" in module for module in modules))

    def test_run_state_ports_and_domain_stay_infrastructure_neutral(self):
        domain_modules = imported_modules(PACKAGE / "domain")
        port_modules = imported_modules(PACKAGE / "ports")
        forbidden_domain = {"pathlib", "json", "os", "httpx", "subprocess", "fastapi"}
        self.assertFalse(any(module.split(".")[0] in forbidden_domain for module in domain_modules))
        self.assertFalse(any("adapters" in module for module in port_modules))

    def test_runtime_boundaries_keep_machine_pure_and_executor_adapter_free(self):
        machine_modules = imported_modules(PACKAGE / "runtime")
        self.assertFalse(any("services" in module for module in machine_modules))
        self.assertFalse(any("adapters" in module for module in machine_modules))
        forbidden_executor = {"httpx", "urllib", "subprocess", "fastapi"}
        self.assertFalse(
            any(module.split(".")[0] in forbidden_executor for module in machine_modules)
        )
