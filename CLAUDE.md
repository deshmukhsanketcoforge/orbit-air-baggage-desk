# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Python 3.12+ CLI backed by SQLite. Run with `python main.py [--now ISO] <command>`.

## Commands
- Run all tests: `pytest`
- Run one test file: `pytest tests/test_claims.py`
- Lint: `ruff check`

## Rules the code cannot tell you
- Tests are the spec. Never edit a test file to make it pass; stop and ask instead.
- Deadline hours from the filed time: gold 4, silver 24, standard 72.
- Overdue means now is strictly later than the deadline. At exactly the deadline it is not overdue.
- Rule code never reads the clock. The current time is always passed in as a parameter.
- A claim change and its history entry are saved in one transaction.
