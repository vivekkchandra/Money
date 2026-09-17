"""Run with Railway-injected environment; no copied keys or handwritten manifest."""

from money.qualification.runner import main

if __name__ == "__main__":
    raise SystemExit(main())
