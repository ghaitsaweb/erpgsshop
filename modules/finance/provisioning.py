"""Extension registry for company accounting provisioning."""

from dataclasses import dataclass


@dataclass(frozen=True)
class AccountDefinition:
    code: str
    name: str
    account_type: str
    allow_reconciliation: bool = False


@dataclass(frozen=True)
class JournalDefinition:
    code: str
    name: str
    journal_type: str
    debit_account_code: str
    credit_account_code: str


_PROVISIONERS = []
_ACCOUNT_DEFINITIONS = {}
_JOURNAL_DEFINITIONS = {}


def register_company_accounting_provisioner(callback):
    if callback not in _PROVISIONERS:
        _PROVISIONERS.append(callback)


def register_accounting_blueprint(*, accounts=(), journals=()):
    for definition in accounts:
        previous = _ACCOUNT_DEFINITIONS.get(definition.code)
        if previous is not None and previous != definition:
            raise ValueError(f"Conflicting accounting account blueprint for code {definition.code}.")
        _ACCOUNT_DEFINITIONS[definition.code] = definition
    for definition in journals:
        previous = _JOURNAL_DEFINITIONS.get(definition.code)
        if previous is not None and previous != definition:
            raise ValueError(f"Conflicting accounting journal blueprint for code {definition.code}.")
        _JOURNAL_DEFINITIONS[definition.code] = definition


def accounting_blueprint():
    return tuple(_ACCOUNT_DEFINITIONS.values()), tuple(_JOURNAL_DEFINITIONS.values())


def run_company_accounting_provisioners(*, company, accounts, journals, actor=None):
    # Registry size is bounded by installed modules, not business record volume.
    return [
        callback(company=company, accounts=accounts, journals=journals, actor=actor)
        for callback in _PROVISIONERS
    ]
