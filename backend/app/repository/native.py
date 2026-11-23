"""Static detectors for supported root application layouts."""

from __future__ import annotations

import ast
import re
import tomllib
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

# Template input is restricted to these path segments, ports and probe routes.
MODULE = r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*"
PROBES = ("/ready", "/health/ready", "/healthz", "/health", "/status", "/health/live", "/live")


@dataclass(frozen=True)
class NativeFinding:
    category: str
    value: str | int
    path: str
    line: int
    excerpt: str


@dataclass
class NativeScan:
    runtime: str
    framework: str
    findings: list[NativeFinding] = field(default_factory=list)
    required_variables: list[str] = field(default_factory=list)
    service_dependencies: list[str] = field(default_factory=list)

    def add(self, category: str, value: str | int, path: str, source: str, line: int = 1):
        self.findings.append(NativeFinding(category, value, path, line, source))


def _add_match(result, category, value, path, source, match):
    result.add(category, value, path, match[0], source.count("\n", 0, match.start()) + 1)


def _probe(paths):
    return next((probe for probe in PROBES if probe in paths), None)


def _python(read, paths):
    requirements = read("requirements.txt")
    if not all(
        re.search(rf"(?im)^\s*{name}(?=\[|\s|[<=>~!;@]|$)", requirements)
        for name in ("fastapi", "uvicorn")
    ):
        return None

    result = NativeScan("python", "fastapi")
    result.service_dependencies = sorted(
        set(
            re.findall(
                r"(?im)^\s*(psycopg(?:2(?:-binary)?)?|asyncpg|redis|celery|pymongo|pika)(?=\[|\s|[<=>~!;@]|$)",
                requirements,
            )
        )
    )
    result.add("runtime", "python", "requirements.txt", requirements)
    result.add(
        "workflow.install_command",
        "python -m pip install -r requirements.txt",
        "requirements.txt",
        requirements,
    )
    manifest = read("pyproject.toml")
    try:
        metadata = tomllib.loads(manifest)
    except tomllib.TOMLDecodeError:
        metadata = {}
    project = metadata.get("project", {})
    constraint = project.get("requires-python") if isinstance(project, dict) else None
    if isinstance(constraint, str) and re.fullmatch(r"[0-9., =<>!~*]{1,80}", constraint):
        result.add("runtime.version", constraint, "pyproject.toml", manifest)

    if re.search(r"(?im)^\s*pytest(?=\[|\s|[<=>~!;@]|$)", requirements) and any(
        re.search(r"(^|/)(test_[^/]+|[^/]+_test)\.py$", path) for path in paths
    ):
        result.add("workflow.test_command", "python -m pytest", "requirements.txt", requirements)

    entries = []
    for path in paths:
        module = path[:-3].replace("/", ".")
        if not path.endswith(".py") or not re.fullmatch(MODULE, module, re.ASCII):
            continue
        if set(path.split("/")) & {"test", "tests"} or Path(path).stem.startswith("test_"):
            continue
        source = read(path)
        if "FastAPI" not in source:
            continue
        try:
            tree = ast.parse(source)
        except (SyntaxError, RecursionError):
            continue
        if not any(
            isinstance(node, ast.ImportFrom)
            and node.module == "fastapi"
            and any(alias.name == "FastAPI" and alias.asname is None for alias in node.names)
            for node in tree.body
        ):
            continue

        def binding(statement, factory):
            return (
                isinstance(statement, ast.Assign)
                and len(statement.targets) == 1
                and isinstance(statement.targets[0], ast.Name)
                and isinstance(statement.value, ast.Call)
                and isinstance(statement.value.func, ast.Name)
                and statement.value.func.id == factory
            )

        for statement in tree.body:
            if binding(statement, "FastAPI"):
                entries.append((module, statement.targets[0].id, path, statement, tree, source))
            elif isinstance(statement, ast.FunctionDef):
                for assignment in statement.body:
                    if not binding(assignment, "FastAPI"):
                        continue
                    app_name = assignment.targets[0].id
                    if not any(
                        isinstance(node, ast.Return)
                        and isinstance(node.value, ast.Name)
                        and node.value.id == app_name
                        for node in statement.body
                    ):
                        continue
                    for exported in tree.body:
                        if binding(exported, statement.name):
                            entries.append(
                                (
                                    module,
                                    exported.targets[0].id,
                                    path,
                                    assignment,
                                    statement,
                                    source,
                                )
                            )
    if len(entries) != 1:
        return result

    module, export, path, binding_node, scope, source = entries[0]
    target = f"{module}:{export}"
    result.add("runtime.application_target", target, path, source, binding_node.lineno)
    # Use only a port from documentation that corroborates the statically found
    # entry point. The program and other arguments are constructed by the host.
    readme = read("README.md")
    launches = list(
        re.finditer(
            rf"`uvicorn[ \t]+{re.escape(target)}[ \t]+--host[ \t]+0\.0\.0\.0[ \t]+--port[ \t]+([0-9]{{1,5}})`",
            readme,
        )
    )
    ports = {int(match[1]) for match in launches if 1 <= int(match[1]) <= 65535}
    if len(ports) <= 1:
        port = next(iter(ports), 8000)
        command = f"uvicorn {target} --host 0.0.0.0 --port {port}"
        if ports:
            launch = next(match for match in launches if int(match[1]) == port)
            _add_match(result, "network.port", port, "README.md", readme, launch)
            _add_match(result, "runtime.command", command, "README.md", readme, launch)
        else:
            result.add("network.port", port, path, source, binding_node.lineno)
            result.add("runtime.command", command, path, source, binding_node.lineno)

    if {"celery", "redis"}.issubset(result.service_dependencies):
        redis_worker = _redis_worker(read, readme, paths, module)
        if redis_worker:
            worker, worker_path, worker_source = redis_worker
            result.add("workflow.worker_target", worker, worker_path, worker_source)
            result.required_variables = ["CELERY_BROKER_URL", "CELERY_RESULT_BACKEND", "QUEUE_NAMESPACE"]
            if re.search(r"""os\.environ(?:\.get\(\s*|\[\s*)['"]API_TOKEN['"]""", source):
                result.required_variables.append("API_TOKEN")

    route_nodes = {}
    for statement in scope.body:
        if not isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in statement.decorator_list:
            if (
                isinstance(decorator, ast.Call)
                and isinstance(decorator.func, ast.Attribute)
                and decorator.func.attr == "get"
                and isinstance(decorator.func.value, ast.Name)
                and decorator.func.value.id == binding_node.targets[0].id
                and decorator.args
                and isinstance(decorator.args[0], ast.Constant)
                and isinstance(decorator.args[0].value, str)
            ):
                route_nodes[decorator.args[0].value] = decorator.lineno
    if "/health/live" in route_nodes:
        result.add("health.liveness", "/health/live", path, source, route_nodes["/health/live"])
    if probe := _probe(route_nodes):
        result.add("health_check", probe, path, source, route_nodes[probe])
    return result


def _redis_worker(read, readme, paths, module):
    # Corroborate a documented worker command with a sibling Python module
    # and the Celery object consumed by the known CLI target.
    package, _, _ = module.rpartition(".")
    workers = set(re.findall(
        rf"`celery[ \t]+-A[ \t]+({MODULE})[.:]celery[ \t]+worker[ \t]+--loglevel=warning[ \t]+--concurrency=1`",
        readme, re.ASCII,
    ))
    if len(workers) != 1 or not package:
        return None
    worker = next(iter(workers))
    path = worker.replace(".", "/") + ".py"
    if worker.rpartition(".")[0] != package or path not in paths:
        return None
    body = read(path)
    try:
        tree = ast.parse(body)
    except (SyntaxError, RecursionError):
        return None
    has_celery = any(
        isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id == "celery"
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Name)
        and node.value.func.id == "Celery"
        for node in tree.body
    )
    if has_celery and all(name in body for name in ("CELERY_BROKER_URL", "CELERY_RESULT_BACKEND", "QUEUE_NAMESPACE")):
        return f"{worker}:celery", path, body
    return None


def _rails(read, paths):
    lock = read("Gemfile.lock")
    if not all(re.search(rf"(?m)^    {gem} \(", lock) for gem in ("rails", "puma")):
        return None
    puma = read("config/puma.rb")
    routes = read("config/routes.rb")
    if not (puma and "Rails.application.routes.draw" in routes):
        return None
    result = NativeScan("ruby", "rails")
    result.add("runtime", "ruby", "Gemfile.lock", lock)
    result.add("workflow.install_command", "bundle install", "Gemfile.lock", lock)
    result.add("runtime.command", "bundle exec puma -C config/puma.rb", "config/puma.rb", puma)
    rakefile = read("Rakefile")
    if (test_task := re.search(r"(?m)^\s*Rake::TestTask\.new\(\s*:unit\s*\)", rakefile)) and any(
        path.startswith("test/") and path.endswith("_test.rb") for path in paths
    ):
        _add_match(
            result,
            "workflow.test_command",
            "bundle exec rake unit",
            "Rakefile",
            rakefile,
            test_task,
        )
    if bundler := re.search(r"(?m)^BUNDLED WITH\n\s+(\d+\.\d+\.\d+)", lock):
        _add_match(result, "runtime.bundler", bundler[1], "Gemfile.lock", lock, bundler)
    if version := re.search(r"(?m)^RUBY VERSION\n\s+ruby (\d+\.\d+\.\d+)", lock):
        _add_match(result, "runtime.version", version[1], "Gemfile.lock", lock, version)
    if port := re.search(r"""ENV\.fetch\(\s*['"]PORT['"]\s*,\s*['"]?(\d{1,5})\b""", puma):
        if 1 <= int(port[1]) <= 65535:
            _add_match(result, "network.port", int(port[1]), "config/puma.rb", puma, port)
    matches = {
        match[1]: match for match in re.finditer(r"""(?m)^\s*get\s+['"]([^'"]+)['"]""", routes)
    }
    if probe := _probe(matches):
        _add_match(result, "health_check", probe, "config/routes.rb", routes, matches[probe])
    if "/health/live" in matches:
        _add_match(result, "health.liveness", "/health/live", "config/routes.rb", routes, matches["/health/live"])
    database = read("config/database.yml")
    if "ENV" in database and "DATABASE_URL" in database:
        result.required_variables.append("DATABASE_URL")
        result.service_dependencies.append("postgresql")
    production = read("config/environments/production.rb")
    if "SECRET_KEY_BASE" in production:
        result.required_variables.append("SECRET_KEY_BASE")

    # Record stronger evidence for the narrowly scoped managed profile. Preserve
    # the existing broad detector for assessments and operator-managed bindings.
    default = re.search(r"(?m)^default: *&default *$\n(?: +[^\n]*\n)*", database)
    adapter = re.search(r"(?m)^ +adapter: *postgresql *$", default[0]) if default else None
    url = re.search(r"""(?m)^ +url: *<%= ENV\.fetch\(['"]DATABASE_URL['"]\) %> *$""", default[0]) if default else None
    pg = re.search(r"(?m)^    pg \(", lock)
    production_database = re.findall(r"(?m)^production: *\n((?: +[^\n]*(?:\n|\Z))*)", database)
    if adapter and url and pg and [block.strip() for block in production_database] == ["<<: *default"]:
        first_line = database.count("\n", 0, default.start()) + 1
        result.add("rails.database.adapter", "postgresql", "config/database.yml", adapter[0],
                   first_line + default[0].count("\n", 0, adapter.start()))
        _add_match(result, "rails.database.driver", "pg", "Gemfile.lock", lock, pg)
        result.add("rails.database.url_reference", "DATABASE_URL", "config/database.yml", url[0],
                   first_line + default[0].count("\n", 0, url.start()))
    if key := re.search(r"""(?m)^ *config\.secret_key_base *= *ENV\.fetch\(['"]SECRET_KEY_BASE['"]\) *$""", production):
        _add_match(result, "rails.production.secret_reference", "SECRET_KEY_BASE",
                   "config/environments/production.rb", production, key)
    if bind := re.search(r"""(?m)^ *bind +["']tcp://0\.0\.0\.0:""", puma):
        _add_match(result, "rails.production.listen_host", "0.0.0.0", "config/puma.rb", puma, bind)
    if load_tasks := re.search(r"(?m)^ +Rails\.application\.load_tasks *$", rakefile):
        _add_match(result, "rails.migration.tasks", "Rails.application.load_tasks", "Rakefile", rakefile, load_tasks)
    if any(
        re.search(r"(?m)^\s*before_action\s+:authenticate\b", read(path))
        for path in paths if path.startswith("app/controllers/") and path.endswith(".rb")
    ):
        result.required_variables.append("API_TOKEN")
    migration = next(
        (path for path in paths if re.fullmatch(r"db/migrate/\d+_[a-z0-9_]+\.rb", path)), None
    )
    if migration and (source := read(migration)):
        result.add("workflow.migration_command", "bundle exec rails db:migrate", migration, source)
    return result


def _maven(read, paths):
    source = read("pom.xml")
    if "<!DOCTYPE" in source.upper() or "<!ENTITY" in source.upper():
        return None
    try:
        pom = ET.fromstring(source)
    except ET.ParseError:
        return None
    ns = {"m": "http://maven.apache.org/POM/4.0.0"} if pom.tag.startswith("{") else {}

    def xpath(path):
        return "/".join(f"m:{part}" for part in path.split("/")) if ns else path

    def value(path):
        return (pom.findtext(xpath(path), namespaces=ns) or "").strip()

    if (
        value("parent/groupId") != "org.springframework.boot"
        or value("parent/artifactId") != "spring-boot-starter-parent"
        or value("packaging") not in {"", "jar"}
        or pom.find(xpath("modules"), ns) is not None
    ):
        return None
    result = NativeScan("java", "spring-boot")
    for dependency in pom.findall(xpath("dependencies/dependency"), ns):
        artifact_id = dependency.findtext(xpath("artifactId"), namespaces=ns) or ""
        if dependency.findtext(xpath("scope"), namespaces=ns) != "test" and re.search(
            r"(?:jdbc|data-|flyway|liquibase|postgresql|redis|amqp|kafka|mongodb)", artifact_id
        ):
            result.service_dependencies.append(artifact_id)
    result.add("runtime", "java", "pom.xml", source)
    if java := value("properties/java.version"):
        if re.fullmatch(r"(?:17|21|25)", java):
            result.add("runtime.version", java, "pom.xml", source)
    if any(path.startswith("src/test/java/") and path.endswith(".java") for path in paths):
        result.add("workflow.test_command", "mvn -B test", "pom.xml", source)
    result.add("workflow.install_command", "mvn -B dependency:go-offline", "pom.xml", source)
    plugin = any(
        item.findtext(xpath("artifactId"), namespaces=ns) == "spring-boot-maven-plugin"
        for item in pom.findall(xpath("build/plugins/plugin"), ns)
    )
    artifact = value("build/finalName") or f"{value('artifactId')}-{value('version')}"
    if (
        plugin
        and value("artifactId")
        and value("version")
        and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,120}", artifact)
        and value("build/directory") in {"", "target"}
    ):
        result.add("workflow.build_command", "mvn -B package", "pom.xml", source)
        result.add("runtime.command", f"java -jar target/{artifact}.jar", "pom.xml", source)
    properties_path = "src/main/resources/application.properties"
    properties = read(properties_path)
    port = re.search(r"(?m)^server\.port\s*=\s*(?:\$\{PORT:(\d{1,5})\}|(\d{1,5}))\s*$", properties)
    if port and 1 <= int(port[1] or port[2]) <= 65535:
        _add_match(
            result, "network.port", int(port[1] or port[2]), properties_path, properties, port
        )
    routes = {}
    for path in paths:
        if path.startswith("src/main/java/") and path.endswith(".java"):
            controller = read(path)
            for variable in ("DATABASE_URL", "API_TOKEN"):
                if f'System.getenv("{variable}")' in controller and variable not in result.required_variables:
                    result.required_variables.append(variable)
            expected_migration = f"java -jar target/{artifact}.jar migrate"
            if (
                'args[0].equals("migrate")' in controller
                and "flyway-core" in result.service_dependencies
                and any(p.startswith("src/main/resources/db/migration/") for p in paths)
                and f"`{expected_migration}`" in read("README.md")
            ):
                result.add("workflow.migration_command", expected_migration, path, controller)
            if re.search(r"@(?:RequestMapping)\b", controller):
                continue  # Class prefixes require a separate route resolver.
            for match in re.finditer(r'@GetMapping\(\s*"([^"]+)"\s*\)', controller):
                routes[match[1]] = (path, controller, match)
    if "/health/live" in routes:
        _add_match(result, "health.liveness", "/health/live", *routes["/health/live"])
    if probe := _probe(routes):
        _add_match(result, "health_check", probe, *routes[probe])
    return result


def detect_native(root: Path, files: list[Path]) -> tuple[NativeScan | None, str | None]:
    paths = {path.relative_to(root).as_posix(): path for path in files}

    def read(name):
        path = paths.get(name)
        if path is None or path.is_symlink() or path.stat().st_size > 128 * 1024:
            return ""
        return path.read_text(encoding="utf-8", errors="replace")

    candidates = [
        detector
        for marker, detector in (
            ("requirements.txt", _python),
            ("Gemfile", _rails),
            ("pom.xml", _maven),
        )
        if marker in paths
    ]
    if len(candidates) > 1:
        return None, "Multiple root runtime manifests require application selection."
    if candidates:
        result = candidates[0](read, paths)
        if result is None:
            return None, "The root manifest does not match a supported application layout."
        return result, None
    return None, None
