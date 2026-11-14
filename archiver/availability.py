"""Whether an archive's original is still online, checked as a logged-out visitor.

"gone" is only reported on a clear answer (not found, deleted, site gone); any
refusal, rate limit or server error gives "unknown" rather than a false alarm."""
from . import integrations
from .integrations import GONE, ONLINE, UNKNOWN, page  # noqa: F401

# The platforms whose integration can tell, and web pages.
CHECKED = (*(module.NAME for module in integrations.ALL if hasattr(module, "online")), "Web")


def check(item, runner):
    """online, gone or unknown; None when the platform's originals are not checked."""
    if item["platform"] == "Web":
        return page(item["source_url"])
    module = integrations.find(item["platform"])
    return module.online(item, runner) if hasattr(module, "online") else None
