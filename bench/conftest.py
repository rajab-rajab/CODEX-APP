"""The benchmark's tasks hold tests that are meant to fail until Codex does the task (their starting projects
and hidden tests). They run inside each task's own copy, never as part of the app's test suite, which
checks them through tests/test_bench.py instead."""

collect_ignore_glob = ["tasks/*"]
