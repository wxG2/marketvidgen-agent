from __future__ import annotations


class QwenClientError(RuntimeError):
    def __init__(self, message: str, *, code: str):
        super().__init__(message)
        self.code = code


class QwenRequestError(QwenClientError):
    def __init__(self, message: str):
        super().__init__(message, code="llm_request_failed")


class QwenResponseParseError(QwenClientError):
    def __init__(self, message: str):
        super().__init__(message, code="llm_parse_failed")


class QwenResponseValidationError(QwenClientError):
    def __init__(self, message: str):
        super().__init__(message, code="llm_validation_failed")
