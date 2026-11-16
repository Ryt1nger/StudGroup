"""Canonical, stable wire states for SDO course/link enrichment.

Stored and returned as their existing string values for old clients.
These states describe LINK extraction only, not schedule validation.
"""

from enum import StrEnum


class LinkState(StrEnum):
    MATCHED = "matched"
    NOT_FOUND = "not_found"
    TIMING_UNVERIFIED = "timing_unverified"
    NO_COURSE_LINKS = "no_course_links"
    MISSING_COURSE_TRANSITION = "missing_course_transition"
    LMS_LOGIN_REQUIRED = "lms_login_required"
    COURSE_CONTENT_UNAVAILABLE = "course_content_unavailable"
    LMS_OTHER_PAGE = "lms_other_page"
    CABINET_TRANSITION_UNAVAILABLE = "cabinet_transition_unavailable"
    UNEXPECTED_REDIRECT = "unexpected_redirect"
    NAVIGATION_ERROR = "navigation_error"
    CAPTCHA_REQUIRED = "captcha_required"
    IDENTITY_MISSING = "identity_missing"
    AMBIGUOUS = "ambiguous"
    NOT_CHECKED = "not_checked"


LINK_STATES = frozenset(state.value for state in LinkState)
