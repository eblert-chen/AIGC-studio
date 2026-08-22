class DomainError(Exception):
    def __init__(self, message: str, code: str = "domain_error", status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.code = code
        self.status_code = status_code


class NotFoundError(DomainError):
    def __init__(self, message: str):
        super().__init__(message, "not_found", 404)


class ConflictError(DomainError):
    def __init__(self, message: str):
        super().__init__(message, "conflict", 409)


class InsufficientBalanceError(DomainError):
    def __init__(self):
        super().__init__("公司可用余额不足", "insufficient_balance", 409)


class PermissionDeniedError(DomainError):
    def __init__(self, message: str = "没有执行该操作的权限"):
        super().__init__(message, "permission_denied", 403)
