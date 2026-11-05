"""
Opt-In Personalization & Daily Concierge Studio (`slm_harness.personalization`).

Strictly opt-in personal profile, local 0600 credential vault, daily news/social dashboard,
email tracking & reply reminders, Naukri/LinkedIn job matcher & auto-apply assistant,
and X/Twitter + internet learning opportunity discovery.
"""

from .concierge import (
    PersonalConciergeEngine,
    detect_concierge_intent,
    get_concierge_engine,
    is_web_cloud_environment,
)

__all__ = [
    "PersonalConciergeEngine",
    "detect_concierge_intent",
    "get_concierge_engine",
    "is_web_cloud_environment",
]
