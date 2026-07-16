# kfxai Agent Rules

- Follow `/home/kojima/work/AGENTS.md`, `WORKFLOW.md`, and `QUALITY_RULES.md`.
- Default to `paper` mode. Never enable live trading from code or committed configuration.
- Never commit OANDA account IDs, access tokens, wallet keys, or broker credentials.
- Treat an accepted order as transport success, not trading success. Record the broker response and verify resulting state.
- Keep deterministic risk limits outside the LLM judgment layer.

