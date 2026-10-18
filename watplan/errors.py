class WatplanError(Exception):
    """An expected, user-facing failure."""

    code = "watplan_error"
    status = 400


class InvalidRequest(WatplanError):
    code = "invalid_request"


class UnknownGroup(WatplanError):
    code = "unknown_group"
    status = 404


class UnknownSemester(WatplanError):
    code = "unknown_semester"
    status = 404


class SourceError(WatplanError):
    code = "source_unavailable"
    status = 502


class ParseError(SourceError):
    code = "source_format_changed"

