## What and why

<!-- One or two sentences. Link the issue or decision record if there is one. -->

## Risk checklist

- [ ] Touches the guard, the egress proxy or the browser launch? Which test shows a page still cannot reach a private address (including through a redirect)?
- [ ] Changes a check or its wording? Re-ran `tracking-audit batch bench/sites.txt` and compared against `bench/hand_check.md`?
- [ ] Could text from an audited page reach the scorecard, the console or a log unescaped?
- [ ] Could it put a token or a secret into logs, errors, the repo or Terraform state?
- [ ] Docs updated: README results, runbook, security questionnaire, ADRs

## How it was tested

<!-- Commands run and what they showed. Say what ran against real sites and what used the fixture shop. -->
