# Contributing

The eval suites are the contract. Any change must keep them green:

```bash
for p in project1-rag-failure-analysis project2-agent-loop-design project3-data-pipeline; do
  (cd $p && python -m evals.run_eval && pytest evals/ -q) || exit 1
done
python -m ruff check .
```

Ground rules: new behavior needs a new eval case; a fixed bug needs a
regression gate; lint suppressions need a written justification on the line.
