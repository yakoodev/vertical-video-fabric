"""Доказательство, что разбор app/store.py и app/render.py механический: сверка с коммитом до разбора.

Запуск из корня репозитория: ``python tools/split_check.py [коммит]``. Для каждого определения прежнего файла:

1. **Текст и AST.** Определение (с декораторами, без импортов и окружающих комментариев) есть ровно один раз среди
   нового файла и его частей, текст совпадает побайтно (без учёта конца строки), ``ast.dump`` одинаков.
2. **Байткод.** Прежний файл исполняется как отдельный модуль; у функций и методов сравниваются объекты кода рекурсивно
   (без имени файла, номеров строк и qualname), значения по умолчанию и атрибуты класса; константы — по значению.
   Единственное допущение — запись вызова ``x.m()``, зависящая от импортов файла (``canonical``), такие функции выводятся.
3. **Имена.** Каждое глобальное имя, которое читает перенесённый код, в его новом модуле указывает на то же, что в прежнем
   файле: тот же объект (импорты), функцию с тем же кодом или равное значение. Прежний модуль и новый содержат одни имена.

Новые определения (классы-части, сборка модуля) перечисляются в выводе, код возврата 1 — если что-то разошлось.
"""

from __future__ import annotations

import ast
import builtins
import dis
import importlib
import subprocess
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
DEFAULT_BASE = "a578ad0"
TARGETS = (("store", "AppStore"), ("render", "ClipRenderService"))
NOT_PARTS = {"render_qc", "render_queue", "render_library_api"}  # и до разбора были отдельными модулями


def part_names(stem: str) -> list[str]:
    return sorted(p.stem for p in (ROOT / "app").glob(f"{stem}_*.py") if p.stem not in NOT_PARTS)


def statements(text: str, cls: str) -> tuple[dict[str, str], dict[str, str], list[str]]:
    """Определения верхнего уровня и члены класса: имя → текст оператора; плюс все прочие классы как члены."""
    lines = text.replace("\r\n", "\n").split("\n")
    top: dict[str, str] = {}
    members: dict[str, str] = {}
    other: list[str] = []

    def source(node) -> str:
        first = node.decorator_list[0].lineno if getattr(node, "decorator_list", None) else node.lineno
        return "\n".join(lines[first - 1 : node.end_lineno])

    def name_of(node) -> str | None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return node.name
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            return node.targets[0].id
        if isinstance(node, ast.AnnAssign):
            return node.target.id
        return None

    for node in ast.parse("\n".join(lines)).body:
        if isinstance(node, ast.ClassDef):
            bucket = node.name == cls or node.name.endswith(("Store", "Render"))
            if bucket:
                for member in node.body:
                    name = name_of(member)
                    if name is None:  # докстринг
                        continue
                    if name in members:
                        raise SystemExit(f"метод {name} определён дважды")
                    members[name] = source(member)
            else:
                other.append(node.name)
        else:
            name = name_of(node)
            if name is not None:
                if name in top:
                    raise SystemExit(f"{name} определён дважды")
                top[name] = source(node)
    return top, members, other


def canonical(code: types.CodeType) -> tuple[list, list]:
    """Команды и таблица исключений, где две записи вызова ``x.m()`` приведены к одной, а переходы — к номерам команд.

    CPython 3.12 компилирует ``x.m()`` по-разному в зависимости от того, импортировано ли имя ``x`` на верхнем уровне
    файла (compile.c, ``is_import_originated``): ``PUSH_NULL; x; LOAD_ATTR m`` вместо ``x; LOAD_ATTR метод m``. Значение
    вызова то же. Разбор меняет только импорты, поэтому такая разница возможна и больше никакая."""
    ins = list(dis.get_instructions(code))
    out: list[list] = []
    index: dict[int, int] = {}
    i = 0
    while i < len(ins):
        cur = ins[i]
        index[cur.offset] = len(out)
        nxt = ins[i + 1] if i + 1 < len(ins) else None
        after = ins[i + 2] if i + 2 < len(ins) else None
        if (cur.opname == "PUSH_NULL" and nxt and after and nxt.opname.startswith("LOAD_") and after.opname == "LOAD_ATTR"
                and not after.argrepr.startswith("NULL|self")):
            index[nxt.offset] = len(out)
            index[after.offset] = len(out) + 1
            out += [[nxt.opname, nxt.argval, None], ["LOAD_METHOD", after.argval, None]]
            i += 3
            continue
        if cur.opname == "LOAD_GLOBAL" and cur.argrepr.startswith("NULL + ") and nxt and nxt.opname == "LOAD_ATTR" \
                and not nxt.argrepr.startswith("NULL|self"):
            index[nxt.offset] = len(out) + 1
            out += [["LOAD_GLOBAL", cur.argval, cur.argval], ["LOAD_METHOD", nxt.argval, None]]
            i += 2
            continue
        if cur.opname == "LOAD_ATTR" and cur.argrepr.startswith("NULL|self"):
            out.append(["LOAD_METHOD", cur.argval, None])
        elif cur.opcode in dis.hasjrel:
            out.append([cur.opname, cur.argval, "jump"])
        elif isinstance(cur.argval, types.CodeType):
            out.append([cur.opname, "<code>", None])
        else:
            # LOAD_GLOBAL без подмены NULL: argrepr отличает «NULL + f» (вызов функции) от чтения значения.
            out.append([cur.opname, cur.argval, cur.argrepr if cur.opname == "LOAD_GLOBAL" else None])
        i += 1
    for row in out:
        if row[2] == "jump":
            row[1] = index[row[1]]
    exceptions = [[index[e.start], index[e.end] if e.end in index else None, index[e.target], e.depth, e.lasti]
                  for e in dis._parse_exception_table(code)]
    return out, exceptions


# Функции, где байткод совпал только после приведения вызова x.m() к одной записи.
METHOD_FORM_ONLY: list[str] = []


def same_code(a: types.CodeType, b: types.CodeType) -> bool:
    for attr in ("co_names", "co_varnames", "co_freevars", "co_cellvars", "co_argcount", "co_posonlyargcount",
                 "co_kwonlyargcount", "co_flags"):
        if getattr(a, attr) != getattr(b, attr):
            return False
    if a.co_code != b.co_code or a.co_exceptiontable != b.co_exceptiontable:
        if canonical(a) != canonical(b):
            return False
        METHOD_FORM_ONLY.append(a.co_name)
    if len(a.co_consts) != len(b.co_consts):
        return False
    for x, y in zip(a.co_consts, b.co_consts):
        if isinstance(x, types.CodeType) and isinstance(y, types.CodeType):
            if not same_code(x, y):
                return False
        elif type(x) is not type(y) or x != y:
            return False
    return True


def plain(fn):
    """Функция под staticmethod и декораторами с functools.wraps."""
    if isinstance(fn, (staticmethod, classmethod)):
        fn = fn.__func__
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


def global_reads(code: types.CodeType) -> set[str]:
    names = {i.argval for i in dis.get_instructions(code) if i.opname in ("LOAD_GLOBAL", "LOAD_NAME")}
    for const in code.co_consts:
        if isinstance(const, types.CodeType):
            names |= global_reads(const)
    return names


def same_function(a, b) -> bool:
    a, b = plain(a), plain(b)
    return same_code(a.__code__, b.__code__) and a.__defaults__ == b.__defaults__ and a.__kwdefaults__ == b.__kwdefaults__


def same_value(a, b) -> bool:
    if a is b:
        return True
    if isinstance(plain(a), types.FunctionType) and isinstance(plain(b), types.FunctionType):
        return same_function(a, b)
    if type(a).__name__ == "ContextVar" and type(b).__name__ == "ContextVar":
        return a.name == b.name
    return type(a) is type(b) and a == b


def check(stem: str, cls: str, base: str) -> list[str]:
    problems: list[str] = []
    rel = f"app/{stem}.py"
    old_text = subprocess.run(["git", "show", f"{base}:{rel}"], cwd=ROOT, capture_output=True, check=True).stdout.decode("utf-8")
    old_top, old_members, _ = statements(old_text, cls)

    new_top: dict[str, str] = {}
    new_members: dict[str, str] = {}
    home: dict[str, str] = {}
    for name in [stem] + part_names(stem):
        top, members, other = statements((ROOT / "app" / f"{name}.py").read_text(encoding="utf-8"), cls)
        for key, value in top.items():
            if key in new_top:
                problems.append(f"{key} определён и в {home[key]}, и в {name}")
            new_top[key] = value
            home[key] = name
        for key, value in members.items():
            if key in new_members:
                problems.append(f"метод {key} определён и в {home['.' + key]}, и в {name}")
            new_members[key] = value
            home["." + key] = name

    identical = 0
    for label, old, new in (("имя", old_top, new_top), ("метод", old_members, new_members)):
        for key, text in old.items():
            if key not in new:
                problems.append(f"{stem}: пропал {label} {key}")
            elif new[key] != text:
                problems.append(f"{stem}: {label} {key}: текст отличается")
            elif ast.dump(ast.parse(text.lstrip()) if label == "имя" else ast.parse("class X:\n" + text)) != ast.dump(
                ast.parse(new[key].lstrip()) if label == "имя" else ast.parse("class X:\n" + new[key])
            ):
                problems.append(f"{stem}: {label} {key}: AST отличается")
            else:
                identical += 1

    # Прежний файл живьём: тот же интерпретатор, те же зависимости (app.*), отдельный объект модуля с прежним именем.
    old_mod = types.ModuleType(f"app.{stem}")
    exec(compile(old_text, f"<{base}:{rel}>", "exec"), old_mod.__dict__)  # noqa: S102 - свой файл из истории git
    new_mod = importlib.import_module(f"app.{stem}")
    old_cls, new_cls = getattr(old_mod, cls), getattr(new_mod, cls)

    bytecode = 0
    for key in old_members:
        a, b = old_cls.__dict__[key], inspect_static(new_cls, key)
        if isinstance(plain(a), types.FunctionType):
            if not same_function(a, b):
                problems.append(f"{stem}: метод {key}: байткод отличается")
            bytecode += 1
        elif a != b:
            problems.append(f"{stem}: атрибут класса {key}: значение отличается")

    names = 0
    for key, old_value in vars(old_mod).items():
        if key.startswith("__") and key.endswith("__"):
            continue
        names += 1
        if key not in vars(new_mod):
            problems.append(f"{stem}: имени {key} больше нет в app.{stem}")
        elif key != cls and not same_value(old_value, vars(new_mod)[key]):
            problems.append(f"{stem}: {key}: другое значение")
        if isinstance(old_value, types.FunctionType):
            bytecode += 1

    reads = 0
    own = {f"app.{name}" for name in [stem] + part_names(stem)}
    new_functions = [plain(v) for v in vars(new_mod).values() if isinstance(plain(v), types.FunctionType)
                     and plain(v).__module__ in own]
    new_functions += [plain(inspect_static(new_cls, k)) for k in old_members
                      if isinstance(plain(inspect_static(new_cls, k)), types.FunctionType)]
    for fn in new_functions:
        for name in global_reads(fn.__code__):
            reads += 1
            if name not in fn.__globals__:
                if not hasattr(builtins, name):
                    problems.append(f"{stem}: {fn.__name__} читает {name}, а в {fn.__module__} его нет")
            elif name in vars(old_mod) and name != cls and not same_value(vars(old_mod)[name], fn.__globals__[name]):
                problems.append(f"{stem}: {fn.__name__}: {name} в {fn.__module__} не то, что было")

    extra_top = sorted(set(new_top) - set(old_top))
    extra_members = sorted(set(new_members) - set(old_members))
    print(f"app/{stem}.py: определений {len(old_top) + len(old_members)}, побайтно и по AST совпали {identical}; "
          f"функций с тем же байткодом {bytecode}; имён модуля {names}; чтений глобальных имён сверено {reads}")
    print(f"  части: {', '.join(part_names(stem)) or '-'}")
    print(f"  новые определения: верхний уровень {extra_top or '-'}, в классах {extra_members or '-'}")
    print(f"  байткод совпал после приведения x.m() к одной записи: {sorted(set(METHOD_FORM_ONLY)) or '-'}")
    METHOD_FORM_ONLY.clear()
    return problems


def inspect_static(cls, name):
    """Атрибут класса через MRO, но без привязки дескриптора."""
    for klass in cls.__mro__:
        if name in klass.__dict__:
            return klass.__dict__[name]
    raise AttributeError(name)


def main() -> int:
    base = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_BASE
    problems: list[str] = []
    for stem, cls in TARGETS:
        problems += check(stem, cls, base)
    for line in problems:
        print("РАСХОЖДЕНИЕ:", line)
    print("итог:", "есть расхождения" if problems else "всё совпало")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
