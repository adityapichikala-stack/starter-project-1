"""Code decides the status. The model never does."""
from app.models import Status


def decide_status(need_info_issues: list[str], shortage_issues: list[str]) -> Status:
    # Need info beats Partial, Partial beats Ready.
    if need_info_issues:
        return "Need info"
    if shortage_issues:
        return "Partial"
    return "Ready"
