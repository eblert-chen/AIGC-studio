"""Transaction boundary for multi-query financial evidence."""

from sqlalchemy.orm import Session

from .errors import ConflictError


def begin_finance_snapshot(session: Session) -> None:
    """Choose isolation before the first SQL; never commit a caller's work.

    Callers with an already-started PostgreSQL transaction must explicitly use
    REPEATABLE READ/SERIALIZABLE. Silently accepting READ COMMITTED can compare
    a wallet before a capture with its ledger after it and persist a false gap.
    """
    if session.get_bind().dialect.name != "postgresql":
        return
    if not session.in_transaction():
        connection = session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
    else:
        connection = session.connection()
    if connection.get_isolation_level() not in {"REPEATABLE READ", "SERIALIZABLE"}:
        raise ConflictError("财务对账必须在独立的一致性快照事务中执行，请使用原幂等键重试")
