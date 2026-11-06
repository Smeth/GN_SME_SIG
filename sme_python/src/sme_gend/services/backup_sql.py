"""Lecture du dialecte de sauvegarde produit par l'application.

Les noms de tables sont lus dans l'en-tête de l'instruction, jamais dans ses
valeurs. La restauration refuse les instructions étrangères à ce dialecte.
"""
import re


def split_statements(sql: str) -> list[str]:
    statements, buffer = [], []
    quote = None
    i = 0
    while i < len(sql):
        char = sql[i]
        if quote:
            buffer.append(char)
            if char == "\\" and quote != "`" and i + 1 < len(sql):
                i += 1
                buffer.append(sql[i])
            elif char == quote:
                if i + 1 < len(sql) and sql[i + 1] == quote:
                    i += 1
                    buffer.append(sql[i])
                else:
                    quote = None
        elif char in "'\"`":
            quote = char
            buffer.append(char)
        elif char == "#" or (sql.startswith("--", i) and
                              (i + 2 == len(sql) or sql[i + 2].isspace())):
            end = sql.find("\n", i)
            i = len(sql) if end == -1 else end
            buffer.append("\n")
            continue
        elif sql.startswith("/*", i):
            end = sql.find("*/", i + 2)
            if end == -1:
                raise ValueError("Commentaire SQL non terminé.")
            if sql.startswith("/*!", i):
                raise ValueError("Commentaire SQL exécutable non pris en charge.")
            buffer.append(" ")
            i = end + 2
            continue
        elif char == ";":
            text = "".join(buffer).strip()
            if text:
                statements.append(text + ";")
            buffer.clear()
        else:
            buffer.append(char)
        i += 1
    if quote:
        raise ValueError("Chaîne ou identifiant SQL non terminé.")
    tail = "".join(buffer).strip()
    if tail:
        statements.append(tail)
    return statements


_IDENTIFIER = r"(?:`(?:``|[^`])+`|[A-Za-z_][A-Za-z0-9_$]*)"
_HEAD = re.compile(
    r"^\s*(?:DROP\s+TABLE\s+(?:IF\s+EXISTS\s+)?|"
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?|"
    r"INSERT\s+INTO\s+|DELETE\s+FROM\s+|ALTER\s+TABLE\s+|"
    r"TRUNCATE\s+(?:TABLE\s+)?)(" + _IDENTIFIER + r")(?=\s|\(|;|$)", re.I,
)


def target_table(statement: str) -> str | None:
    match = _HEAD.match(statement)
    if not match:
        return None
    raw = match[1]
    return raw[1:-1].replace("``", "`") if raw.startswith("`") else raw


def is_safe_setting(statement: str) -> bool:
    if re.fullmatch(r"\s*SET\s+SESSION\s+SQL_MODE\s*=\s*'NO_AUTO_VALUE_ON_ZERO'\s*;?\s*", statement, re.I):
        return True
    return bool(re.fullmatch(
        r"\s*SET\s+(?:NAMES\s+utf8(?:mb4)?|FOREIGN_KEY_CHECKS\s*=\s*[01])\s*;?\s*",
        statement, re.I,
    ))


def validate_restore_statement(statement: str) -> None:
    """Le dump interne ne contient ni plusieurs cibles ni INSERT ... SELECT."""
    if is_safe_setting(statement):
        return
    match = _HEAD.match(statement)
    if not match:
        raise ValueError("Instruction SQL étrangère au format de sauvegarde.")
    command = statement.lstrip().split(None, 1)[0].upper()
    tail = statement[match.end():].strip().removesuffix(";").strip()
    if command == "DROP" and not tail:
        return
    if command == "CREATE" and tail.startswith("("):
        return
    if command == "INSERT":
        columns = rf"(?:\(\s*{_IDENTIFIER}(?:\s*,\s*{_IDENTIFIER})*\s*\)\s*)?"
        prefix = re.match(columns + r"VALUES\s*", tail, re.I)
        if prefix:
            literal = r"(?:NULL|(?:_utf8mb4\s+)?0x[0-9a-f]+|[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?|'(?:[^'\\]|\\[\s\S]|'')*')"
            row = rf"\(\s*{literal}(?:\s*,\s*{literal})*\s*\)"
            if re.fullmatch(row + rf"(?:\s*,\s*{row})*", tail[prefix.end():], re.I):
                return
    raise ValueError("Instruction non conforme au dump produit par l'application.")
