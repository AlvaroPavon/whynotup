from __future__ import annotations

import json
from dataclasses import asdict

from . import __version__
from .models import Report
from .redaction import Redactor


def render(report: Report, *, format: str = "text", redactor: Redactor | None = None) -> str:
    data = (redactor or Redactor()).sanitize(asdict(report))
    if format == "json":
        return json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    if format not in ("text", "markdown"):
        raise ValueError("Unsupported report format")
    md = format == "markdown"
    lines = [f"{'# ' if md else ''}WhyNotUp {__version__} — {data['project']}", "", f"Compose file: {data['compose_file']}", ""]
    findings = data["findings"]
    if not findings:
        lines += ["No findings in the completed checks.", ""]
    else:
        errors = sum(f["severity"] == "error" for f in findings)
        lines += [f"{len(findings)} finding(s): {errors} error(s), {len(findings) - errors} warning(s).", ""]
    for finding in findings:
        service = f" ({finding['service']})" if finding["service"] else ""
        lines += [f"{'## ' if md else ''}[{finding['severity'].upper()}] {finding['code']}{service}: {finding['title']}",
                  f"Confidence: {finding['confidence']}", finding["explanation"]]
        for evidence in finding["evidence"]:
            source = evidence["file"] or "runtime"
            if evidence["line"] is not None:
                source += f":{evidence['line']}"
            lines.append(f"- Evidence [{source}]: {evidence['summary']}")
        suggestion = finding["suggestion"]
        if suggestion:
            lines.append(f"Suggestion: {suggestion['description']}")
            if suggestion["file"]:
                target = suggestion["file"] + (f":{suggestion['line']}" if suggestion["line"] else "")
                lines.append(f"Review in: {target}")
            if suggestion["before"] is not None and suggestion["after"] is not None:
                if md:
                    lines.append("```diff")
                lines += [f"- {suggestion['before']}", f"+ {suggestion['after']}"]
                if md:
                    lines.append("```")
        lines.append("")
    lines.append("## Check coverage" if md else "Check coverage:")
    for check in data["checks"]:
        reason = f" — {check['reason']}" if check["reason"] else ""
        lines.append(f"- {check['name']}: {check['status']}{reason}")
    if data["notes"]:
        lines += ["", "## Scope notes" if md else "Scope notes:"]
        lines += [f"- {note}" for note in data["notes"]]
    lines += ["", "Suggestions are review-only. No project files or containers were changed."]
    return "\n".join(lines) + "\n"
