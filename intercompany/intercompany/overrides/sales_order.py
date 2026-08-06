from intercompany.intercompany.services.posting_service import (
	cascade_cancel,
	process_ic_event,
)


def create_ic_transaction(doc, method=None):
	process_ic_event(doc, method)


def reverse_ic_transaction(doc, method=None):
	cascade_cancel(doc, method)
