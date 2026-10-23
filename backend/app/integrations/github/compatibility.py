"""Conservative Node source compatibility with an approved infrastructure baseline.

No repository code is executed. Unknown file types, dependency changes and
changes to deployment-sensitive expressions require a new assessment/review.
"""
import difflib
import hashlib
import re
from pathlib import PurePosixPath

import tree_sitter_javascript
from tree_sitter import Language, Parser

from app.integrations.github.api import GitHubPhase6Error

VERSION = "node-source-compatibility-v1"
HANDLER_VERSION = "node-handler-compatibility-v2"
POLICIES = {VERSION, HANDLER_VERSION}


def policy_permits(policy, evidence):
    if policy not in POLICIES:
        return False
    if evidence is None:  # Original separately reviewed bootstrap build.
        return True
    version = evidence.get("policy")
    return version == VERSION or (version == HANDLER_VERSION and policy == HANDLER_VERSION)
# Even unchanged requirements can be expressed dynamically. Such edits are not
# automatically approved. This is deliberately narrower than generic JS support.
SENSITIVE = re.compile(
    r"process|\b(?:PORT|HOST|port|host)\b|listen\s*\(|health|ready|live|"
    r"\bimport\b|\brequire\b|\bexport\b|\beval\b|\bFunction\b|"
    r"child_process|\bexec\b|\bspawn\b|\bfetch\b|\bconnect\b|"
    r"readFile|writeFile|mkdir|createServer|setInterval|setTimeout|"
    r"\benv\b|\bconfig\b|\blocals\b|\.\s*app\b|getPrototypeOf|setPrototypeOf|Reflect|Proxy",
    re.I,
)


def _safe_response_edits(before: bytes, after: bytes) -> bool:
    """Accept response-data changes while preserving executable JS structure.

    An AST check complements the deployment-sensitive diff check. It deliberately
    does not claim arbitrary JS equivalence. Literal constants used only in JSON
    responses can be added/changed (e.g. a version marker). Other logic needs review.
    """
    parser = Parser(Language(tree_sitter_javascript.language()))

    def skeleton(raw):
        root = parser.parse(raw).root_node
        if root.has_error:
            raise GitHubPhase6Error("deployment_requirements_need_review")
        nodes = []
        def visit(node):
            nodes.append(node)
            for child in node.named_children:
                visit(child)
        visit(root)
        responses = []
        for node in nodes:
            if node.type == "call_expression":
                function = node.child_by_field_name("function")
                if function and re.fullmatch(rb"[A-Za-z_$][\w$]*\.json", function.text):
                    arguments = node.child_by_field_name("arguments")
                    responses.append(arguments)
        health_handlers = []
        for node in nodes:
            if node.type == "call_expression":
                arguments = node.child_by_field_name("arguments")
                if arguments and arguments.named_children:
                    route = arguments.named_children[0]
                    if route.type == "string" and re.search(rb"health|ready|live", route.text, re.I):
                        health_handlers.append(node.text)
        # Preserve executable expressions, including getters/assignments that
        # JSON serialization can execute without an explicit function call.
        executable = {"call_expression", "new_expression", "function_expression", "arrow_function",
                      "method_definition", "assignment_expression", "augmented_assignment_expression",
                      "update_expression", "await_expression", "yield_expression"}
        calls = [n.text for n in nodes if n.type in executable
                 and any(r.start_byte <= n.start_byte < r.end_byte for r in responses)]
        replacements = [(r.start_byte, r.end_byte, b"(RESPONSE_DATA)") for r in responses]
        literal_names = set()
        for node in nodes:
            if node.type != "lexical_declaration" or not node.text.startswith(b"const ") or len(node.named_children) != 1:
                continue
            decl = node.named_children[0]
            name, value = decl.child_by_field_name("name"), decl.child_by_field_name("value")
            if not name or not value or value.type not in {"string", "number", "true", "false", "null"}:
                continue
            uses = [n for n in nodes if n.type in {"identifier", "shorthand_property_identifier"} and n.text == name.text and n.start_byte != name.start_byte]
            if uses and all(any(r.start_byte <= n.start_byte < r.end_byte for r in responses) for n in uses):
                literal_names.add(name.text)
                replacements.append((node.start_byte, node.end_byte, b""))
        output = raw
        for start, end, replacement in sorted(replacements, reverse=True):
            output = output[:start] + replacement + output[end:]
        references = sorted({n.text for n in nodes if n.type in {"identifier", "shorthand_property_identifier"}
                             and n.text not in literal_names
                             and any(r.start_byte <= n.start_byte < r.end_byte for r in responses)})
        return re.sub(rb"\s+", b" ", output).strip(), calls, health_handlers, references
    return skeleton(before) == skeleton(after)


def _safe_handler_edits(before: bytes, after: bytes) -> bool:
    """Allow behavior changes inside existing static, non-health Express routes.

    Preserve imports, startup, route method/path, handler parameters and all code
    outside handler bodies. New routes, helper/module edits and dynamic loading
    require reassessment. This classifies deployment contracts, not JS equivalence.
    """
    parser = Parser(Language(tree_sitter_javascript.language()))

    def names(node):
        if node is None:
            return set()
        if node.type in {"identifier", "shorthand_property_identifier_pattern"}:
            return {node.text}
        return set().union(*(names(child) for child in node.named_children))

    def local_logic(body, parameters):
        # Only local handler logic is covered by this opt-in. Module helpers and
        # imported aliases can conceal new environment/network requirements.
        # Track lexical blocks/functions instead of treating a nested shadowing
        # declaration as a global authorization for that identifier.
        builtins = {b"Math", b"JSON", b"Number", b"String", b"Boolean", b"Array", b"Object", b"Promise", b"undefined"}

        def inspect(node, scope):
            if node.type == "subscript_expression":
                return False  # Dynamic property access needs reassessment.
            if node.type == "statement_block":
                scope = set(scope)
                for statement in node.named_children:
                    if statement.type == "lexical_declaration":
                        for declaration in statement.named_children:
                            scope |= names(declaration.child_by_field_name("name"))
                    elif statement.type == "function_declaration":
                        scope |= names(statement.child_by_field_name("name"))
            if node.type in {"arrow_function", "function_expression", "function_declaration"}:
                scope = scope | names(node.child_by_field_name("parameters") or node.child_by_field_name("parameter")) | names(node.child_by_field_name("name"))
            if node.type in {"identifier", "shorthand_property_identifier"} and node.text not in scope | builtins:
                return False
            return all(inspect(child, scope) for child in node.named_children)

        return inspect(body, names(parameters))

    def skeleton(raw):
        root = parser.parse(raw).root_node
        if root.has_error:
            raise GitHubPhase6Error("deployment_requirements_need_review")
        replacements = []

        def visit(node):
            if node.type == "call_expression":
                function = node.child_by_field_name("function")
                args = node.child_by_field_name("arguments")
                if function and args and re.fullmatch(rb"[A-Za-z_$][\w$]*\.(?:get|post|put|patch|delete|head|options)", function.text):
                    children = args.named_children
                    if len(children) == 2 and children[0].type == "string" and children[1].type in {"arrow_function", "function_expression"}:
                        if not re.search(rb"health|ready|live", children[0].text, re.I):
                            body = children[1].child_by_field_name("body")
                            parameters = children[1].child_by_field_name("parameters") or children[1].child_by_field_name("parameter")
                            if body and body.type == "statement_block" and local_logic(body, parameters):
                                replacements.append((body.start_byte, body.end_byte))
                                return
            for child in node.named_children:
                visit(child)
        visit(root)
        value = raw
        for start, end in sorted(replacements, reverse=True):
            value = value[:start] + b"{APPROVED_HANDLER_BODY}" + value[end:]
        return value

    return skeleton(before) == skeleton(after)


def checked_source_compatibility(api, *, baseline: str, source: str, files: dict[str, str], policy: str = VERSION) -> dict:
    if policy not in POLICIES:
        raise GitHubPhase6Error("source_compatibility_policy_unknown")
    if not re.fullmatch(r"[0-9a-f]{40}", source):
        raise GitHubPhase6Error("source_revision_invalid")
    api.require_descendant(baseline, source)
    for path, expected in files.items():
        if api.file_at(path, source) != expected.encode():
            raise GitHubPhase6Error("approved_infrastructure_changed")
    changed = api.compare_paths(baseline, source)
    evidence = {}
    for path in sorted(changed):
        if path in files:
            continue
        name = PurePosixPath(path)
        # Documentation and tests do not alter the approved build/runtime contract.
        documentation = name.suffix == ".md" or name.parts[0] in {"test", "tests"} or name.name.endswith(".test.js")
        if not documentation and (name.suffix not in {".js", ".mjs", ".cjs"} or name.parts[0] != "src"):
            raise GitHubPhase6Error("deployment_requirements_need_review")
        before, after = api.file_at(path, baseline), api.file_at(path, source)
        if not documentation:
            if before is None or after is None or max(len(before), len(after)) > 128 * 1024:
                raise GitHubPhase6Error("deployment_requirements_need_review")
            try:
                old, new = before.decode(), after.decode()
            except UnicodeDecodeError as exc:
                raise GitHubPhase6Error("deployment_requirements_need_review") from exc
            # Changes anywhere in an existing health handler need review, even
            # if the route name itself was not edited (e.g. response.status(500)).
            health = r"\.(?:get|head)\(\s*['\"][^'\"]*(?:health|ready|live)[^'\"]*['\"][\s\S]*?\n\s*\}\);"
            if re.findall(health, old, re.I) != re.findall(health, new, re.I):
                raise GitHubPhase6Error("deployment_requirements_need_review")
            delta = [line[1:] for line in difflib.ndiff(old.splitlines(), new.splitlines()) if line[:2] in {"+ ", "- "}]
            unsafe = any(SENSITIVE.search(line) or re.search(r"\b(?:globalThis|global|constructor|__proto__)\b", line) for line in delta)
            compatible = _safe_response_edits(before, after) or (policy == HANDLER_VERSION and _safe_handler_edits(before, after))
            if unsafe or not compatible:
                raise GitHubPhase6Error("deployment_requirements_need_review")
        evidence[path] = hashlib.sha256(after or b"").hexdigest()
    return {"policy": policy, "baseline_sha": baseline, "source_sha": source,
            "status": "compatible", "changed_file_hashes": evidence}
