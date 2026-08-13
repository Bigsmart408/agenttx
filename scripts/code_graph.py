#!/usr/bin/env python3
"""Static code-graph extractor + renderer for the agenttx package.

Builds, from the AST of every module under src/agenttx:
  * an import graph  (module -> module)
  * a call graph     (function/method -> function/method, statically resolved)
  * a class hierarchy (class -> base class, when resolvable to internal code)

Outputs:
  * codegraph.json  — full node/edge data
  * *_imports.dot / *_imports.svg  — module dependency diagram
  * *_calls.dot   / *_calls.svg    — function-level call diagram
  * *_modules.svg                  — aggregated module-to-module call diagram
  * a text summary of the structure

Pure stdlib (ast), no external dependencies. Run from the repo root:
    python3 scripts/code_graph.py [--all] [--out DIR]
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import sys
from collections import Counter, defaultdict, deque

SRC_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src")
PACKAGE = "agenttx"
SKIP_DIRS = {"__pycache__", "optimization_history"}

# populated in parse_modules() before call resolution runs
modules_by_name: dict[str, ModuleInfo] = {}


# --------------------------------------------------------------------------
# 1. AST extraction
# --------------------------------------------------------------------------

class ModuleInfo:
    def __init__(self, path: str, name: str):
        self.path = path
        self.name = name
        self.imports = {}          # local name -> (module, original name)
        self.symbols = {}          # local name -> qualname (func/class)
        self.classes = {}          # class name -> {bases, methods:{name:line}, lines}
        self.funcs = {}            # top-level func name -> line
        self.calls = []            # (caller_qualname, callee_qualname|None, raw_name, line)
        self.inherits = []         # (class_qualname, base_qualname|None, raw_base)
        self.caller_lines = {}     # caller_qualname -> line


def module_name_from_path(path: str) -> str:
    rel = os.path.relpath(path, SRC_ROOT)
    return rel[:-3].replace(os.sep, ".")


def collect_symbols(tree: ast.Module, mod: ModuleInfo) -> None:
    """First pass: index every function/class/method by its local name."""
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            q = node.name
            mod.symbols[node.name] = q
            mod.funcs[node.name] = node.lineno
            mod.caller_lines[q] = node.lineno
            _index_nested(node, q, mod)
        elif isinstance(node, ast.ClassDef):
            q = node.name
            mod.symbols[node.name] = q
            mod.classes[node.name] = {
                "bases": [ast.unparse(b) for b in node.bases],
                "methods": {},
                "lines": (node.lineno, getattr(node, "end_lineno", node.lineno)),
            }
            mod.caller_lines[q] = node.lineno
            for stmt in node.body:
                if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    mq = f"{q}.{stmt.name}"
                    mod.symbols[stmt.name] = mq
                    mod.classes[node.name]["methods"][stmt.name] = stmt.lineno
                    mod.caller_lines[mq] = stmt.lineno
                    _index_nested(stmt, mq, mod)


def _index_nested(node, prefix, mod: ModuleInfo) -> None:
    for sub in ast.walk(node):
        if sub is node:
            continue
        if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
            q = f"{prefix}.{sub.name}"
            mod.symbols[sub.name] = q
            mod.caller_lines[q] = sub.lineno
        elif isinstance(sub, ast.ClassDef):
            q = f"{prefix}.{sub.name}"
            mod.symbols[sub.name] = q
            mod.caller_lines[q] = sub.lineno


def collect_imports(tree: ast.Module, mod: ModuleInfo) -> None:
    pkg_parts = mod.name.split(".")[:-1]  # the package containing this module
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                mod.imports[a.asname or a.name.split(".")[0]] = (a.name, None)
        elif isinstance(node, ast.ImportFrom):
            # resolve relative levels against this module's own dotted path
            if node.level > 0:
                base_parts = pkg_parts[: max(0, len(pkg_parts) - (node.level - 1))]
                if node.module:
                    base_parts = base_parts + node.module.split(".")
                base = ".".join(base_parts) if base_parts else ""
            else:
                base = node.module or ""
            for a in node.names:
                if a.name == "*":
                    continue
                mod.imports[a.asname or a.name] = (base, a.name)


def local_names(func_node) -> set:
    """Names bound inside a function (defs, assigns, params, imports)."""
    names = {a.arg for a in func_node.args.args + func_node.args.kwonlyargs}
    if func_node.args.vararg:
        names.add(func_node.args.vararg.arg)
    if func_node.args.kwarg:
        names.add(func_node.args.kwarg.arg)
    for sub in ast.walk(func_node):
        if sub is func_node:
            continue
        if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(sub.name)
        elif isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Store):
            names.add(sub.id)
        elif isinstance(sub, (ast.Import, ast.ImportFrom)):
            for a in sub.names:
                names.add(a.asname or a.name.split(".")[0])
    return names


def resolve_call(mod: ModuleInfo, func, call_node: ast.Call, enclosing_class: str | None):
    """Resolve a call target to a qualname; returns (qualname|None, raw)."""
    f = call_node.func
    if isinstance(f, ast.Name):
        return _resolve_name(mod, f.id, func, enclosing_class), f.id
    if isinstance(f, ast.Attribute):
        raw = ast.unparse(f)
        if isinstance(f.value, ast.Name):
            base = f.value.id
            # self.method / cls.method
            if base in ("self", "cls") and enclosing_class:
                q = f"{enclosing_class}.{f.attr}"
                if q in mod.caller_lines:
                    return q, raw
                return None, raw
            # module.attr  (module alias or internal module name)
            if base in mod.imports:
                imod, _ = mod.imports[base]
                return _resolve_external(imod, f.attr), raw
            if base in mod.symbols:
                q = mod.symbols[base]
                if q in mod.classes or q in mod.funcs:
                    # object call, may be a class instantiation
                    return q, raw
                return None, raw
            return None, raw
        # dotted chains: walk from the leftmost name, try longest module prefix
        chain, node = [], f
        while isinstance(node, ast.Attribute):
            chain.append(node.attr)
            node = node.value
        if isinstance(node, ast.Name):
            chain.append(node.id)
            chain.reverse()
            for cut in range(1, len(chain)):
                modname = ".".join(chain[:cut])
                if modname in mod.imports:
                    imod, _ = mod.imports[modname]
                    q = _resolve_external(imod, chain[-1])
                    return q, raw
                if modname in modules_by_name:
                    return f"{modname}:{chain[cut]}", raw
        # chains like self.ledger.add(...) -> enclosing class method when it is
        # the final attr on self, else unresolved
        if isinstance(f.value, ast.Attribute) and isinstance(f.value.value, ast.Name) \
                and f.value.value.id in ("self", "cls") and enclosing_class:
            return None, raw
        return None, raw
    return None, ast.unparse(f)


def _resolve_name(mod: ModuleInfo, name, func, enclosing_class: str | None):
    local = local_names(func)
    if name in local:
        return None  # local variable / nested def, not a module-level callee
    if name in mod.symbols:
        return mod.symbols[name]
    if name in mod.imports:
        imod, _ = mod.imports[name]
        return _resolve_external(imod, name)
    return None


def _resolve_external(module: str, name: str):
    if module == PACKAGE or module.startswith(PACKAGE + "."):
        return f"{module}:{name}"
    return None  # external / stdlib: not resolvable statically


def collect_calls(mod: ModuleInfo, tree: ast.Module) -> None:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            caller = _caller_qualname(mod, node)
            for sub in ast.walk(node):
                if isinstance(sub, ast.Call):
                    q, raw = resolve_call(mod, node, sub, caller.split(".")[0] if "." in caller else None)
                    mod.calls.append((caller, q, raw, sub.lineno))


def _caller_qualname(mod: ModuleInfo, node) -> str:
    # find whether node is inside a class
    for cls_name, info in mod.classes.items():
        cl, ce = info["lines"]
        if cl <= node.lineno <= ce and node.name in info["methods"]:
            return f"{cls_name}.{node.name}"
    return node.name


def collect_inheritance(mod: ModuleInfo, tree: ast.Module) -> None:
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for b in node.bases:
                if isinstance(b, ast.Name):
                    if b.id in mod.symbols:
                        mod.inherits.append((node.name, mod.symbols[b.id], b.id))
                    elif b.id in mod.imports:
                        imod, orig = mod.imports[b.id]
                        q = _resolve_external(imod, orig or b.id)
                        mod.inherits.append((node.name, q, b.id))
                    else:
                        mod.inherits.append((node.name, None, b.id))
                else:
                    mod.inherits.append((node.name, None, ast.unparse(b)))


def parse_modules(root: str, include_history: bool) -> dict[str, ModuleInfo]:
    global modules_by_name
    modules: dict[str, ModuleInfo] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS or (include_history and d == "optimization_history")]
        for fn in sorted(filenames):
            if not fn.endswith(".py") or fn == "__main__.py":
                continue
            path = os.path.join(dirpath, fn)
            name = module_name_from_path(path)
            if not include_history and "optimization_history" in path:
                continue
            try:
                tree = ast.parse(open(path, encoding="utf-8").read(), filename=path)
            except SyntaxError as e:
                print(f"  !! parse error {path}: {e}", file=sys.stderr)
                continue
            mod = ModuleInfo(path, name)
            modules[name] = mod
    modules_by_name = modules
    for name, mod in modules.items():
        tree = ast.parse(open(mod.path, encoding="utf-8").read(), filename=mod.path)
        collect_symbols(tree, mod)
        collect_imports(tree, mod)
        collect_calls(mod, tree)
        collect_inheritance(mod, tree)
    return modules


# --------------------------------------------------------------------------
# 2. Graph assembly
# --------------------------------------------------------------------------

def build_import_graph(modules: dict[str, ModuleInfo]):
    nodes = sorted(modules)
    edges = []
    for m in modules.values():
        for local, (target, _) in m.imports.items():
            if target == PACKAGE or target.startswith(PACKAGE + "."):
                edges.append((m.name, target))
    # dedupe, drop self edges
    seen = set()
    out = []
    for u, v in edges:
        if u != v and (u, v) not in seen:
            seen.add((u, v))
            out.append((u, v))
    return nodes, out


def build_call_graph(modules: dict[str, ModuleInfo]):
    """Returns (nodes, edges, unresolved, external_calls) at qualname level."""
    nodes = set()
    edges = []
    unresolved = Counter()
    for m in modules.values():
        for caller, callee, raw, line in m.calls:
            cq = f"{m.name}:{caller}"
            nodes.add(cq)
            if callee is not None:
                # strip module prefix for cross-module edge target
                if ":" in callee and not callee.startswith(m.name + ":"):
                    tmod, tname = callee.split(":", 1)
                    if tmod in modules:
                        edges.append((cq, f"{tmod}:{tname}"))
                        continue
                edges.append((cq, callee if callee.startswith(m.name) else f"{m.name}:{callee}"))
            else:
                unresolved[(m.name, caller, raw)] += 1
    # dedupe
    seen = set()
    dedup = []
    for u, v in edges:
        if (u, v) not in seen:
            seen.add((u, v))
            dedup.append((u, v))
    return sorted(nodes), dedup, unresolved


def build_module_call_graph(call_edges):
    """Aggregate qualname edges to module -> module with counts."""
    agg = Counter()
    for u, v in call_edges:
        mu, mv = u.split(":")[0], v.split(":")[0]
        if mu != mv:
            agg[(mu, mv)] += 1
    return agg


def build_class_hierarchy(modules: dict[str, ModuleInfo]):
    nodes, edges = [], []
    for m in modules.values():
        for cls, base, raw in m.inherits:
            if base is not None:
                bmod, bname = (base.split(":", 1) + [""])[:2] if ":" in base else (m.name, base)
                if ":" in base:
                    bmod, bname = base.split(":", 1)
                if bmod in modules:
                    nodes.append(f"{m.name}:{cls}")
                    nodes.append(f"{bmod}:{bname}")
                    edges.append((f"{m.name}:{cls}", f"{bmod}:{bname}"))
    return sorted(set(nodes)), edges


# --------------------------------------------------------------------------
# 3. Layered layout + SVG rendering (pure stdlib)
# --------------------------------------------------------------------------

NODE_W = 168
NODE_H = 30
X_GAP = 26
Y_GAP = 42
FONT = "font-family='DejaVu Sans, sans-serif'"


def layered_layout(nodes, edges):
    """Longest-path layering, cycle-tolerant; returns pos: {node: (x, y, w)}."""
    succ = defaultdict(set)
    pred = defaultdict(set)
    for u, v in edges:
        if u in nodes and v in nodes:
            succ[u].add(v)
            pred[v].add(u)
    layer = {n: 0 for n in nodes}
    for _ in range(len(nodes) + 1):
        changed = False
        for u, v in edges:
            if u in layer and v in layer and layer[v] < layer[u] + 1:
                layer[v] = layer[u] + 1
                changed = True
        if not changed:
            break
    by_layer = defaultdict(list)
    for n in nodes:
        by_layer[layer[n]].append(n)
    pos = {}
    for lv, items in by_layer.items():
        items.sort()
        total_w = sum(max(NODE_W, 12 * len(i)) for i in items) + X_GAP * (len(items) - 1)
        x = -total_w / 2
        y = lv * (NODE_H + Y_GAP)
        for it in items:
            w = max(NODE_W, 12 * len(it))
            pos[it] = (x + w / 2, y, w)
            x += w + X_GAP
    return pos, layer


def svg_header(w, h):
    return (f"<svg xmlns='http://www.w3.org/2000/svg' width='{w}' height='{h}' "
            f"viewBox='{-w/2} {-20} {w} {h + 60}'>"
            f"<defs><marker id='arr' viewBox='0 0 10 10' refX='10' refY='5' "
            f"markerWidth='7' markerHeight='7' orient='auto-start-reverse'>"
            f"<path d='M 0 0 L 10 5 L 0 10 z' fill='#8a8a8a'/></marker></defs>")


def render_svg(title, nodes, edges, pos, color_for, shape_for=None, label_for=None,
               edge_labels=None):
    label_for = label_for or (lambda n: n.split(":")[-1])
    shape_for = shape_for or (lambda n: "round")
    edge_labels = edge_labels or {}
    if not pos:
        return f"<svg xmlns='http://www.w3.org/2000/svg' width='400' height='120'>" \
               f"<text x='200' y='60' text-anchor='middle' {FONT}>{title} (empty)</text></svg>"
    w = max(400, max(p[0] + p[2] / 2 for p in pos.values()) * 2 + 80)
    h = max(200, max(p[1] for p in pos.values()) + NODE_H + 80)
    parts = [svg_header(w, h)]
    parts.append(f"<text x='0' y='-6' text-anchor='middle' font-size='15' font-weight='bold' "
                 f"{FONT}>{title}</text>")
    # edges first
    for u, v in edges:
        if u not in pos or v not in pos:
            continue
        x1, y1, w1 = pos[u]
        x2, y2, w2 = pos[v]
        stroke = "#9aa0a6"
        if y2 > y1:
            d = f"M {x1} {y1 + NODE_H / 2} C {x1} {(y1 + y2) / 2}, {x2} {(y1 + y2) / 2}, {x2} {y2 - NODE_H / 2}"
        elif y2 < y1:
            d = f"M {x1} {y1 - NODE_H / 2} C {x1} {(y1 + y2) / 2}, {x2} {(y1 + y2) / 2}, {x2} {y2 + NODE_H / 2}"
        else:
            d = f"M {x1 + w1 / 2} {y1} C {x1 + w1 / 2 + 60} {y1 + 30}, {x2 + w2 / 2 + 60} {y2 + 30}, {x2 + w2 / 2} {y2}"
        parts.append(f"<path d='{d}' fill='none' stroke='{stroke}' stroke-width='1.2' marker-end='url(#arr)'/>")
        lbl = edge_labels.get((u, v))
        if lbl:
            parts.append(f"<text x='{(x1 + x2) / 2}' y='{(y1 + y2) / 2 - 4}' text-anchor='middle' "
                         f"font-size='9' fill='#555' {FONT}>{lbl}</text>")
    # nodes
    for n, (x, y, wn) in pos.items():
        kind = shape_for(n)
        if kind == "round":
            parts.append(f"<rect x='{x - wn/2}' y='{y - NODE_H/2}' width='{wn}' height='{NODE_H}' "
                         f"rx='7' fill='{color_for(n)}' stroke='#555' stroke-width='1'/>")
        else:
            parts.append(f"<rect x='{x - wn/2}' y='{y - NODE_H/2}' width='{wn}' height='{NODE_H}' "
                         f"fill='{color_for(n)}' stroke='#555' stroke-width='1'/>")
        parts.append(f"<text x='{x}' y='{y + 4}' text-anchor='middle' font-size='10.5' {FONT} "
                     f"fill='#111'>{label_for(n)}</text>")
    parts.append("</svg>")
    return "".join(parts)


def write_svg(path, svg):
    with open(path, "w", encoding="utf-8") as f:
        f.write(svg)


# --- PNG rendering (PIL) ---------------------------------------------------

def render_png(path, title, nodes, edges, pos, color_for, shape_for=None, label_for=None,
               edge_labels=None):
    """Render the same layout as render_svg into a PNG using PIL."""
    from PIL import Image, ImageDraw, ImageFont
    label_for = label_for or (lambda n: n.split(":")[-1])
    shape_for = shape_for or (lambda n: "round")
    edge_labels = edge_labels or {}
    if not pos:
        return
    w = int(max(600, max(p[0] + p[2] / 2 for p in pos.values()) * 2 + 120))
    h = int(max(240, max(p[1] for p in pos.values()) + NODE_H + 130))
    ox, oy = w / 2, 40
    img = Image.new("RGB", (w, h), "white")
    d = ImageDraw.Draw(img)
    font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 11)
    font_s = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 9)
    font_t = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 15)

    def pt(x, y):
        return (ox + x, oy + y)

    d.text(pt(0, -34), title, fill="black", font=font_t, anchor="mm")

    # edges
    for u, v in edges:
        if u not in pos or v not in pos:
            continue
        x1, y1, w1 = pos[u]
        x2, y2, w2 = pos[v]
        if y2 > y1:
            p0 = (x1, y1 + NODE_H / 2)
            p1 = (x1, (y1 + y2) / 2)
            p2 = (x2, (y1 + y2) / 2)
            p3 = (x2, y2 - NODE_H / 2)
        elif y2 < y1:
            p0 = (x1, y1 - NODE_H / 2)
            p1 = (x1, (y1 + y2) / 2)
            p2 = (x2, (y1 + y2) / 2)
            p3 = (x2, y2 + NODE_H / 2)
        else:
            p0 = (x1 + w1 / 2, y1)
            p1 = (x1 + w1 / 2 + 60, y1 + 30)
            p2 = (x2 + w2 / 2 + 60, y2 + 30)
            p3 = (x2 + w2 / 2, y2)
        pts = []
        for t in range(0, 21):
            s = t / 20.0
            mt = 1 - s
            bx = (mt ** 3) * p0[0] + 3 * (mt ** 2) * s * p1[0] + 3 * mt * (s ** 2) * p2[0] + (s ** 3) * p3[0]
            by = (mt ** 3) * p0[1] + 3 * (mt ** 2) * s * p1[1] + 3 * mt * (s ** 2) * p2[1] + (s ** 3) * p3[1]
            pts.append(pt(bx, by))
        d.line(pts, fill="#8a8a8a", width=1, joint="curve")
        # arrowhead at the end
        if len(pts) >= 2:
            ax, ay = pts[-1]
            bx, by = pts[-2]
            import math
            ang = math.atan2(ay - by, ax - bx)
            for da in (0.5, -0.5):
                ex = ax - 9 * math.cos(ang + da)
                ey = ay - 9 * math.sin(ang + da)
                d.line([(ax, ay), (ex, ey)], fill="#8a8a8a", width=1)
        lbl = edge_labels.get((u, v))
        if lbl:
            d.text(pt((x1 + x2) / 2, (y1 + y2) / 2 - 12), lbl, fill="#666666", font=font_s, anchor="mm")

    # nodes
    for n, (x, y, wn) in pos.items():
        x0, y0 = pt(x - wn / 2, y - NODE_H / 2)
        x1, y1 = pt(x + wn / 2, y + NODE_H / 2)
        if shape_for(n) == "round":
            d.rounded_rectangle([x0, y0, x1, y1], radius=7, fill=color_for(n), outline="#555555", width=1)
        else:
            d.rectangle([x0, y0, x1, y1], fill=color_for(n), outline="#555555", width=1)
        cx, cy = pt(x, y + 4)
        d.text((cx, cy), label_for(n), fill="#111111", font=font, anchor="mm")

    img.save(path)


# --------------------------------------------------------------------------
# 4. Structure summary
# --------------------------------------------------------------------------

def structure_summary(modules: dict[str, ModuleInfo]) -> str:
    lines = []
    lines.append("=" * 78)
    lines.append("AGENTTX CODE STRUCTURE (static AST code graph)")
    lines.append("=" * 78)
    for name in sorted(modules):
        m = modules[name]
        lines.append(f"\n## {name}  ({os.path.relpath(m.path, SRC_ROOT)})")
        imports = sorted(set(t for _, (t, _) in m.imports.items()))
        if imports:
            internal = [i for i in imports if i.startswith(PACKAGE)]
            external = [i for i in imports if not i.startswith(PACKAGE)]
            if internal:
                lines.append(f"  internal imports: {', '.join(internal)}")
            if external:
                lines.append(f"  external imports: {', '.join(external[:12])}"
                             + (" …" if len(external) > 12 else ""))
        if m.classes:
            for cn, info in m.classes.items():
                bases = f" ({', '.join(info['bases'])})" if info["bases"] else ""
                lines.append(f"  class {cn}{bases}")
                for mn, ln in info["methods"].items():
                    lines.append(f"    {mn}()  [line {ln}]")
        if m.funcs:
            lines.append("  functions:")
            for fn, ln in sorted(m.funcs.items(), key=lambda kv: kv[1]):
                lines.append(f"    {fn}()  [line {ln}]")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="include optimization_history snapshots")
    ap.add_argument("--out", default="codegraph", help="output directory")
    args = ap.parse_args()

    root = os.path.abspath(SRC_ROOT)
    out = os.path.abspath(args.out)
    os.makedirs(out, exist_ok=True)

    print(f"Parsing modules under {root} …")
    modules = parse_modules(root, args.all)
    print(f"  {len(modules)} modules")

    # import graph
    inodes, iedges = build_import_graph(modules)
    ipos, _ = layered_layout(inodes, iedges)
    mod_color = {}
    for n in inodes:
        if n.startswith("agenttx.agents"):
            mod_color[n] = "#c8e6c9"
        elif n == "agenttx" or n.count(".") == 1:
            mod_color[n] = "#bbdefb"
        else:
            mod_color[n] = "#fff9c4"
    import_svg = render_svg("agenttx module import graph", inodes, iedges, ipos,
                            lambda n: mod_color.get(n, "#eee"),
                            label_for=lambda n: n.replace("agenttx.", ""))
    write_svg(os.path.join(out, "imports.svg"), import_svg)
    render_png(os.path.join(out, "imports.png"), "agenttx module import graph", inodes, iedges, ipos,
               lambda n: mod_color.get(n, "#eee"),
               label_for=lambda n: n.replace("agenttx.", ""))

    # call graph
    cnodes, cedges, unresolved = build_call_graph(modules)
    cpos, _ = layered_layout(cnodes, cedges)
    mod_of = {n: n.split(":")[0] for n in cnodes}
    palettes = {"agenttx": "#bbdefb", "agenttx.agents": "#c8e6c9", "agenttx.optimization_history": "#f8bbd0"}
    call_color = lambda n: palettes.get(mod_of[n], "#fff9c4") if mod_of[n] in palettes else "#e0e0e0"
    shape = lambda n: "round" if "." in n.split(":", 1)[1] else "box"
    call_svg = render_svg(f"agenttx call graph ({len(cnodes)} nodes, {len(cedges)} edges)",
                          cnodes, cedges, cpos, call_color, shape_for=shape)
    write_svg(os.path.join(out, "calls.svg"), call_svg)
    render_png(os.path.join(out, "calls.png"),
               f"agenttx call graph ({len(cnodes)} nodes, {len(cedges)} edges)",
               cnodes, cedges, cpos, call_color, shape_for=shape)

    # aggregated module-level call graph
    magg = build_module_call_graph(cedges)
    mnodes = sorted({m for u, v in magg for m in (u, v)})
    medges = [(u, v) for (u, v) in magg]
    mpos, _ = layered_layout(mnodes, medges)
    msizes = {m: sum(magg[(u, v)] for u, v in magg if u == m or v == m) for m in mnodes}
    elabels = {(u, v): str(c) for (u, v), c in magg.items()}
    mod_svg = render_svg("agenttx module-level call aggregation (edge label = # calls)",
                         mnodes, medges, mpos,
                         lambda n: palettes.get(n, "#fff9c4"),
                         label_for=lambda n: n.replace("agenttx.", "") + f" ({msizes[n]})",
                         edge_labels=elabels)
    with open(os.path.join(out, "modules.svg"), "w", encoding="utf-8") as f:
        f.write(mod_svg)
    render_png(os.path.join(out, "modules.png"),
               "agenttx module-level call aggregation (edge label = # calls)",
               mnodes, medges, mpos,
               lambda n: palettes.get(n, "#fff9c4"),
               label_for=lambda n: n.replace("agenttx.", "") + f" ({msizes[n]})",
               edge_labels=elabels)

    # DOT outputs
    def to_dot(name, nodes, edges, directed=True):
        s = [f"digraph {name} {{", "  rankdir=LR;", "  node [shape=box, fontsize=10];"]
        for n in nodes:
            s.append(f'  "{n}" [label="{n.replace(chr(34), "")}"];')
        for u, v in edges:
            s.append(f'  "{u}" -> "{v}";')
        s.append("}")
        return "\n".join(s)

    with open(os.path.join(out, "imports.dot"), "w") as f:
        f.write(to_dot("imports", inodes, iedges))
    with open(os.path.join(out, "calls.dot"), "w") as f:
        f.write(to_dot("calls", cnodes, cedges))

    # JSON data
    data = {
        "modules": {n: {"path": m.path, "classes": {
            c: {"bases": i["bases"], "methods": i["methods"]} for c, i in m.classes.items()},
            "functions": m.funcs} for n, m in modules.items()},
        "import_graph": {"nodes": inodes, "edges": iedges},
        "call_graph": {"nodes": cnodes, "edges": cedges},
        "module_call_aggregate": [{"from": u, "to": v, "count": c} for (u, v), c in sorted(magg.items())],
        "class_hierarchy": {"nodes": [], "edges": []},
        "unresolved_calls": [{"caller": f"{m}:{fn}", "target": raw, "count": c}
                             for (m, fn, raw), c in unresolved.most_common(60)],
    }
    hn, hedge = build_class_hierarchy(modules)
    data["class_hierarchy"]["nodes"] = hn
    data["class_hierarchy"]["edges"] = hedge
    with open(os.path.join(out, "codegraph.json"), "w") as f:
        json.dump(data, f, indent=1, sort_keys=True)

    # summary text
    with open(os.path.join(out, "structure.txt"), "w") as f:
        f.write(structure_summary(modules))

    # console stats
    print(f"\nImport graph: {len(inodes)} modules, {len(iedges)} edges")
    print(f"Call graph:   {len(cnodes)} nodes, {len(cedges)} edges")
    print(f"Unresolved call sites: {sum(unresolved.values())}")
    top = Counter()
    for u, v in cedges:
        top[u.split(':')[0]] += 1
    print("\nMost cross-module call edges by source module:")
    for m, c in top.most_common(10):
        print(f"  {m:32s} {c}")
    hot = Counter()
    for u, v in cedges:
        hot[v] += 1
    print("\nMost-called functions (fan-in):")
    for q, c in hot.most_common(12):
        print(f"  {c:3d}  {q}")
    print(f"\nOutput written to {out}/")

    # write a labeled module graph with edge counts via direct SVG post-edit is
    # complex; instead we embed counts into the DOT for modules
    with open(os.path.join(out, "modules.dot"), "w") as f:
        s = ["digraph modules {", "  rankdir=LR;", "  node [shape=box, fontsize=10];"]
        for n in mnodes:
            s.append(f'  "{n}" [label="{n.replace("agenttx.", "")}"];')
        for (u, v), c in sorted(magg.items()):
            s.append(f'  "{u}" -> "{v}" [label="{c}"];')
        s.append("}")
        f.write("\n".join(s))


if __name__ == "__main__":
    main()
