"""The forge seam: everything above this line is forge-agnostic.

An adapter is a module exposing two functions:

    supports(root) -> bool
        Cheap and offline. Answers "is this checkout mine?" without spending a
        network call, so a repo hosted somewhere we don't know reads as *no
        forge* rather than *broken forge*.

    fetch(root) -> {"open": [PR], "merged": [PR]} | None
        None means COULD NOT ANSWER. It is not the same as an empty list, and
        the distinction is load-bearing: an empty list means "this repo has no
        open PRs" and clears tokens, while None means "ask again later" and
        preserves whatever was last known. Conflating them is what makes a
        sidebar collapse every time the network hiccups.

Records are normalised by the adapter, so glyph derivation never learns a
forge's vocabulary:

    open:   {number, branch, draft, checks, conflicting, labels}
              checks       list of conclusion strings, upper-cased; "" for a
                           check that has not concluded yet
              conflicting  bool
              labels       list of label-name strings
    merged: {number, branch}
"""
from . import github

ADAPTERS = (github,)


def adapter_for(root):
    """The first adapter that claims this checkout, or None if nothing does."""
    for adapter in ADAPTERS:
        if adapter.supports(root):
            return adapter
    return None
