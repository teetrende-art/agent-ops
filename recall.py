#!/usr/bin/env python3
"""recall — pull only the memory passages relevant to the task in hand.

An agent's memory (daily logs + MEMORY.md + working docs) can be 100k+ tokens,
far more than its context window. Reading it wholesale is impossible and wasteful;
this returns the handful of passages that actually match the task.

Ranking: BM25 (keyword/IDF). Chosen after measuring: embedding 790 passages on
this box's CPU took over eight minutes, while BM25 indexes the same corpus in
under a second and needs no model. For agent memory — file names, product names,
error strings, decisions — keyword ranking is strong, and it is free.

Usage:
    recall.py --agent robben "what did we decide about the Drop 01 hero"
    recall.py --agent robben --top 8 "bybit api key"
    recall.py --agent robben --files          # what would be searched
"""
import argparse, math, os, re, sys, time
from collections import Counter

ROOT = "/root/.openclaw"
CHUNK_CHARS = 700
SKIP_DIRS = {"skills", "tmp", "node_modules", "sessions", ".git", ".claude", ".agents",
             "__pycache__", "media"}

STOP = set("""a an and are as at be but by for from has have how i if in into is it its of on or
that the their them then there these they this to was we were what when where which who why will
with you your do does did not no can could should would""".split())


ALIAS = {"main_bot": "main", "teacher_sha": "teacher-sha", "frontend_designer": "frontend-designer"}


def workspace(agent):
    base = ALIAS.get(agent, agent)
    for cand in (f"workspace-{base}", f"workspace-{agent}", f"workspace-{agent.replace('_', '-')}"):
        p = os.path.join(ROOT, cand)
        if os.path.isdir(p):
            return p
    raise SystemExit(f"no workspace for agent {agent!r}")


def md_files(ws):
    out = []
    for root, dirs, files in os.walk(ws):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for f in files:
            if f.endswith(".md") and not f.startswith("."):
                out.append(os.path.join(root, f))
    return sorted(out)


def tokenize(text):
    return [t for t in re.findall(r"[a-z0-9_\-\.]+", text.lower()) if t not in STOP and len(t) > 1]


def chunk_file(path, ws):
    """Split a markdown file into passages, keeping the starting line number."""
    try:
        text = open(path, encoding="utf-8", errors="ignore").read()
    except Exception:
        return []
    rel = os.path.relpath(path, ws)
    chunks, buf, buf_line, line_no = [], [], 1, 1
    for raw in text.split("\n"):
        if not buf:
            buf_line = line_no
        buf.append(raw)
        if sum(len(x) + 1 for x in buf) >= CHUNK_CHARS:
            body = "\n".join(buf).strip()
            if len(body) > 60:
                chunks.append({"file": rel, "line": buf_line, "text": body})
            buf = []
        line_no += 1
    if buf:
        body = "\n".join(buf).strip()
        if len(body) > 60:
            chunks.append({"file": rel, "line": buf_line, "text": body})
    return chunks


class BM25:
    K1, B = 1.4, 0.72

    def __init__(self, docs):
        self.docs = docs
        self.toks = [Counter(tokenize(d["text"] + " " + d["file"])) for d in docs]
        self.lens = [sum(t.values()) for t in self.toks]
        self.avg = (sum(self.lens) / len(self.lens)) if self.lens else 0
        df = Counter()
        for t in self.toks:
            for w in t:
                df[w] += 1
        n = len(docs)
        self.idf = {w: math.log(1 + (n - c + 0.5) / (c + 0.5)) for w, c in df.items()}

    def search(self, query, k=5):
        q = tokenize(query)
        scored = []
        for i, t in enumerate(self.toks):
            s = 0.0
            for w in q:
                if w not in t:
                    continue
                f = t[w]
                dl = self.lens[i] or 1
                s += self.idf.get(w, 0) * (f * (self.K1 + 1)) / (
                    f + self.K1 * (1 - self.B + self.B * dl / (self.avg or 1)))
            if s > 0:
                scored.append((s, self.docs[i]))
        scored.sort(key=lambda x: -x[0])
        return scored[:k]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("query", nargs="*")
    ap.add_argument("--agent", default="robben")
    ap.add_argument("--top", type=int, default=5)
    ap.add_argument("--chars", type=int, default=300)
    ap.add_argument("--files", action="store_true")
    a = ap.parse_args()

    ws = workspace(a.agent)
    files = md_files(ws)

    if a.files:
        total = sum(os.path.getsize(f) for f in files)
        print(f"{a.agent}: {len(files)} files, {total/1024:.0f} KB (~{total//4:,} tokens)")
        for f in files:
            print(f"  {os.path.getsize(f)/1024:7.1f} KB  {os.path.relpath(f, ws)}")
        return

    q = " ".join(a.query).strip()
    if not q:
        raise SystemExit("give me a query")

    t0 = time.time()
    docs = []
    for f in files:
        docs.extend(chunk_file(f, ws))
    idx = BM25(docs)
    hits = idx.search(q, a.top)
    build_ms = (time.time() - t0) * 1000

    print(f"# recall: {q}")
    print(f"# agent: {a.agent} · {len(docs)} passages from {len(files)} files "
          f"({sum(os.path.getsize(f) for f in files)//4:,} tokens) · searched in {build_ms:.0f} ms\n")
    if not hits:
        print("no matching passages — try different words")
        return
    shown_chars = 0
    for score, d in hits:
        body = re.sub(r"\s+", " ", d["text"]).strip()[: a.chars]
        shown_chars += len(body)
        print(f"[{score:.1f}] {d['file']}:{d['line']}")
        print(f"    {body}\n")
    print(f"({len(hits)} passages · ~{shown_chars//4} tokens returned)")


if __name__ == "__main__":
    main()
