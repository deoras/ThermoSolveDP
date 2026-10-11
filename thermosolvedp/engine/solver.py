"""Sequential-modular steady-state solver with automatic tear-stream selection.

1. Check that every required inlet is connected.
2. Choose tear streams so that the remaining block graph is acyclic
   (user-marked tears first, then streams with an estimate, then streams
   entering a mixer).
3. Calculate the blocks in topological order and update the tear streams by
   direct substitution or Wegstein acceleration until they converge.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from .flowsheet import Flowsheet, Stream
from .material import Material
from .params import Result
from .thermo import (METHODS, Fluid, ThermoError, flash, for_package, formation_enthalpy_flow, kij_for, pair_key,
                     set_property_package)
from .unitops import Feed, Mixer, Product, Reactor, UnitOpError


@dataclass
class SolveReport:
    converged: bool = False
    iterations: int = 0
    tears: list[str] = field(default_factory=list)
    messages: list[tuple[str, str]] = field(default_factory=list)   # (level, text)
    error: Optional[str] = None
    error_block: Optional[str] = None
    summary: list[Result] = field(default_factory=list)
    elapsed: float = 0.0
    cancelled: bool = False

    def info(self, t):
        self.messages.append(("info", t))

    def warn(self, t):
        self.messages.append(("warning", t))

    def fail(self, t, block=None):
        self.messages.append(("error", t))
        self.error = t
        self.error_block = block


# ----------------------------------------------------------------------------------
# graph helpers
# ----------------------------------------------------------------------------------

def _sccs(nodes: list[str], edges: list[Stream]) -> list[set[str]]:
    """Tarjan's strongly connected components (iterative)."""
    adj: dict[str, list[str]] = {n: [] for n in nodes}
    for e in edges:
        adj[e.src].append(e.dst)
    index, low, on, stack, out = {}, {}, set(), [], []
    counter = [0]
    for root in nodes:
        if root in index:
            continue
        work = [(root, iter(adj[root]))]
        index[root] = low[root] = counter[0]; counter[0] += 1
        stack.append(root); on.add(root)
        while work:
            v, it = work[-1]
            advanced = False
            for w in it:
                if w not in index:
                    index[w] = low[w] = counter[0]; counter[0] += 1
                    stack.append(w); on.add(w)
                    work.append((w, iter(adj[w])))
                    advanced = True
                    break
                elif w in on:
                    low[v] = min(low[v], index[w])
            if advanced:
                continue
            work.pop()
            if work:
                low[work[-1][0]] = min(low[work[-1][0]], low[v])
            if low[v] == index[v]:
                comp = set()
                while True:
                    w = stack.pop(); on.discard(w); comp.add(w)
                    if w == v:
                        break
                out.append(comp)
    return out


def select_tears(fs: Flowsheet) -> list[Stream]:
    tears = {s.uid for s in fs.streams.values() if s.tear}
    order = list(fs.streams)
    for _ in range(len(fs.streams) + 1):
        edges = [s for s in fs.streams.values() if s.uid not in tears]
        comps = _sccs(list(fs.blocks), edges)
        cyclic = [c for c in comps if len(c) > 1 or any(e.src == e.dst and e.src in c for e in edges)]
        if not cyclic:
            break
        for comp in cyclic:
            cand = [e for e in edges if e.src in comp and e.dst in comp]
            cand.sort(key=lambda e: (not e.estimate.get("enabled"), not isinstance(fs.blocks[e.dst], Mixer),
                                     order.index(e.uid)))
            tears.add(cand[0].uid)
    return [fs.streams[t] for t in order if t in tears]


def topo_order(fs: Flowsheet, tears: set[str]) -> list[str]:
    indeg = {b: 0 for b in fs.blocks}
    for s in fs.streams.values():
        if s.uid not in tears:
            indeg[s.dst] += 1
    ready = [b for b in fs.blocks if indeg[b] == 0]
    order = []
    while ready:
        b = ready.pop(0)
        order.append(b)
        for s in fs.streams.values():
            if s.src == b and s.uid not in tears:
                indeg[s.dst] -= 1
                if indeg[s.dst] == 0:
                    ready.append(s.dst)
    if len(order) != len(fs.blocks):
        raise RuntimeError("Internal error: flowsheet is still cyclic after tearing.")
    return order


def _upstream_feed(fs: Flowsheet, stream: Stream) -> Optional[Feed]:
    seen, queue = set(), [stream.src]
    while queue:
        b = queue.pop(0)
        if b in seen:
            continue
        seen.add(b)
        blk = fs.blocks[b]
        if isinstance(blk, Feed):
            return blk
        queue.extend(s.src for s in fs.streams.values() if s.dst == b)
    feeds = [b for b in fs.blocks.values() if isinstance(b, Feed)]
    return feeds[0] if feeds else None


# ----------------------------------------------------------------------------------
# solver
# ----------------------------------------------------------------------------------

def _validate(fs: Flowsheet, rep: SolveReport) -> bool:
    if not fs.blocks:
        rep.fail("The flowsheet is empty. Drag blocks from the palette onto the canvas.")
        return False
    ok = True
    for b in fs.blocks.values():
        optional = b.optional_inlets()
        connected = [p for p, _ in b.inlet_ports() if fs.stream_to(b.uid, p)]
        for p, label in b.inlet_ports():
            if p not in optional and not fs.stream_to(b.uid, p):
                rep.fail(f"{b.name}: {label.lower()} is not connected.", b.uid)
                ok = False
        if b.inlet_ports() and optional and not connected:
            rep.fail(f"{b.name}: no inlet is connected.", b.uid)
            ok = False
        for p, label in b.outlet_ports():
            if not fs.stream_from(b.uid, p):
                rep.warn(f"{b.name}: {label.lower()} is not connected (its flow leaves the flowsheet).")
    return ok


def _diff(a: Material, b: Material) -> float:
    em = abs(a.m - b.m) / max(abs(a.m), abs(b.m), 1e-9)
    eh = abs(a.h - b.h) / max(abs(a.h), abs(b.h), 1e5)
    ep = abs(a.P - b.P) / max(a.P, b.P)
    ex = 0.0
    if a.fluid != b.fluid:
        xa = dict(zip(a.fluid.components, a.fluid.x))
        xb = dict(zip(b.fluid.components, b.fluid.x))
        ex = max(abs(xa.get(c, 0.0) - xb.get(c, 0.0)) for c in set(xa) | set(xb))
    return max(em, eh, ep, ex)


class _PassFailed(Exception):
    def __init__(self, block, msg):
        super().__init__(msg)
        self.block, self.msg = block, msg


class _Wegstein:
    # q < 0 accelerates, 0 < q < 1 damps. The usual bound q <= 0 cannot damp the
    # oscillating loops that heat-exchanger recycles produce, so damping is allowed.
    QMIN, QMAX = -5.0, 0.5

    def __init__(self):
        self.prev: dict[str, tuple[list[float], list[float]]] = {}

    def update(self, key: str, x: list[float], g: list[float]) -> list[float]:
        prev = self.prev.get(key)
        self.prev[key] = (x, g)
        if prev is None:
            return list(g)
        xo, go = prev
        new = []
        for xi, gi, xoi, goi in zip(x, g, xo, go):
            dx = xi - xoi
            if abs(dx) < 1e-14 * max(1.0, abs(xi)):
                new.append(gi)
                continue
            s = (gi - goi) / dx
            q = self.QMIN if abs(s - 1.0) < 1e-12 else s / (s - 1.0)
            q = min(max(q, self.QMIN), self.QMAX)
            new.append(q * xi + (1.0 - q) * gi)
        return new


class _Broyden:
    """Quasi-Newton (Broyden 'good' update of the inverse Jacobian) on all tear variables at once.

    Solves F(x) = g(x) - x = 0. The initial inverse Jacobian -I makes the first
    step a direct substitution; later steps capture the coupling between tears.
    """

    def __init__(self, n: int):
        import numpy as np
        self.np = np
        self.H = -np.eye(n)
        self.x_old = self.f_old = None

    def reset(self):
        self.H = -self.np.eye(len(self.H))
        self.x_old = self.f_old = None

    def step(self, x, g):
        np = self.np
        x, g = np.asarray(x, float), np.asarray(g, float)
        f = g - x
        if self.x_old is not None:
            dx, df = x - self.x_old, f - self.f_old
            Hdf = self.H @ df
            denom = dx @ Hdf
            if abs(denom) > 1e-14 * (np.linalg.norm(dx) * np.linalg.norm(Hdf) + 1e-300):
                self.H += np.outer(dx - Hdf, dx @ self.H) / denom
        self.x_old, self.f_old = x, f
        step = -self.H @ f
        # cap very large steps; infeasible steps are handled by back-off in solve()
        lim = 50.0 * np.linalg.norm(f)
        n = np.linalg.norm(step)
        if n > lim > 0:
            step *= lim / n
        return x + step


SOLVER_NAME = "Thermosolve1.0"


def solve(fs: Flowsheet, log: Optional[Callable[[str], None]] = None,
          cancel: Optional[threading.Event] = None) -> SolveReport:
    t0 = time.perf_counter()
    rep = SolveReport()
    log = log or (lambda _t: None)
    fs.clear_results()
    try:
        pkg = set_property_package(fs.settings.get("property_method", "HEOS"), fs.settings.get("kij", {}))
    except ThermoError as exc:
        rep.fail(str(exc))
        return rep
    if pkg.cubic:
        rep.info(f"Property method: {METHODS[pkg.method]}.")

    if not _validate(fs, rep):
        rep.elapsed = time.perf_counter() - t0
        return rep

    tears = select_tears(fs)
    tear_ids = {t.uid for t in tears}
    rep.tears = [t.name for t in tears]
    order = topo_order(fs, tear_ids)
    if tears:
        rep.info("Recycle loop(s) detected. Tear stream(s): " + ", ".join(rep.tears))

    # -- initial guesses for tear streams ------------------------------------------
    guess: dict[str, Material] = {}
    for t in tears:
        est = t.estimate
        try:
            if est.get("enabled"):
                fluid_spec = est.get("fluid") or ""
                if not fluid_spec:
                    feed = _upstream_feed(fs, t)
                    if feed is None:
                        raise UnitOpError("set the estimate's fluid")
                    fluid_spec = feed.values["fluid"]
                guess[t.uid] = Material.from_PT(for_package(Fluid.parse(fluid_spec)), float(est["m"]),
                                                float(est["P"]), float(est["T"]))
            else:
                feed = _upstream_feed(fs, t)
                if feed is None:
                    rep.fail(f"Stream {t.name} closes a loop that has no feed. Give it an initial estimate "
                             "(select the stream and tick 'Use estimate').")
                    rep.elapsed = time.perf_counter() - t0
                    return rep
                fm = feed.calculate({})["out"]
                m0 = 0.0 if isinstance(fs.blocks[t.dst], Mixer) else fm.m
                guess[t.uid] = Material(m0, fm.state)
                rep.info(f"No estimate on tear stream {t.name}: starting from {feed.name} conditions"
                         f"{' with zero flow' if m0 == 0 else ''}.")
        except (ThermoError, UnitOpError, ValueError) as exc:
            rep.fail(f"Could not build the initial estimate for stream {t.name}: {exc}")
            rep.elapsed = time.perf_counter() - t0
            return rep

    method = fs.settings.get("method", "Broyden")
    tol = float(fs.settings.get("tol", 1e-6))
    max_iter = int(fs.settings.get("max_iter", 200)) if tears else 1
    tl = [t.uid for t in tears]
    accel = _Wegstein()
    broyden = _Broyden(3 * len(tl)) if tl else None
    # common scales for the accelerator variables (mass flow, enthalpy, pressure)
    feed_flow = sum(float(b.values["m"]) for b in fs.blocks.values() if isinstance(b, Feed))
    m_sc = max([abs(guess[t].m) for t in tl] + [feed_flow, 1e-6])
    scale = {t: (m_sc, max(abs(guess[t].h), 1e5), max(guess[t].P, 1e3)) for t in tl}

    def run_pass(trial: dict[str, Material]) -> dict[str, Material]:
        """Calculate every block once. Raises _PassFailed on a block error."""
        computed: dict[str, Material] = {}
        for bid in order:
            blk = fs.blocks[bid]
            inlets = {}
            for p, _ in blk.inlet_ports():
                s = fs.stream_to(bid, p)
                if s is not None:
                    inlets[p] = trial[s.uid] if s.uid in tear_ids else s.material
            try:
                outs = blk.run(inlets)
            except UnitOpError as exc:
                raise _PassFailed(blk, str(exc))
            except Exception as exc:  # unexpected - report rather than crash the GUI
                blk.status, blk.error = "error", str(exc)
                raise _PassFailed(blk, f"unexpected error: {exc!r}")
            for p, mat in outs.items():
                s = fs.stream_from(bid, p)
                if s is None:
                    blk.dangling[p] = mat
                elif s.uid in tear_ids:
                    computed[s.uid] = mat
                else:
                    s.material = mat
        return computed

    def blend(a: Material, b: Material, w: float) -> Material:
        """State a fraction w of the way from a to b (in m, h, P)."""
        return Material.from_PH(b.fluid, a.m + w * (b.m - a.m), a.P + w * (b.P - a.P), a.h + w * (b.h - a.h))

    last_good: Optional[tuple[dict, dict]] = None   # (guess, computed) of the last successful pass
    computed: dict[str, Material] = {}
    retries = 0
    for it in range(1, max_iter + 1):
        if cancel is not None and cancel.is_set():
            rep.cancelled = True
            rep.fail("Solve cancelled.")
            break
        rep.iterations = it
        try:
            computed = run_pass(guess)
        except _PassFailed as pf:
            # An accelerated step can land somewhere infeasible (e.g. a temperature cross).
            # Back off towards the last good point before giving up.
            if last_good is not None and retries < 6:
                retries += 1
                xg, gg = last_good
                w = 0.5 ** retries
                try:
                    guess = {t: blend(xg[t], gg[t], w) for t in tl}
                except ThermoError:
                    guess = dict(xg)
                accel = _Wegstein()
                if broyden is not None:
                    broyden.reset()
                log(f"Iteration {it}: {pf.block.name} failed ({pf.msg}); taking a smaller step.")
                continue
            rep.fail(f"{pf.block.name}: {pf.msg}", pf.block.uid)
            if tears and it > 1:
                rep.info(f"(failed during recycle iteration {it})")
            no_est = [fs.streams[t].name for t in tl if not fs.streams[t].estimate.get("enabled")]
            if no_est:
                rep.info("Tip: this may be caused by the starting guess for tear stream(s) "
                         f"{', '.join(no_est)}. Select the stream and give it an estimate close to the expected "
                         "conditions.")
            rep.elapsed = time.perf_counter() - t0
            return rep
        retries = 0
        last_good = (dict(guess), dict(computed))

        if not tears:
            rep.converged = True
            break

        err = max(_diff(guess[t], computed[t]) for t in tl)
        log(f"Iteration {it}: max relative change in tear streams = {err:.3e}")
        if err <= tol:
            for t in tl:
                fs.streams[t].material = computed[t]
            rep.converged = True
            break

        if method == "Broyden":
            xs, gs = [], []
            for t in tl:
                g, c = guess[t], computed[t]
                sc = scale[t]
                xs += [g.m / sc[0], g.h / sc[1], g.P / sc[2]]
                gs += [c.m / sc[0], c.h / sc[1], c.P / sc[2]]
            xn = broyden.step(xs, gs)
            new_guess = {}
            try:
                for k, t in enumerate(tl):
                    sc = scale[t]
                    m, h, P = xn[3 * k] * sc[0], xn[3 * k + 1] * sc[1], xn[3 * k + 2] * sc[2]
                    if m < 0 or P <= 0:
                        raise ThermoError("out of range")
                    new_guess[t] = Material.from_PH(computed[t].fluid, m, P, h)
                guess = new_guess
            except ThermoError:  # fall back to direct substitution and restart the Jacobian estimate
                broyden.reset()
                guess = dict(computed)
            continue

        for t in tl:
            g, c = guess[t], computed[t]
            if method == "Wegstein" and it >= 2:
                xm, xh, xp = accel.update(t, [g.m, g.h, g.P], [c.m, c.h, c.P])
                try:
                    guess[t] = Material.from_PH(c.fluid, max(xm, 0.0), max(xp, 1.0), xh)
                except ThermoError:
                    guess[t] = c
            else:
                if method == "Wegstein":
                    accel.update(t, [g.m, g.h, g.P], [c.m, c.h, c.P])
                guess[t] = c

    if tears and not rep.converged and not rep.error:
        for t in tl:
            if t in computed:
                fs.streams[t].material = computed[t]
        rep.fail(f"Recycle did not converge in {max_iter} iterations. Try an estimate closer to the "
                 "answer, more iterations, or a different tear stream or method (Simulation ▸ Settings).")

    if rep.converged:
        if tears:
            rep.info(f"{SOLVER_NAME} converged the recycle loop(s) in {rep.iterations} iterations.")
        for b in fs.blocks.values():
            for w in b.warnings:
                rep.warn(f"{b.name}: {w}")
        if pkg.cubic:
            est = estimated_pairs(fs)
            if est:
                rep.warn(f"k_ij = 0 was assumed for {len(est)} component pair(s) with no data: "
                         + ", ".join(p.replace("|", "/") for p in est)
                         + ". Phase equilibrium for these pairs is a rough estimate; enter literature values "
                           "under Simulation ▸ Fluid package.")
        rep.summary = summarize(fs)
        fs.solved = True
    rep.elapsed = time.perf_counter() - t0
    return rep


def summarize(fs: Flowsheet) -> list[Result]:
    W_in = W_out = Q_in = Q_out = 0.0
    for b in fs.blocks.values():
        w, q = b.energy["work"], b.energy["heat"]
        if w > 0:
            W_in += w
        else:
            W_out -= w
        if q > 0:
            Q_in += q
        else:
            Q_out -= q
    m_in = H_in = m_out = H_out = 0.0
    # If pseudo-pure fluids (e.g. Air) were mixed into real-component mixtures, evaluate their
    # boundary enthalpies on the component basis so feeds and products share one reference state.
    rebase = any(st.material is not None and not st.material.fluid.is_pure for st in fs.streams.values())

    reactors = [b for b in fs.blocks.values() if isinstance(b, Reactor)]

    def H(mat):
        if reactors:   # reactions change composition: compare enthalpies on the formation basis
            try:
                return formation_enthalpy_flow(mat)
            except ThermoError:
                pass
        f = mat.fluid.expanded()
        if rebase and f != mat.fluid:
            try:
                return mat.m * flash(f, "PT", mat.P, mat.T).h
            except ThermoError:
                pass
        return mat.H

    for b in fs.blocks.values():
        if isinstance(b, Feed):
            s = fs.stream_from(b.uid, "out")
            if s and s.material:
                m_in += s.material.m
                H_in += H(s.material)
        if isinstance(b, Product):
            s = fs.stream_to(b.uid, "in")
            if s and s.material:
                m_out += s.material.m
                H_out += H(s.material)
        # unconnected outlets also leave the flowsheet
        for mat in b.dangling.values():
            m_out += mat.m
            H_out += H(mat)
    net = W_out - W_in
    res = [
        Result("Work input (compressors, pumps)", W_in, "power"),
        Result("Work output (expanders)", W_out, "power"),
        Result("Net work output", net, "power"),
        Result("Heat added (heaters, flashes)", Q_in, "power"),
        Result("Heat removed (coolers, flashes)", Q_out, "power"),
    ]
    fuel = sum(getattr(r, "reaction_heat", 0.0) for r in reactors)
    if fuel > 0:
        res.append(Result("Heat released by reactions (fuel LHV input)", fuel, "power"))
        if net > 0:
            res.append(Result("Thermal efficiency (net work / fuel LHV)", net / fuel, "frac"))
            res.append(Result("Heat rate [kJ/kWh]", 3600.0 * fuel / net, None))
    if net > 0 and Q_in > 0:
        res.append(Result("Thermal efficiency (net work / heat added)", net / Q_in, "frac"))
    if net < 0 and not any(isinstance(b, Feed) for b in fs.blocks.values()):  # closed cycle
        if Q_in > 0:
            res.append(Result("Cooling COP (heat added / net work input)", Q_in / -net, "frac"))
        if Q_out > 0:
            res.append(Result("Heating COP (heat removed / net work input)", Q_out / -net, "frac"))
    if m_in > 0 or m_out > 0:
        res.append(Result("Total feed flow", m_in, "m"))
        res.append(Result("Total product flow", m_out, "m"))
        if net < 0 and m_out > 0:
            res.append(Result("Net work input per kg of product", -net / m_out, "h"))
        res.append(Result("Mass balance error", (m_in - m_out) / max(m_in, m_out, 1e-12), "frac"))
        e_scale = max(abs(H_in), abs(H_out), W_in, W_out, Q_in, Q_out, 1.0)
        res.append(Result("Energy balance error", (H_in + W_in - W_out + Q_in - Q_out - H_out) / e_scale, "frac"))
    return res


def flowsheet_components(fs: Flowsheet) -> list[str]:
    """All components that appear in feeds, estimates or calculated streams of a flowsheet."""
    comps: set[str] = set()
    for b in fs.blocks.values():
        if isinstance(b, Feed):
            try:
                comps.update(for_package(Fluid.parse(b.values["fluid"])).components)
            except ThermoError:
                pass
    for s in fs.streams.values():
        if s.estimate.get("enabled") and s.estimate.get("fluid"):
            try:
                comps.update(for_package(Fluid.parse(s.estimate["fluid"])).components)
            except ThermoError:
                pass
        if s.material is not None:
            comps.update(s.material.fluid.components)
    return sorted(comps)


def estimated_pairs(fs: Flowsheet) -> list[str]:
    """Component pairs (in mixtures actually present) whose cubic k_ij had to be assumed."""
    pairs = set()
    for s in fs.streams.values():
        if s.material is None or s.material.fluid.is_pure:
            continue
        c = s.material.fluid.components
        for i in range(len(c)):
            for j in range(i + 1, len(c)):
                pairs.add(pair_key(c[i], c[j]))
    return sorted(p for p in pairs if kij_for(*p.split("|"))[1] == "estimated")
