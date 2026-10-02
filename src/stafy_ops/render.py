from stafy_ops.schemas import IssueDraft, Kind


def _bullets(items: list[str], checkbox: bool = False) -> str:
    if not items:
        return "- None"
    prefix = "- [ ] " if checkbox else "- "
    return "\n".join(f"{prefix}{i}" for i in items)


def _impact(d: IssueDraft, with_api: bool) -> str:
    lines = []
    if with_api:
        lines.append(f"- API contract: {d.api_contract}")
    lines += [f"- Roles/RBAC: {d.roles}", f"- Money data (`rate_applied`, reports): {'yes' if d.money_data else 'no'}"]
    return "\n".join(lines)


def _criteria(d: IssueDraft) -> list[str]:
    criteria = list(d.acceptance_criteria)
    if d.money_data or d.roles.strip().lower() not in ("", "none"):
        criteria.append("security-auditor pass before merge")
    return criteria


def render_body(d: IssueDraft) -> str:
    """Mirrors the templates in the workspace repo's .github/ISSUE_TEMPLATE/ (same section headings)."""
    if d.kind == Kind.BUG:
        parts = [
            ("What happens", d.summary),
            ("Expected", d.expected or "Not stated"),
            ("Steps to reproduce", "\n".join(f"{n}. {s}" for n, s in enumerate(d.steps, 1)) or "Not stated"),
            ("Environment", d.environment or "Not stated"),
            ("Impact", _impact(d, with_api=False)),
            ("Acceptance criteria", _bullets(_criteria(d), checkbox=True)),
        ]
    elif d.kind == Kind.FEATURE:
        parts = [
            ("Gap", d.summary),
            ("Context", d.context),
            ("Why it matters", d.why or "Not stated"),
            ("Acceptance criteria", _bullets(_criteria(d), checkbox=True)),
            ("Out of scope", _bullets(d.out_of_scope)),
            ("Impact", _impact(d, with_api=True)),
            ("Open questions", _bullets(d.open_questions)),
        ]
    else:
        parts = [
            ("Gap", d.summary),
            ("Acceptance criteria", _bullets(_criteria(d), checkbox=True)),
            ("Out of scope", _bullets(d.out_of_scope)),
        ]
    return "\n\n".join(f"## {title}\n{body}" for title, body in parts) + "\n"
