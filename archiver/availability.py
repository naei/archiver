"""Whether an archive's original is still online, checked as a logged-out visitor.

"gone" is only reported on a clear answer (not found, deleted, site gone); any
refusal, rate limit or server error gives "unknown" rather than a false alarm."""
from . import integrations
from .integrations import GONE, ONLINE, UNKNOWN, page  # noqa: F401


def check(item, runner):
    """online, gone or unknown; None when this archive cannot be checked."""
    if item["platform"] == "Web":
        return page(item["source_url"])
    return integrations.find(item["platform"]).online(item, runner)
