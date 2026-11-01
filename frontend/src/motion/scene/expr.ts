/**
 * Безопасные выражения сцены: `"=200*sin(t*3) + i*40"`.
 *
 * Свой маленький разборщик (не eval / Function): арифметика, сравнения, тернарный оператор,
 * белый список функций и переменных. Ни циклов, ни доступа к объектам, ни строк — выражение
 * не может ничего, кроме как посчитать число, и всегда считается за ограниченное время.
 * Случайность — только сидовая (rand/noise от номера и зерна), поэтому кадр = f(t).
 */
import { noise1, rand01 } from "../keys";

export type ExprVars = {
  /** Время узла, с (после stagger / time.offset). */
  t: number;
  /** Время слоя, с. */
  T: number;
  /** Длина слоя, с. */
  dur: number;
  /** Номер копии в repeat (0..n−1) и их число; u = i/(n−1). */
  i: number;
  n: number;
  /** Зерно сцены (seed). */
  seed: number;
};

type Fn = (v: ExprVars) => number;

export const MAX_EXPR_LEN = 600;
const MAX_NODES = 400;

const FUNCS: Record<string, { n: [number, number]; f: (...a: number[]) => number }> = {
  sin: { n: [1, 1], f: Math.sin },
  cos: { n: [1, 1], f: Math.cos },
  tan: { n: [1, 1], f: Math.tan },
  asin: { n: [1, 1], f: Math.asin },
  acos: { n: [1, 1], f: Math.acos },
  atan: { n: [1, 1], f: Math.atan },
  atan2: { n: [2, 2], f: Math.atan2 },
  abs: { n: [1, 1], f: Math.abs },
  sign: { n: [1, 1], f: Math.sign },
  floor: { n: [1, 1], f: Math.floor },
  ceil: { n: [1, 1], f: Math.ceil },
  round: { n: [1, 1], f: Math.round },
  fract: { n: [1, 1], f: (x) => x - Math.floor(x) },
  sqrt: { n: [1, 1], f: (x) => Math.sqrt(Math.max(0, x)) },
  exp: { n: [1, 1], f: Math.exp },
  log: { n: [1, 1], f: (x) => Math.log(Math.max(1e-12, x)) },
  pow: { n: [2, 2], f: Math.pow },
  min: { n: [1, 8], f: Math.min },
  max: { n: [1, 8], f: Math.max },
  mod: { n: [2, 2], f: (a, b) => (b === 0 ? 0 : ((a % b) + b) % b) },
  clamp: { n: [3, 3], f: (x, a, b) => Math.min(b, Math.max(a, x)) },
  lerp: { n: [3, 3], f: (a, b, p) => a + (b - a) * p },
  mix: { n: [3, 3], f: (a, b, p) => a + (b - a) * p },
  step: { n: [2, 2], f: (e, x) => (x < e ? 0 : 1) },
  smoothstep: {
    n: [3, 3],
    f: (a, b, x) => {
      const q = Math.min(1, Math.max(0, (x - a) / (b - a || 1e-9)));
      return q * q * (3 - 2 * q);
    },
  },
  /** Туда-обратно: 0→len→0 с периодом 2·len. */
  pingpong: { n: [2, 2], f: (x, l) => (l <= 0 ? 0 : l - Math.abs((((x % (2 * l)) + 2 * l) % (2 * l)) - l)) },
  /** Треугольная волна 0..1 с периодом 1. */
  tri: { n: [1, 1], f: (x) => 1 - Math.abs(((x % 2) + 2) % 2 - 1) },
  deg: { n: [1, 1], f: (r) => (r * 180) / Math.PI },
  rad: { n: [1, 1], f: (d) => (d * Math.PI) / 180 },
};

/** Функции, которым нужно зерно сцены: rand(k) — [0,1), noise(x[, k]) — плавный шум [−1,1]. */
const SEEDED = new Set(["rand", "noise"]);

const VARS = new Set(["t", "T", "dur", "i", "n", "u", "p", "seed", "pi", "tau", "e"]);

class ExprError extends Error {}

type Tok = { k: "num" | "id" | "op"; v: string; pos: number };

function tokenize(src: string): Tok[] {
  const out: Tok[] = [];
  let i = 0;
  while (i < src.length) {
    const c = src[i];
    if (c === " " || c === "\t" || c === "\n") {
      i++;
      continue;
    }
    if (/[0-9.]/.test(c)) {
      const m = /^(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?/.exec(src.slice(i));
      if (!m) throw new ExprError(`непонятное число в позиции ${i}`);
      out.push({ k: "num", v: m[0], pos: i });
      i += m[0].length;
      continue;
    }
    if (/[A-Za-z_]/.test(c)) {
      const m = /^[A-Za-z_][A-Za-z0-9_]*/.exec(src.slice(i))!;
      out.push({ k: "id", v: m[0], pos: i });
      i += m[0].length;
      continue;
    }
    const two = src.slice(i, i + 2);
    if (["<=", ">=", "==", "!=", "&&", "||", "**"].includes(two)) {
      out.push({ k: "op", v: two === "**" ? "^" : two, pos: i });
      i += 2;
      continue;
    }
    if ("+-*/%^(),?:<>!".includes(c)) {
      out.push({ k: "op", v: c, pos: i });
      i++;
      continue;
    }
    throw new ExprError(`недопустимый символ «${c}» в позиции ${i}`);
  }
  return out;
}

const BIN_PREC: Record<string, number> = {
  "||": 1,
  "&&": 2,
  "==": 3,
  "!=": 3,
  "<": 4,
  ">": 4,
  "<=": 4,
  ">=": 4,
  "+": 5,
  "-": 5,
  "*": 6,
  "/": 6,
  "%": 6,
  "^": 8,
};

function binary(op: string, a: Fn, b: Fn): Fn {
  switch (op) {
    case "+":
      return (v) => a(v) + b(v);
    case "-":
      return (v) => a(v) - b(v);
    case "*":
      return (v) => a(v) * b(v);
    case "/":
      return (v) => {
        const d = b(v);
        return d === 0 ? 0 : a(v) / d;
      };
    case "%":
      return (v) => {
        const d = b(v);
        return d === 0 ? 0 : a(v) % d;
      };
    case "^":
      return (v) => Math.pow(a(v), b(v));
    case "<":
      return (v) => (a(v) < b(v) ? 1 : 0);
    case ">":
      return (v) => (a(v) > b(v) ? 1 : 0);
    case "<=":
      return (v) => (a(v) <= b(v) ? 1 : 0);
    case ">=":
      return (v) => (a(v) >= b(v) ? 1 : 0);
    case "==":
      return (v) => (a(v) === b(v) ? 1 : 0);
    case "!=":
      return (v) => (a(v) !== b(v) ? 1 : 0);
    case "&&":
      return (v) => (a(v) && b(v) ? 1 : 0);
    case "||":
      return (v) => (a(v) || b(v) ? 1 : 0);
    default:
      throw new ExprError(`оператор ${op}`);
  }
}

function variable(name: string): Fn {
  switch (name) {
    case "t":
      return (v) => v.t;
    case "T":
      return (v) => v.T;
    case "dur":
      return (v) => v.dur;
    case "p":
      return (v) => (v.dur > 0 ? v.T / v.dur : 0);
    case "i":
      return (v) => v.i;
    case "n":
      return (v) => v.n;
    case "u":
      return (v) => (v.n > 1 ? v.i / (v.n - 1) : 0);
    case "seed":
      return (v) => v.seed;
    case "pi":
      return () => Math.PI;
    case "tau":
      return () => Math.PI * 2;
    case "e":
      return () => Math.E;
    default:
      throw new ExprError(`неизвестная переменная «${name}»`);
  }
}

function parse(src: string): Fn {
  const toks = tokenize(src);
  let pos = 0;
  let nodes = 0;
  const peek = () => toks[pos];
  const take = (v?: string): Tok => {
    const t = toks[pos];
    if (!t) throw new ExprError(v ? `ожидалось «${v}» в конце` : "выражение оборвалось");
    if (v !== undefined && t.v !== v) throw new ExprError(`ожидалось «${v}», а стоит «${t.v}» (позиция ${t.pos})`);
    pos++;
    return t;
  };
  const count = () => {
    if (++nodes > MAX_NODES) throw new ExprError(`выражение слишком длинное (больше ${MAX_NODES} частей)`);
  };

  function primary(): Fn {
    count();
    const t = take();
    if (t.k === "num") {
      const n = Number(t.v);
      return () => n;
    }
    if (t.k === "op" && t.v === "(") {
      const e = ternary();
      take(")");
      return e;
    }
    if (t.k === "op" && (t.v === "-" || t.v === "+" || t.v === "!")) {
      const e = unary();
      return t.v === "-" ? (v) => -e(v) : t.v === "!" ? (v) => (e(v) ? 0 : 1) : e;
    }
    if (t.k === "id") {
      if (peek()?.v === "(") {
        take("(");
        const args: Fn[] = [];
        if (peek()?.v !== ")") {
          args.push(ternary());
          while (peek()?.v === ",") {
            take(",");
            args.push(ternary());
          }
        }
        take(")");
        if (SEEDED.has(t.v)) {
          if (t.v === "rand") {
            if (args.length < 1 || args.length > 2) throw new ExprError("rand(k[, зерно]) — 1 или 2 аргумента");
            const [k, s] = args;
            return (v) => rand01(((s ? s(v) : 0) * 7919 + v.seed) >>> 0, Math.floor(k(v)) | 0);
          }
          if (args.length < 1 || args.length > 2) throw new ExprError("noise(x[, зерно]) — 1 или 2 аргумента");
          const [x, s] = args;
          return (v) => noise1(((s ? s(v) : 0) * 7919 + v.seed) >>> 0, x(v));
        }
        const fn = FUNCS[t.v];
        if (!fn) throw new ExprError(`неизвестная функция «${t.v}» (есть: ${[...Object.keys(FUNCS), ...SEEDED].join(", ")})`);
        if (args.length < fn.n[0] || args.length > fn.n[1])
          throw new ExprError(`${t.v}: аргументов ${args.length}, нужно ${fn.n[0] === fn.n[1] ? fn.n[0] : `${fn.n[0]}..${fn.n[1]}`}`);
        const f = fn.f;
        if (args.length === 1) {
          const a = args[0];
          return (v) => f(a(v));
        }
        if (args.length === 2) {
          const [a, b] = args;
          return (v) => f(a(v), b(v));
        }
        return (v) => f(...args.map((a) => a(v)));
      }
      if (!VARS.has(t.v)) throw new ExprError(`неизвестная переменная «${t.v}» (есть: ${[...VARS].join(", ")})`);
      return variable(t.v);
    }
    throw new ExprError(`неожиданное «${t.v}» в позиции ${t.pos}`);
  }

  function unary(): Fn {
    return power();
  }

  function power(): Fn {
    const base = primary();
    if (peek()?.v === "^") {
      take("^");
      const exp = unaryOrPower();
      return (v) => Math.pow(base(v), exp(v));
    }
    return base;
  }

  // Правая ассоциативность степени и унарный минус в показателе: 2^-x.
  function unaryOrPower(): Fn {
    const t = peek();
    if (t && t.k === "op" && (t.v === "-" || t.v === "+")) {
      take();
      const e = unaryOrPower();
      return t.v === "-" ? (v) => -e(v) : e;
    }
    return power();
  }

  function binaryExpr(minPrec: number): Fn {
    let left = unary();
    for (;;) {
      const t = peek();
      if (!t || t.k !== "op") break;
      const prec = BIN_PREC[t.v];
      if (prec === undefined || prec < minPrec || t.v === "^") break;
      take();
      count();
      const right = binaryExpr(prec + 1);
      left = binary(t.v, left, right);
    }
    return left;
  }

  function ternary(): Fn {
    const cond = binaryExpr(1);
    if (peek()?.v === "?") {
      take("?");
      const a = ternary();
      take(":");
      const b = ternary();
      return (v) => (cond(v) ? a(v) : b(v));
    }
    return cond;
  }

  if (!toks.length) throw new ExprError("пустое выражение");
  const out = ternary();
  if (pos < toks.length) throw new ExprError(`лишнее «${toks[pos].v}» в позиции ${toks[pos].pos}`);
  return out;
}

const cache = new Map<string, Fn | ExprError>();

/** Скомпилировать выражение (с кэшем); ошибка — исключение с понятным текстом. */
export function compileExpr(src: string): Fn {
  let hit = cache.get(src);
  if (hit === undefined) {
    try {
      if (src.length > MAX_EXPR_LEN) throw new ExprError(`выражение длиннее ${MAX_EXPR_LEN} символов`);
      const raw = parse(src);
      hit = (v: ExprVars) => {
        const x = raw(v);
        return Number.isFinite(x) ? x : 0;
      };
    } catch (e) {
      hit = e instanceof ExprError ? e : new ExprError(String((e as Error)?.message || e));
    }
    if (cache.size > 2000) cache.clear();
    cache.set(src, hit);
  }
  if (hit instanceof ExprError) throw hit;
  return hit;
}

/** Ошибка выражения или null — для проверки сцены. */
export function exprError(src: string): string | null {
  try {
    compileExpr(src);
    return null;
  } catch (e) {
    return (e as Error).message;
  }
}

export const EXPR_FUNCS = [...Object.keys(FUNCS), ...SEEDED];
export const EXPR_VARS = [...VARS];
