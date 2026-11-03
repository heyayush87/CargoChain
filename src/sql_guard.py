import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

from src.db import VIEW_NAME

MAX_ROWS = 100
MAX_PLAN_COST = 100_000.0

ALLOWED_RELATION = VIEW_NAME.lower()

# Anything that changes state, session settings, or reaches outside the view
FORBIDDEN_NODES = (
    exp.Insert, exp.Update, exp.Delete, exp.Merge, exp.Create, exp.Drop, exp.Alter,
    exp.Command, exp.Set, exp.Copy, exp.Into, exp.Lock,
)
FORBIDDEN_FUNCTION_PREFIXES = ("pg_", "lo_", "dblink", "set_config", "current_setting")


class SqlRejected(ValueError):
    """The generated SQL failed validation and must not be executed."""


def validate_select(sql: str) -> str:
    """
    Parse the model's SQL and return a safe single SELECT with a row limit enforced.
    Raises SqlRejected for anything else. The database role is still the real
    security boundary; this layer gives the model a clear error and caps the output.
    """
    try:
        statements = sqlglot.parse(sql, read="postgres")
    except SqlglotError as e:
        raise SqlRejected(f"SQL could not be parsed: {e}")

    statements = [s for s in statements if s is not None]
    if len(statements) != 1:
        raise SqlRejected("Exactly one SQL statement is allowed.")

    tree = statements[0]
    if not isinstance(tree, (exp.Select, exp.Union)):
        raise SqlRejected("Only SELECT statements are authorized on this view.")

    for node in tree.walk():
        if isinstance(node, FORBIDDEN_NODES):
            raise SqlRejected(f"{type(node).__name__.upper()} is not allowed.")
        if isinstance(node, (exp.Func, exp.Anonymous)):
            name = (node.name or node.sql_name() or "").lower()
            if name.startswith(FORBIDDEN_FUNCTION_PREFIXES):
                raise SqlRejected(f"Function '{name}' is not allowed.")

    ctes = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
    for table in tree.find_all(exp.Table):
        qualified = ".".join(p for p in (table.db, table.name) if p).lower()
        if qualified in ctes or table.name.lower() in ctes:
            continue
        if qualified != ALLOWED_RELATION:
            raise SqlRejected(f"Only {VIEW_NAME} may be queried (got '{qualified}').")

    # Enforce a ceiling on returned rows, keeping a smaller limit if the model set one
    limit = tree.args.get("limit")
    if limit is None:
        tree = tree.limit(MAX_ROWS)
    else:
        try:
            requested = int(limit.expression.name)
        except (ValueError, AttributeError):
            requested = MAX_ROWS
        if requested > MAX_ROWS:
            tree = tree.limit(MAX_ROWS)

    return tree.sql(dialect="postgres")
