"""The base for rule option types and the tables nested inside them."""

import msgspec


class RuleOptions(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    """A table read from a rule's config; an unrecognized key fails conversion.

    Subclasses inherit both settings. They repeat `frozen=True` only because Pyright doesn't
    inherit it through msgspec's dataclass transform, and reports a subclass that leaves it out.
    """


__all__ = ["RuleOptions"]
