## What and why

<!-- What changes, and what problem it solves. The diff already says what; explain why. -->

## Verification

<!-- How you know it works. "Tests pass" alone is not verification if the change
     touches the bridge or the generators. -->

- [ ] `python -m pytest` passes from the repo root
- [ ] New behaviour has a test; bug fixes have a test that **fails without the fix**
- [ ] Ran it against a real Packet Tracer session — or explicitly noted below what
      could not be verified offline

## Security checklist

Only relevant if you touched generators, paths or the bridge — delete if not.

- [ ] Generated JavaScript uses `json.dumps` per field, not raw f-strings
- [ ] New path components go through `safe_name_component()` + `resolve_within()`
- [ ] No new bridge endpoint skips the token check
- [ ] Fields reaching IOS CLI reject newlines (and `#` for banners)
