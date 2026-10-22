"""Repair passes that let a run fix its own draft instead of failing (P0, 2026-10-22).

Kept free of heavy imports so it can be tested without the research stack.
"""


def repair_after_verification(draft_fn, verify_fn, draft_result: dict, verification: dict, attention: list) -> tuple[dict, dict]:
    """One repair pass when the fact check finds unsupported claims (P0, 2026-10-22).

    The run used to print "Verified: False" and publish anyway, with nothing
    in the PR. Now the critical issues go back to the drafting agent once;
    whatever the second check still finds is listed under Needs attention.
    The run never fails for this.
    """
    def critical(v):
        return [i for i in (v or {}).get("issues", []) if i.get("severity") == "critical"]

    def describe(i):
        loc = f"[{i.get('location')}] " if i.get("location") else ""
        claim = (i.get("claim") or i.get("description") or "").strip()
        reason = (i.get("reason") or "").strip()
        return f"{loc}{claim}" + (f" ({reason})" if reason else "")

    issues = critical(verification)
    if not issues:
        return draft_result, verification
    print(f"  Repairing {len(issues)} unsupported claim(s) with one more drafting pass...")
    try:
        repaired = draft_fn([describe(i) for i in issues])
    except Exception as e:  # noqa: BLE001 - keep the first draft, flag the claims
        repaired = {"error": str(e)}
    if repaired and "error" not in repaired:
        try:
            new_v = verify_fn(repaired)
        except Exception as e:  # noqa: BLE001 - keep the first draft and its check
            new_v = None
            attention.append(f"The fact check could not re-run on the repaired draft ({type(e).__name__}: {e}); "
                             "the first draft was kept.")
        if new_v is not None and len(critical(new_v)) <= len(issues):
            draft_result, verification = repaired, new_v
    for i in critical(verification)[:8]:
        attention.append(f"Fact check: not supported by the source material: {describe(i)}")
    return draft_result, verification


UNVERIFIED = {"verified": False, "faithfulness_score": 0, "issues": [], "_unavailable": True}


def safe_verify(verify_call, attention: list) -> dict:
    """Run the fact check; if it cannot run (model outage, credits, bad JSON),
    publish anyway and say so under Needs attention instead of failing the run
    after the draft is done."""
    try:
        result = verify_call()
    except Exception as e:  # noqa: BLE001
        attention.append(f"The fact check could not run ({type(e).__name__}: {e}), so nothing in this draft was "
                         "checked against the source material. Review every claim before merging.")
        return dict(UNVERIFIED)
    if not isinstance(result, dict):
        attention.append("The fact check returned no usable result, so nothing in this draft was checked against "
                         "the source material. Review every claim before merging.")
        return dict(UNVERIFIED)
    return result
