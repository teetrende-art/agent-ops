# Agent operations

Notes and code from running a small fleet of AI agents as an actual operation rather than a demo:
10 agents, 5 long-running services, a verification gate, spend limits and a live dashboard.

## What the discipline looks like

**Every agent gets a written brief.** Who it is, what it may decide, what it must escalate, and
what "done" means for its task. An agent without a brief invents one.

**Handoffs are explicit.** The output of one role is the input of the next, and it is a file, not a
memory of a conversation. If a handoff cannot be pointed at, it did not happen.

**Verification runs before delivery.** A 40-check gate inspects the artifact — file exists, size,
hash, the strings only a real output would contain. The rule: trust the check, not the agent's
report of its own success.

**Memory is retrieval, not recitation.** Loading every note into context is how a $20 balance
disappears. `recall.py` ranks passages by keyword relevance and returns a few hundred tokens
instead of a hundred thousand:

```
136,671 tokens of notes  →  ~375 tokens returned, in 112 ms
```

Measured against embeddings on the same box: 790 passages took over eight minutes on CPU and never
finished. Keyword ranking was 4,000× faster and hit the same passages. The fix was discipline, not
a bigger model.

**Spending has a ceiling per day, not per mood.** `spend_watch.py` compares recorded spend against
per-agent and fleet daily limits, and alerts when a floor is crossed. It reports; it does not
silently throttle, because a stopped fleet that nobody knows about is worse than an expensive one.

**Context is capped, not just budgeted.** Trimming per-turn context from 195k to 25k tokens cut the
cost of a turn 7.7× with no drop in output quality. Compaction is a feature.

## Files

| File | Role |
|---|---|
| `recall.py` | keyword (BM25-style) memory retrieval over agent notes |
| `spend_watch.py` | daily spend limits per agent and for the fleet, with alerts |

Both are standalone and read plain files, so they are easy to lift into another setup.
