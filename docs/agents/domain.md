# Domain Docs

How engineering skills consume repo domain documentation.

## Before exploring, read these

- `CONTEXT.md` at repo root, or
- `CONTEXT-MAP.md` at repo root if it exists: it points at one `CONTEXT.md` per context.
- `docs/adr/`: read ADRs that affect current work area.

If files do not exist, proceed silently. `/domain-modeling` creates them when terms or decisions need recording.

## File structure

Single-context repo:

```
/
├── CONTEXT.md
├── docs/adr/
└── src/
```

## Use glossary vocabulary

When output names a domain concept, use term defined in `CONTEXT.md`. Do not drift to synonyms.

## Flag ADR conflicts

Surface conflicts explicitly rather than silently overriding them.
