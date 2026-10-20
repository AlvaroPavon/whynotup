"""Load a deliberately bounded subset of Compose without invoking Docker."""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit

import yaml

from .models import ComposeProject, Evidence, Finding, SourceLocation, Suggestion


class ComposeError(ValueError):
    """An input error safe to show without echoing configuration contents."""


class _UniqueLoader(yaml.SafeLoader):
    def construct_mapping(self, node: yaml.MappingNode, deep: bool = False) -> dict:
        seen = set()
        for key_node, _ in node.value:
            if key_node.tag == "tag:yaml.org,2002:merge":
                continue
            key = self.construct_object(key_node, deep=deep)
            try:
                if key in seen:
                    raise ComposeError(f"Duplicate YAML key at line {key_node.start_mark.line + 1}.")
                seen.add(key)
            except TypeError:
                raise ComposeError("YAML mapping keys must be scalar values.") from None
        self.flatten_mapping(node)
        return super().construct_mapping(node, deep=deep)


# Compose follows YAML 1.2 boolean spelling. PyYAML's default YAML 1.1 resolver
# would turn ON/OFF/YES/NO environment names and values into booleans (and even
# collapse distinct mapping keys). Keep this resolver change local to our loader.
_UniqueLoader.yaml_implicit_resolvers = {
    first: [(tag, pattern) for tag, pattern in resolvers if tag != "tag:yaml.org,2002:bool"]
    for first, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
_UniqueLoader.add_implicit_resolver(
    "tag:yaml.org,2002:bool", re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"),
    list("tTfF"),
)


_SENSITIVE = re.compile(r"password|passwd|secret|token|api.?key|private.?key|credential|authorization", re.I)
_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_MAX_FILE = 2 * 1024 * 1024


def _read(path: Path) -> str:
    try:
        if path.stat().st_size > _MAX_FILE:
            raise ComposeError(f"Input file exceeds the 2 MiB limit: {path.name}.")
        return path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError):
        raise ComposeError(f"Cannot read UTF-8 input file: {path.name}.") from None


def _relative(path: Path, base: Path) -> str:
    try:
        return str(path.relative_to(base))
    except ValueError:
        return path.name


def _locations(node: yaml.Node, file: str) -> dict[tuple, SourceLocation]:
    result = {}
    count = 0

    def visit(item: yaml.Node, path: tuple, ancestors: set[int]) -> None:
        nonlocal count
        count += 1
        if count > 50000 or len(path) > 80:
            raise ComposeError("YAML structure is too complex for this diagnostic scope.")
        if id(item) in ancestors:
            raise ComposeError("Recursive YAML aliases are not supported.")
        result[path] = SourceLocation(file, item.start_mark.line + 1)
        parents = ancestors | {id(item)}
        if isinstance(item, yaml.MappingNode):
            for key, value in item.value:
                if not isinstance(key, yaml.ScalarNode):
                    raise ComposeError("YAML mapping keys must be scalar values.")
                visit(value, path + (key.value,), parents)
                result[path + (key.value,)] = SourceLocation(file, key.start_mark.line + 1)
        elif isinstance(item, yaml.SequenceNode):
            for index, value in enumerate(item.value):
                visit(value, path + (index,), parents)

    visit(node, (), set())
    return result


def _interpolate(text: str, env: Mapping[str, str], missing, depth: int = 0) -> str:
    if depth > 20:
        raise ComposeError("Environment interpolation exceeds the nesting limit.")
    pieces = []
    index = 0
    while index < len(text):
        if text[index] != "$":
            pieces.append(text[index])
            index += 1
            continue
        if text[index:index + 2] == "$$":
            pieces.append("$")
            index += 2
            continue
        if text[index:index + 2] == "${":
            end, nested = index + 2, 1
            while end < len(text) and nested:
                if text[end:end + 2] == "${":
                    nested += 1
                    end += 2
                elif text[end] == "}":
                    nested -= 1
                    if nested:
                        end += 1
                else:
                    end += 1
            if nested:
                raise ComposeError("Unclosed environment interpolation expression.")
            expression = text[index + 2:end]
            match = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_]*)(?:(:-|:\?|:\+|-|\?|\+)(.*))?", expression, re.S)
            if not match:
                raise ComposeError("Unsupported environment interpolation expression.")
            key, operator, alternate = match.groups()
            exists, value = key in env, env.get(key, "")
            nonempty = exists and value != ""
            if operator in (":-", "-"):
                use_default = not (nonempty if operator == ":-" else exists)
                value = _interpolate(alternate or "", env, missing, depth + 1) if use_default else value
            elif operator in (":?", "?"):
                if not (nonempty if operator == ":?" else exists):
                    missing(key, True)
                    value = ""
            elif operator in (":+", "+"):
                value = _interpolate(alternate or "", env, missing, depth + 1) if (nonempty if operator == ":+" else exists) else ""
            elif not exists:
                missing(key, False)
            pieces.append(str(value))
            index = end + 1
            continue
        match = re.match(r"\$([A-Za-z_][A-Za-z0-9_]*)", text[index:])
        if match:
            key = match.group(1)
            if key not in env:
                missing(key, False)
            pieces.append(env.get(key, ""))
            index += len(match.group(0))
        else:
            pieces.append("$")
            index += 1
    return "".join(pieces)


def _dotenv(path: Path, env: Mapping[str, str], missing, *, raw: bool = False,
            inherited: Mapping[str, str] | None = None) -> tuple[dict[str, str | None], dict[str, int]]:
    lines = _read(path).splitlines()
    values, positions = {}, {}
    index = 0
    while index < len(lines):
        number = index + 1
        # Whitespace inside a multiline quote, or after '=' in raw format,
        # is part of the value. Only discard indentation before the key here.
        line = lines[index].lstrip()
        index += 1
        if not line or line.startswith("#"):
            continue
        if not raw and re.match(r"export\s+", line):
            line = re.sub(r"^export\s+", "", line, count=1)
        key, separator, value = line.partition("=")
        key = key if raw else key.strip()
        if not _KEY.fullmatch(key):
            raise ComposeError(f"Invalid dotenv key in {path.name} at line {number}.")
        positions[key] = number
        previous = {**(inherited or {}), **{k: v for k, v in values.items() if v is not None}}
        lookup = {**previous, **env}
        if not separator:
            # Compose resolves a bare env_file key from its lookup environment,
            # including earlier files. An unresolved bare key remains unset.
            values[key] = lookup.get(key)
            continue
        if not raw:
            value = value.lstrip()
        single = value.startswith("'") and not raw
        if not raw and value.startswith(("'", '"')):
            quote = value[0]
            content = value[1:]
            end = None
            while end is None:
                for pos, char in enumerate(content):
                    preceding = len(content[:pos]) - len(content[:pos].rstrip("\\"))
                    if char == quote and preceding % 2 == 0:
                        end = pos
                        break
                if end is None:
                    if index >= len(lines):
                        raise ComposeError(f"Unclosed dotenv quote in {path.name} at line {number}.")
                    content += "\n" + lines[index]
                    index += 1
            remainder = content[end + 1:].strip()
            if remainder and not remainder.startswith("#"):
                raise ComposeError(f"Unexpected text after dotenv value in {path.name} at line {number}.")
            value = content[:end]
            if single:
                value = value.replace("\\'", "'")
            else:
                # Protect an escaped dollar from the interpolation pass. Other
                # unknown backslash sequences stay literal, as Compose does.
                escapes = {"a": "\a", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t", "v": "\v", "\\": "\\", '"': '"', "$": "$$"}
                value = re.sub(r"\\([abfnrtv\\\"$])", lambda m: escapes[m.group(1)], value)
        elif not raw:
            value = re.split(r"\s+#", value, maxsplit=1)[0].rstrip()
        if not single and not raw:
            value = _interpolate(value, lookup, lambda name, required: missing(name, required, number))
        values[key] = value
    return values, positions


def _collect_secrets(values: Mapping[str, str | None]) -> set[str]:
    secrets = set()
    for key, value in values.items():
        if not value:
            continue
        if _SENSITIVE.search(key):
            secrets.add(value)
        try:
            password = urlsplit(value).password
            if password:
                secrets.add(password)
        except ValueError:
            pass
    return secrets


def load_project(path: Path, *, file: Path | None = None, env_file: Path | None = None,
                 profiles: set[str] | None = None, environ: dict[str, str] | None = None) -> ComposeProject:
    path = Path(path).resolve()
    explicit = file is not None or path.is_file()
    base = path.parent if path.is_file() else path
    if not base.is_dir():
        raise ComposeError("The project directory does not exist.")
    if file is not None:
        compose_file = Path(file)
        compose_file = compose_file if compose_file.is_absolute() else base / compose_file
    elif path.is_file():
        compose_file = path
    else:
        candidates = [base / name for name in ("compose.yaml", "compose.yml", "docker-compose.yaml", "docker-compose.yml")]
        compose_file = next((p for p in candidates if p.is_file()), None)
        if compose_file is None:
            raise ComposeError("No Compose file found. Use -f to select one.")
    compose_file = compose_file.resolve()
    overrides = [base / name for name in ("compose.override.yaml", "compose.override.yml", "docker-compose.override.yaml", "docker-compose.override.yml")]
    if not explicit and any(p.is_file() for p in overrides):
        raise ComposeError("An override file was found. This version supports one Compose file; use -f to explicitly scope the diagnosis.")
    text = _read(compose_file)
    try:
        root_node = yaml.compose(text, Loader=_UniqueLoader)
        if root_node is None:
            raise ComposeError("The Compose file is empty.")
        locations = _locations(root_node, _relative(compose_file, base))
        document = yaml.load(text, Loader=_UniqueLoader)
    except yaml.YAMLError as error:
        mark = getattr(error, "problem_mark", None)
        line = f" at line {mark.line + 1}" if mark else ""
        raise ComposeError(f"Invalid or unsupported YAML in {compose_file.name}{line}.") from None
    if not isinstance(document, dict) or not isinstance(document.get("services"), dict) or not document["services"]:
        raise ComposeError("The Compose file must contain a nonempty services mapping.")
    if "include" in document:
        raise ComposeError("Compose include is not supported; diagnose a single self-contained file.")
    all_services = document["services"]
    for name, config in all_services.items():
        if not isinstance(name, str) or not isinstance(config, dict):
            raise ComposeError("Each Compose service must have a string name and mapping configuration.")
        if "extends" in config:
            raise ComposeError("Compose extends is not supported; diagnose a single self-contained file.")
    findings, notes, seen_missing = [], [], set()

    def missing(name: str, required: bool, loc: SourceLocation, service: str | None = None):
        identity = (name, required, loc.file, loc.line, service)
        if identity in seen_missing:
            return
        seen_missing.add(identity)
        findings.append(Finding(
            code="WNU001", severity="error" if required else "warning",
            title=f"{'Required' if required else 'Referenced'} variable {name} is not set",
            explanation="Compose cannot resolve a required interpolation value." if required else "Compose substitutes an empty string for this unset variable; check whether that is intended.",
            service=service, evidence=[Evidence(f"Interpolation references {name}.", loc.file, loc.line)],
            suggestion=Suggestion(f"Set {name} in the interpolation environment or the project dotenv file. No value is guessed.")))

    shell = dict(os.environ if environ is None else environ)
    if env_file is None and shell.get("COMPOSE_ENV_FILES"):
        raise ComposeError("COMPOSE_ENV_FILES is set. This version supports one interpolation dotenv file; use --env-file to select it explicitly.")
    disable_default_dotenv = shell.get("COMPOSE_DISABLE_ENV_FILE") in {"1", "t", "T", "TRUE", "true", "True"}
    selected_dotenv = Path(env_file) if env_file is not None else base / ".env"
    if not selected_dotenv.is_absolute():
        selected_dotenv = base / selected_dotenv
    dotenv_values: dict[str, str | None] = {}
    dotenv_positions: dict[str, int] = {}
    interpolation_env_file: Path | None = None
    if (env_file is not None or not disable_default_dotenv) and selected_dotenv.is_file():
        dotenv_values, dotenv_positions = _dotenv(selected_dotenv, shell,
            lambda key, required, line: missing(key, required, SourceLocation(_relative(selected_dotenv, base), line)))
        interpolation_env_file = selected_dotenv.resolve()
    elif env_file is not None:
        raise ComposeError("The selected interpolation dotenv file does not exist.")
    environment = {**{k: v for k, v in dotenv_values.items() if v is not None}, **shell}
    if env_file is None and environment.get("COMPOSE_ENV_FILES"):
        raise ComposeError("COMPOSE_ENV_FILES is set. This version supports one interpolation dotenv file; use --env-file to select it explicitly.")
    if env_file is not None and environment.get("COMPOSE_ENV_FILES"):
        notes.append("COMPOSE_ENV_FILES is not used: --env-file explicitly selects the one interpolation dotenv file.")
    if not explicit and environment.get("COMPOSE_FILE"):
        raise ComposeError("COMPOSE_FILE is set. This version supports one Compose file; use -f to explicitly select its diagnostic scope.")
    selected_profiles = set(profiles or [])
    if profiles is None and environment.get("COMPOSE_PROFILES"):
        selected_profiles.update(p.strip() for p in environment["COMPOSE_PROFILES"].split(",") if p.strip())
    active, raw_active = {}, {}
    for name, config in all_services.items():
        service_profiles = config.get("profiles", [])
        if not isinstance(service_profiles, list) or any(not isinstance(p, str) for p in service_profiles):
            raise ComposeError("Service profiles must be a list of strings.")
        service_profiles = [_interpolate(p, environment,
            lambda key, required, index=index: missing(key, required, locations.get(("services", name, "profiles", index), SourceLocation(compose_file.name)), name))
            for index, p in enumerate(service_profiles)]
        if service_profiles and "*" not in selected_profiles and not selected_profiles.intersection(service_profiles):
            notes.append(f"Service {name} was excluded by its profile.")
            continue
        raw_active[name] = config

        def resolve(value: Any, key_path: tuple) -> Any:
            if isinstance(value, str):
                loc = locations.get(key_path, SourceLocation(_relative(compose_file, base)))
                return _interpolate(value, environment, lambda key, required: missing(key, required, loc, name))
            if isinstance(value, dict):
                return {k: resolve(v, key_path + (k,)) for k, v in value.items()}
            if isinstance(value, list):
                return [resolve(v, key_path + (i,)) for i, v in enumerate(value)]
            return value

        active[name] = resolve(config, ("services", name))
    if not active:
        notes.append("No services are active in the selected profiles.")
    effective, secrets = {}, _collect_secrets(environment)
    for name, config in active.items():
        container_env: dict[str, str] = {}
        files = config.get("env_file", [])
        if isinstance(files, (str, dict)):
            files = [files]
        if not isinstance(files, list):
            raise ComposeError("Service env_file must be a path, mapping, or list.")
        for index, item in enumerate(files):
            if not isinstance(item, (str, dict)):
                raise ComposeError("Each service env_file entry must be a path or mapping.")
            filename = item if isinstance(item, str) else item.get("path")
            if not isinstance(filename, str) or not filename:
                raise ComposeError("Each service env_file entry needs a nonempty path.")
            required = item.get("required", True) if isinstance(item, dict) else True
            raw_format = item.get("format", "") if isinstance(item, dict) else ""
            if not isinstance(required, bool) or raw_format not in ("", "raw"):
                raise ComposeError("Unsupported service env_file options.")
            target = Path(filename)
            if not target.is_absolute():
                target = base / target
            loc = locations.get(("services", name, "env_file", index), locations.get(("services", name, "env_file"), SourceLocation(compose_file.name)))
            if not target.is_file():
                if required:
                    findings.append(Finding("WNU002", "error", "Required service env file is missing", "Compose cannot read the configured env_file.", name,
                        evidence=[Evidence("Required env_file path is absent.", loc.file, loc.line)],
                        suggestion=Suggestion("Create the env file or correct its path; do not commit real credentials.", file=loc.file, line=loc.line)))
                continue
            values, positions = _dotenv(target, environment,
                lambda key, required, line: missing(key, required, SourceLocation(_relative(target, base), line), name), raw=raw_format == "raw", inherited=container_env)
            secrets.update(_collect_secrets(values))
            for key, value in values.items():
                if value is None:
                    container_env.pop(key, None)
                else:
                    container_env[key] = value
                    locations[("service_environment", name, key)] = SourceLocation(_relative(target, base), positions[key])
        declared = config.get("environment", {})
        pairs = []
        if isinstance(declared, dict):
            pairs = [(key, value, ("services", name, "environment", key)) for key, value in declared.items()]
        elif isinstance(declared, list):
            for index, item in enumerate(declared):
                if not isinstance(item, str):
                    raise ComposeError("List-style environment entries must be strings.")
                key, separator, value = item.partition("=")
                pairs.append((key, value if separator else None, ("services", name, "environment", index)))
        elif declared is not None:
            raise ComposeError("Service environment must be a mapping or list.")
        for key, value, position in pairs:
            if not isinstance(key, str) or not _KEY.fullmatch(key):
                raise ComposeError("Environment names must be valid variable names.")
            if isinstance(value, (bool, dict, list)):
                raise ComposeError("Environment values must be strings, numbers, or null; quote YAML booleans.")
            value = environment.get(key) if value is None else str(value)
            if value is None:
                container_env.pop(key, None)
            else:
                container_env[key] = value
                locations[("service_environment", name, key)] = locations.get(position, SourceLocation(compose_file.name))
                raw_environment = all_services[name].get("environment", {})
                if isinstance(raw_environment, dict):
                    original = raw_environment.get(key)
                elif isinstance(raw_environment, list):
                    original = next((entry.partition("=")[2] for entry in raw_environment if isinstance(entry, str) and entry.partition("=")[0] == key), None)
                else:
                    original = None
                single_variable = re.fullmatch(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)", original) if isinstance(original, str) else None
                if single_variable:
                    referenced = single_variable.group(1) or single_variable.group(2)
                    if referenced in dotenv_positions and referenced not in shell:
                        locations[("service_environment", name, key)] = SourceLocation(_relative(selected_dotenv, base), dotenv_positions[referenced])
        effective[name] = container_env
        secrets.update(_collect_secrets(container_env))
        if config.get("build"):
            notes.append(f"Builds and application code are not inspected for service {name}.")
    if explicit and any(p.is_file() and p.resolve() != compose_file for p in overrides):
        notes.append("Other override files exist but are outside the explicitly selected single-file scope.")
    if environment.get("COMPOSE_FILE"):
        notes.append("COMPOSE_FILE is not used: this report is scoped to the selected single file.")
    return ComposeProject(base, compose_file, active, all_services, environment, effective, locations, findings, notes, secrets, tuple(sorted(selected_profiles)),
                          interpolation_env_file=interpolation_env_file)
